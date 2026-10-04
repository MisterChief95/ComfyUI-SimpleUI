import unittest

import httpx
from app.catalog import CatalogService
from app.comfy_client import ComfyClient, ComfyUnavailable

from tests.auth.support import AuthTestCase


class VramServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_used_is_total_minus_free_and_free_posts_both_flags(self) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.content))
            if request.url.path == "/system_stats":
                return httpx.Response(
                    200, json={"devices": [{"vram_total": 100, "vram_free": 40}]}
                )
            return httpx.Response(200)

        client = ComfyClient("http://x", transport=httpx.MockTransport(handler))
        service = CatalogService.__new__(CatalogService)
        service._client = client
        self.assertEqual(await service.vram(), {"used_bytes": 60, "total_bytes": 100})
        await service.free_memory()
        self.assertEqual(seen[-1][:2], ("POST", "/free"))
        self.assertIn(b'"unload_models":true', seen[-1][2].replace(b" ", b""))
        await client.aclose()

    async def test_missing_device_fields_are_unknown(self) -> None:
        client = ComfyClient(
            "http://x",
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"devices": []})
            ),
        )
        service = CatalogService.__new__(CatalogService)
        service._client = client
        self.assertEqual(
            await service.vram(), {"used_bytes": None, "total_bytes": None}
        )
        await client.aclose()


class VramRouteTest(AuthTestCase):
    def test_free_is_a_gated_mutation_and_unavailable_is_actionable(self) -> None:
        from app.comfy_client import ComfyUnavailable

        async def down():
            raise ComfyUnavailable("upstream_unreachable", "ComfyUI is not reachable")

        self.app.state.catalog.free_memory = down
        client = self.local_client()
        response = self.post(client, "/api/catalog/free")
        self.assertEqual(response.status_code, 503)
        self.assertIn("not reachable", response.text)


class CompletionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_embeddings_and_lora_names_only(self) -> None:
        client = ComfyClient(
            "http://x",
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=["b", "a", 3])
            ),
        )
        service = CatalogService.__new__(CatalogService)
        service._client = client
        service._cache = {
            "normalized": {
                "nodes": {
                    "LoraLoader": {
                        "inputs": {
                            "lora_name": {"choices": ["z.safetensors", "y.safetensors"]}
                        }
                    }
                }
            }
        }
        self.assertEqual(
            await service.completions(),
            {"embeddings": ["a", "b"], "loras": ["y.safetensors", "z.safetensors"]},
        )
        await client.aclose()

    async def test_unreachable_comfyui_gives_empty_embeddings(self) -> None:
        def down(request):
            raise httpx.ConnectError("x")

        client = ComfyClient(
            "http://x", attempts=1, transport=httpx.MockTransport(down)
        )
        service = CatalogService.__new__(CatalogService)
        service._client = client
        service._cache = None
        self.assertEqual(await service.completions(), {"embeddings": [], "loras": []})
        await client.aclose()


class ModelBrowserTest(unittest.IsolatedAsyncioTestCase):
    def service(self, handler):
        client = ComfyClient(
            "http://x", attempts=1, transport=httpx.MockTransport(handler)
        )
        service = CatalogService.__new__(CatalogService)
        service._client = client
        service._previews = {}
        service._cache = {
            "normalized": {
                "nodes": {
                    "LoraLoader": {
                        "inputs": {
                            "lora_name": {
                                "choices_source": "server_model_catalog",
                                "choices": ["sub/a.safetensors"],
                            }
                        }
                    }
                }
            }
        }
        return service, client

    async def test_trigger_words_come_from_tag_frequency(self) -> None:
        meta = {
            "ss_tag_frequency": '{"d1": {"cat": 5, "dog": 2}, "d2": {"cat": 1, "bird": 9}}',
            "ss_base_model_version": "sdxl_base_v1-0",
        }
        service, client = self.service(lambda r: httpx.Response(200, json=meta))
        info = await service.model_info("loras", "sub/a.safetensors")
        self.assertEqual(
            info,
            {"base_model": "sdxl_base_v1-0", "trigger_words": ["bird", "cat", "dog"]},
        )
        await client.aclose()

    async def test_only_catalog_names_are_forwarded_and_missing_routes_degrade(
        self,
    ) -> None:
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(404)

        service, client = self.service(handler)
        with self.assertRaises(KeyError):
            await service.model_info("loras", "../../etc/passwd")
        with self.assertRaises(KeyError):
            await service.model_preview("lo/ras", "sub/a.safetensors")
        self.assertEqual(seen, [])
        self.assertEqual(
            await service.model_info("loras", "sub/a.safetensors"),
            {"base_model": None, "trigger_words": []},
        )
        with self.assertRaises(ComfyUnavailable):
            await service.model_preview("loras", "sub/a.safetensors")
        await client.aclose()

    async def test_preview_is_proxied_and_cached(self) -> None:
        calls = []

        def handler(request):
            calls.append(str(request.url.path))
            return httpx.Response(
                200, content=b"img", headers={"content-type": "image/webp"}
            )

        service, client = self.service(handler)
        for _ in range(2):
            self.assertEqual(
                await service.model_preview("loras", "sub/a.safetensors"),
                (b"img", "image/webp"),
            )
        self.assertEqual(
            calls, ["/experiment/models/preview/loras/0/sub/a.safetensors"]
        )
        await client.aclose()
