"""PACK-001: optional node pack detection (missing / present / outdated).

The pack is never required: every case here must leave the catalog itself
fresh and unchanged. Run from backend/:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from app.catalog import CatalogService
from app.catalog.pack import PACK_REPO_URL, SUPPORTED_CONTRACT
from app.comfy_client import ComfyClient

from .test_catalog import CatalogTestCase

#: Projection-relevant fields of the pack's classes, as its object_info lists them.
PACK_CLASSES: dict[str, Any] = {
    "SimpleUILoraStack": {
        "input": {
            "required": {"model": ["MODEL"], "clip": ["CLIP"]},
            "optional": {"loras": ["STRING", {"forceInput": True}]},
        },
        "output": ["MODEL", "CLIP", "STRING"],
        "output_node": False,
        "python_module": "custom_nodes.comfyui-simpleui-nodes",
        "category": "SimpleUI",
    },
    "SimpleUIChainOutput": {
        "input": {"required": {"image": ["IMAGE"], "name": ["STRING", {}]}},
        "output": ["IMAGE"],
        "output_node": False,
        "python_module": "custom_nodes.comfyui-simpleui-nodes",
        "category": "SimpleUI",
    },
}


class PackDetectionTest(CatalogTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.route: Any = None  # None = route absent (404)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/simpleui/pack" and self.online:
            self.requests.append(request.url.path)
            if self.route is None:
                return httpx.Response(404, json={"error": "no such route"})
            return httpx.Response(200, json=self.route)
        return super().handler(request)

    def install_pack(self) -> None:
        self.object_info.update(json.loads(json.dumps(PACK_CLASSES)))

    async def status(self, service: CatalogService | None = None) -> Any:
        service = service or self.make_service()
        await service.startup()
        return await service.pack_status()

    async def test_missing_without_classes_or_route(self) -> None:
        status = await self.status()
        self.assertEqual(status.state, "missing")
        self.assertIsNone(status.detected_by)
        self.assertEqual(status.node_classes, [])
        self.assertEqual(status.repo_url, PACK_REPO_URL)
        self.assertEqual(status.catalog_state, "fresh")

    async def test_present_by_route_with_the_supported_contract(self) -> None:
        self.install_pack()
        self.route = {
            "pack": "comfyui-simpleui-nodes",
            "version": "2.0.0",
            "contract": SUPPORTED_CONTRACT,
        }
        status = await self.status()
        self.assertEqual(status.state, "present")
        self.assertEqual(status.detected_by, "route")
        self.assertEqual(status.version, "2.0.0")
        self.assertEqual(status.contract, SUPPORTED_CONTRACT)
        self.assertEqual(
            status.node_classes, ["SimpleUIChainOutput", "SimpleUILoraStack"]
        )

    async def test_route_absent_falls_back_to_class_types(self) -> None:
        self.install_pack()
        status = await self.status()
        self.assertEqual(status.state, "present")
        self.assertEqual(status.detected_by, "class_types")
        self.assertIsNone(status.version)
        self.assertIsNone(status.contract)

    async def test_an_unsupported_contract_is_outdated(self) -> None:
        self.install_pack()
        for contract in (SUPPORTED_CONTRACT + 1, 1, 0, "2", None):
            with self.subTest(contract=contract):
                self.route = {
                    "pack": "comfyui-simpleui-nodes",
                    "version": "9.0.0",
                    "contract": contract,
                }
                status = await self.status()
                self.assertEqual(status.state, "outdated")
                self.assertEqual(status.detected_by, "route")

    async def test_a_foreign_reply_on_the_route_is_ignored(self) -> None:
        self.route = {"pack": "something-else", "version": "9", "contract": 1}
        status = await self.status()
        self.assertEqual(status.state, "missing")

    async def test_a_failing_probe_does_not_fail_the_refresh(self) -> None:
        self.install_pack()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/simpleui/pack":
                return httpx.Response(500, json={"error": "C:\\secret\\path"})
            return CatalogTestCase.handler(self, request)

        client = ComfyClient(
            "http://127.0.0.1:8188",
            transport=httpx.MockTransport(handler),
            attempts=1,
            backoff_s=0,
        )
        self._clients.append(client)
        service = CatalogService(self.store, client)
        snapshot = await service.startup()
        status = await service.pack_status()

        self.assertEqual(snapshot.freshness.state, "fresh")
        self.assertEqual(status.state, "present")
        self.assertEqual(status.detected_by, "class_types")
        self.assertNotIn("secret", status.model_dump_json())

    async def test_only_named_route_fields_are_kept(self) -> None:
        self.install_pack()
        self.route = {
            "pack": "comfyui-simpleui-nodes",
            "version": "2.0.0",
            "contract": SUPPORTED_CONTRACT,
            "path": "C:\\Users\\someone\\ComfyUI\\custom_nodes",
        }
        status = await self.status()
        self.assertNotIn("someone", status.model_dump_json())
        self.assertNotIn("someone", json.dumps(self.store.load()))

    async def test_status_survives_a_restart_without_comfyui(self) -> None:
        self.install_pack()
        self.route = {
            "pack": "comfyui-simpleui-nodes",
            "version": "2.0.0",
            "contract": SUPPORTED_CONTRACT,
        }
        await self.status()

        self.online = False
        self.reopen()
        before = len(self.requests)
        service = self.make_service()
        status = await service.pack_status()  # cache only, no upstream call

        self.assertEqual(len(self.requests), before)
        self.assertEqual(status.state, "present")
        self.assertEqual(status.version, "2.0.0")

    async def test_no_catalog_reports_missing_with_the_catalog_state(self) -> None:
        self.online = False
        status = await self.status()
        self.assertEqual(status.state, "missing")
        self.assertEqual(status.catalog_state, "unavailable")
