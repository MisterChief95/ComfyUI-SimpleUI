"""Catalog checks, one class per CATALOG-001 acceptance criterion.

Run from backend/:  python -m unittest discover -s tests -v

There is no live ComfyUI on this host (see docs/COMPATIBILITY.md), so the HTTP
layer is mocked with httpx.MockTransport and every payload comes from the
COMPAT-001 fixture set in tests/fixtures/, which is read-only here.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from app.catalog import CatalogService, CatalogStore, evaluate_selections, normalize
from app.catalog.normalize import NormalizeError
from app.comfy_client import ComfyClient, ComfyUnavailable, redact_url
from app.storage import Database

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "catalog"
OBJECT_INFO = json.loads((FIXTURES / "object_info.synthetic.json").read_text(encoding="utf-8"))
LEGACY_INFO = json.loads((FIXTURES / "object_info.legacy-shape.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((FIXTURES / "sanitized_projection.expected.json").read_text(encoding="utf-8"))

#: The shared ComfyUI input-directory filenames that must never reach a client.
WITHHELD_NAMES = EXPECTED["must_not_appear_in_projection"]

SYSTEM_STATS = {
    "system": {
        "comfyui_version": "0.3.99",
        "python_version": "3.13.1 (main, Dec 2024) [MSC v.1942 64 bit] C:\\build\\python",
        "pytorch_version": "2.5.1+cu124",
    },
    "devices": [{"name": "synthetic-device", "type": "cuda", "index": 0}],
}


def strip_comments(value: Any) -> Any:
    """Fixture keys beginning with an underscore are commentary, not contract."""
    if isinstance(value, dict):
        return {k: strip_comments(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [strip_comments(v) for v in value]
    return value


class CatalogTestCase(unittest.IsolatedAsyncioTestCase):
    """A fresh database plus a scripted ComfyUI."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "app.sqlite3"
        self.db = Database(self.db_path)
        self.store = CatalogStore(self.db)
        self.requests: list[str] = []
        self.object_info: Any = copy.deepcopy(OBJECT_INFO)
        self.online = True
        self._clients: list[ComfyClient] = []

    async def asyncTearDown(self) -> None:
        for client in self._clients:
            await client.aclose()
        self.db.close()
        self._tmp.cleanup()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if not self.online:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/object_info":
            return httpx.Response(200, json=self.object_info)
        if request.url.path == "/system_stats":
            return httpx.Response(200, json=SYSTEM_STATS)
        return httpx.Response(404, json={"error": "no such route"})

    def make_client(self, url: str = "http://127.0.0.1:8188", **kwargs: Any) -> ComfyClient:
        kwargs.setdefault("attempts", 1)
        kwargs.setdefault("backoff_s", 0)
        client = ComfyClient(url, transport=httpx.MockTransport(self.handler), **kwargs)
        self._clients.append(client)
        return client

    def make_service(self, **kwargs: Any) -> CatalogService:
        return CatalogService(self.store, self.make_client(), **kwargs)

    def count(self, path: str) -> int:
        return self.requests.count(path)

    def reopen(self) -> CatalogStore:
        """Simulate a process restart against the same database file."""
        self.db.close()
        self.db = Database(self.db_path)
        self.store = CatalogStore(self.db)
        return self.store


# --- normalization and sanitization ---------------------------------------


class SanitizationTest(CatalogTestCase):
    async def test_projection_matches_the_compat_fixture(self) -> None:
        nodes = normalize(OBJECT_INFO).nodes
        expected = strip_comments(EXPECTED["nodes"])

        for class_type, expected_node in expected.items():
            self.assertIn(class_type, nodes, class_type)
            actual = nodes[class_type]
            for key, value in expected_node.items():
                if key == "inputs":
                    for name, spec in value.items():
                        self.assertIn(name, actual["inputs"], f"{class_type}.{name}")
                        for field, expected_value in spec.items():
                            self.assertEqual(
                                actual["inputs"][name].get(field),
                                expected_value,
                                f"{class_type}.{name}.{field}",
                            )
                else:
                    self.assertEqual(actual.get(key), value, f"{class_type}.{key}")

    async def test_shared_filenames_never_reach_a_profile(self) -> None:
        service = self.make_service()
        snapshot = await service.startup()

        payload = json.dumps(snapshot.model_dump())
        for name in WITHHELD_NAMES:
            self.assertNotIn(name, payload, f"{name} leaked into the client projection")

        loader = snapshot.nodes["LoadImage"]["inputs"]["image"]
        self.assertEqual(loader["logical_type"], "OWNED_INPUT_REF")
        self.assertTrue(loader["choices_withheld"])
        self.assertEqual(loader["choices"], [])

        # Withheld server-side, not discarded: staging needs them at submission.
        raw = await service.raw_catalog()
        self.assertIn(WITHHELD_NAMES[0], json.dumps(raw))

    async def test_sanitized_after_refresh_too_not_only_startup(self) -> None:
        service = self.make_service()
        await service.startup()
        # A later refresh adds another shared filename upstream.
        self.object_info["LoadImage"]["input"]["required"]["image"][0].append(
            "someone-elses-upload.png"
        )
        snapshot = await service.refresh(force=True)

        payload = json.dumps(snapshot.model_dump())
        self.assertNotIn("someone-elses-upload.png", payload)
        self.assertEqual(snapshot.nodes["LoadImage"]["inputs"]["image"]["choices"], [])

    async def test_raw_catalog_is_not_part_of_the_snapshot(self) -> None:
        snapshot = await self.make_service().startup()
        dumped = snapshot.model_dump()
        self.assertNotIn("withheld", dumped)
        self.assertNotIn("raw", dumped)
        # Node metadata is projected, never the upstream document itself.
        self.assertNotIn("python_module", json.dumps(dumped))

    async def test_an_absolute_path_choice_is_withheld(self) -> None:
        self.object_info["OpaqueConfigNode"]["input"]["required"]["config"] = [
            ["C:\\ComfyUI\\models\\internal.cfg", "/opt/comfy/other.cfg"],
            {},
        ]
        snapshot = await self.make_service().startup()
        projected = snapshot.nodes["OpaqueConfigNode"]["inputs"]["config"]

        self.assertEqual(projected["logical_type"], "UNSUPPORTED")
        self.assertTrue(projected["choices_withheld"])
        self.assertNotIn("ComfyUI\\models", json.dumps(snapshot.model_dump()))
        self.assertNotIn("/opt/comfy", json.dumps(snapshot.model_dump()))

    async def test_both_metadata_shapes_normalize(self) -> None:
        legacy = normalize(LEGACY_INFO)
        # Combo with no trailing options object, and no python_module/input_order.
        self.assertEqual(
            legacy.nodes["KSampler"]["inputs"]["sampler_name"]["logical_type"], "COMBO"
        )
        self.assertEqual(legacy.nodes["CLIPTextEncode"]["inputs"]["clip"]["socket_type"], "CLIP")
        self.assertEqual(
            legacy.nodes["LoadImage"]["inputs"]["image"]["logical_type"], "OWNED_INPUT_REF"
        )
        # A legacy payload withholds its shared filenames just the same.
        self.assertEqual(legacy.nodes["LoadImage"]["inputs"]["image"]["choices"], [])

    async def test_exact_integers_stay_decimal_strings(self) -> None:
        seed = normalize(OBJECT_INFO).nodes["KSampler"]["inputs"]["seed"]
        self.assertEqual(seed["logical_type"], "INT_EXACT")
        self.assertEqual(seed["max"], "18446744073709551615")
        self.assertFalse(seed["slider_allowed"])

    async def test_hidden_inputs_are_never_projected(self) -> None:
        save_image = normalize(OBJECT_INFO).nodes["SaveImage"]["inputs"]
        self.assertNotIn("prompt", save_image)
        self.assertNotIn("extra_pnginfo", save_image)

    async def test_malformed_payloads_are_rejected(self) -> None:
        for payload in ([], {}, "nodes", {"_comment": "only commentary"}):
            with self.assertRaises(NormalizeError):
                normalize(payload)


# --- coalescing and cooldown ----------------------------------------------


class CoalescingTest(CatalogTestCase):
    async def test_simultaneous_refreshes_make_one_upstream_fetch(self) -> None:
        gate = asyncio.Event()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request.url.path)
            if request.url.path == "/object_info":
                await gate.wait()
                return httpx.Response(200, json=self.object_info)
            return httpx.Response(200, json=SYSTEM_STATS)

        client = ComfyClient(
            "http://127.0.0.1:8188", transport=httpx.MockTransport(slow_handler), attempts=1
        )
        self._clients.append(client)
        service = CatalogService(self.store, client)

        async def release() -> None:
            await asyncio.sleep(0.05)  # let all callers pile up on the same fetch
            gate.set()

        results = await asyncio.gather(
            release(), *[service.refresh(force=True) for _ in range(6)]
        )

        self.assertEqual(self.count("/object_info"), 1, "concurrent refreshes fetched more than once")
        for snapshot in results[1:]:
            self.assertEqual(snapshot.freshness.state, "fresh")
            self.assertTrue(snapshot.nodes)

    async def test_cooldown_is_enforced_by_the_server(self) -> None:
        service = self.make_service(cooldown_s=60)
        await service.refresh(force=True)
        self.assertEqual(self.count("/object_info"), 1)

        # A page reload asking again must not reach ComfyUI.
        for _ in range(3):
            snapshot = await service.refresh()
        self.assertEqual(self.count("/object_info"), 1, "the cooldown was bypassed")
        self.assertTrue(snapshot.freshness.cooldown_active)
        self.assertIsNotNone(snapshot.freshness.next_refresh_allowed_ms)
        self.assertEqual(snapshot.freshness.state, "fresh")

    async def test_failures_back_off_to_a_ceiling(self) -> None:
        service = self.make_service(cooldown_s=1, max_backoff_s=4)
        self.online = False

        delays = []
        for _ in range(4):
            snapshot = await service.refresh(force=True)
            delays.append(
                int(snapshot.freshness.next_refresh_allowed_ms) - int(snapshot.freshness.checked_ms)
            )

        # checked_ms and next_refresh_allowed_ms are sampled a moment apart, so
        # allow a few ms of drift in the derived delay.
        tolerance_ms = 50
        self.assertEqual(snapshot.freshness.consecutive_failures, 4)
        self.assertLess(delays[0], delays[1])
        self.assertLess(delays[1], delays[2])
        self.assertLessEqual(delays[-1], 4000 + tolerance_ms, "backoff exceeded its ceiling")
        self.assertGreater(delays[-1], 4000 - tolerance_ms, "backoff did not reach the ceiling")

    async def test_a_caller_giving_up_does_not_cancel_the_shared_fetch(self) -> None:
        gate = asyncio.Event()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request.url.path)
            if request.url.path == "/object_info":
                await gate.wait()
                return httpx.Response(200, json=self.object_info)
            return httpx.Response(200, json=SYSTEM_STATS)

        client = ComfyClient(
            "http://127.0.0.1:8188", transport=httpx.MockTransport(slow_handler), attempts=1
        )
        self._clients.append(client)
        service = CatalogService(self.store, client)

        first = asyncio.create_task(service.refresh(force=True))
        await asyncio.sleep(0.02)
        second = asyncio.create_task(service.refresh(force=True))
        await asyncio.sleep(0.02)
        first.cancel()  # client disconnected
        gate.set()

        snapshot = await second
        self.assertEqual(snapshot.freshness.state, "fresh")
        self.assertTrue(snapshot.nodes)


# --- cached startup and failure retention ---------------------------------


class CacheRetentionTest(CatalogTestCase):
    async def test_a_failed_refresh_keeps_the_last_good_catalog(self) -> None:
        service = self.make_service()
        good = await service.startup()
        self.assertEqual(good.freshness.state, "fresh")
        revision = good.freshness.catalog_revision

        self.online = False
        stale = await service.refresh(force=True)

        self.assertEqual(stale.freshness.state, "stale")
        self.assertEqual(stale.nodes, good.nodes, "a transient failure blanked the catalog")
        self.assertEqual(stale.freshness.catalog_revision, revision)
        self.assertTrue(stale.freshness.submission_allowed)
        self.assertIsNotNone(stale.freshness.error)
        self.assertEqual(stale.freshness.error.code, "upstream_unreachable")
        # The stored snapshot kept its data too.
        self.assertIsNotNone(self.store.load()["normalized_json"])

    async def test_a_malformed_payload_does_not_replace_a_good_catalog(self) -> None:
        service = self.make_service()
        good = await service.startup()

        self.object_info = {"_comment": "upstream returned nothing usable"}
        snapshot = await service.refresh(force=True)

        self.assertEqual(snapshot.freshness.state, "stale")
        self.assertEqual(snapshot.nodes, good.nodes)
        self.assertEqual(snapshot.freshness.error.code, "upstream_malformed")

    async def test_cold_start_serves_the_cache_without_comfyui(self) -> None:
        await self.make_service().startup()

        self.reopen()  # process restart
        self.online = False
        restarted = CatalogService(self.store, self.make_client())

        # Before any refresh, the cached catalog is already available.
        cached = await restarted.snapshot()
        self.assertTrue(cached.nodes)
        self.assertEqual(self.count("/object_info"), 1, "startup cache read hit the network")

        after = await restarted.startup()
        self.assertEqual(after.freshness.state, "stale")
        self.assertTrue(after.nodes, "an offline startup blanked the cached catalog")
        self.assertTrue(after.freshness.submission_allowed)

    async def test_recovery_after_a_failure_returns_to_fresh(self) -> None:
        service = self.make_service()
        self.online = False
        await service.startup()
        self.online = True

        recovered = await service.refresh(force=True)
        self.assertEqual(recovered.freshness.state, "fresh")
        self.assertEqual(recovered.freshness.consecutive_failures, 0)
        self.assertIsNone(recovered.freshness.error)


class FirstRunOfflineTest(CatalogTestCase):
    async def test_no_cache_and_no_comfyui_is_an_explicit_state(self) -> None:
        self.online = False
        snapshot = await self.make_service().startup()

        self.assertEqual(snapshot.freshness.state, "unavailable")
        self.assertEqual(snapshot.nodes, {}, "expected an explicitly empty catalog")
        self.assertFalse(
            snapshot.freshness.submission_allowed,
            "submission must be refused while no catalog exists",
        )
        self.assertIsNotNone(snapshot.freshness.error, "an empty catalog was reported silently")
        self.assertIn("ComfyUI", snapshot.freshness.error.message)
        self.assertIsNone(snapshot.freshness.fetched_ms)
        self.assertIsNotNone(snapshot.freshness.checked_ms)

    async def test_the_unavailable_state_survives_a_restart(self) -> None:
        self.online = False
        await self.make_service().startup()

        self.reopen()
        snapshot = await CatalogService(self.store, self.make_client()).snapshot()

        self.assertEqual(snapshot.freshness.state, "unavailable")
        self.assertFalse(snapshot.freshness.submission_allowed)
        self.assertIsNotNone(snapshot.freshness.error)


# --- change detection and safe invalidation --------------------------------


class SchemaHashTest(CatalogTestCase):
    async def test_new_models_change_content_but_not_the_schema_hash(self) -> None:
        before = normalize(OBJECT_INFO)
        changed = copy.deepcopy(OBJECT_INFO)
        changed["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
            "placeholder-checkpoint-c.safetensors"
        )
        after = normalize(changed)

        self.assertNotEqual(before.content_hash, after.content_hash)
        self.assertEqual(
            before.schema_hash, after.schema_hash, "a new model read as a structural change"
        )

    async def test_a_type_change_changes_the_schema_hash(self) -> None:
        changed = copy.deepcopy(OBJECT_INFO)
        changed["KSampler"]["input"]["required"]["steps"][0] = "FLOAT"
        self.assertNotEqual(normalize(OBJECT_INFO).schema_hash, normalize(changed).schema_hash)

    async def test_the_revision_is_reported_and_changes_with_content(self) -> None:
        service = self.make_service()
        first = await service.startup()
        self.object_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].append(
            "placeholder-checkpoint-c.safetensors"
        )
        second = await service.refresh(force=True)

        self.assertNotEqual(first.freshness.catalog_revision, second.freshness.catalog_revision)
        self.assertEqual(first.freshness.schema_hash, second.freshness.schema_hash)


class SelectionInvalidationTest(CatalogTestCase):
    SAVED = [
        {
            "node_id": "4",
            "class_type": "CheckpointLoaderSimple",
            "input_name": "ckpt_name",
            "value": "placeholder-checkpoint-b.safetensors",
        },
        {"node_id": "3", "class_type": "KSampler", "input_name": "steps", "value": 20},
    ]

    async def test_a_removed_model_is_flagged_without_erasing_the_value(self) -> None:
        changed = copy.deepcopy(OBJECT_INFO)
        changed["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] = [
            "placeholder-checkpoint-a.safetensors"
        ]
        issues = evaluate_selections(normalize(changed).nodes, self.SAVED)

        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["code"], "choice_missing")
        self.assertEqual(issue["severity"], "blocking")
        # The missing value is shown, not replaced by the first option.
        self.assertEqual(issue["value"], "placeholder-checkpoint-b.safetensors")
        self.assertEqual(issue["node_id"], "4")

    async def test_an_omitted_class_marks_controls_uncertain_without_erasing_them(self) -> None:
        changed = copy.deepcopy(OBJECT_INFO)
        del changed["KSampler"]  # a custom node pack failed to load this time
        issues = evaluate_selections(normalize(changed).nodes, self.SAVED)

        self.assertEqual([i["code"] for i in issues], ["class_missing"])
        self.assertEqual(issues[0]["severity"], "uncertain")
        self.assertEqual(issues[0]["value"], 20, "the saved value was not preserved")
        self.assertEqual(issues[0]["class_type"], "KSampler")

    async def test_an_unchanged_catalog_produces_no_issues(self) -> None:
        self.assertEqual(evaluate_selections(normalize(OBJECT_INFO).nodes, self.SAVED), [])

    async def test_a_literal_that_became_a_socket_is_flagged_not_dropped(self) -> None:
        changed = copy.deepcopy(OBJECT_INFO)
        changed["KSampler"]["input"]["required"]["steps"][0] = "SIGMAS"
        issues = evaluate_selections(normalize(changed).nodes, self.SAVED)

        self.assertEqual([i["code"] for i in issues], ["type_changed"])
        self.assertEqual(issues[0]["severity"], "uncertain")
        self.assertEqual(issues[0]["value"], 20)

    async def test_dynamic_inputs_are_not_reported_missing(self) -> None:
        saved = [
            {
                "node_id": "9",
                "class_type": "AnySwitchWildcard",
                "input_name": "input_2",  # not enumerated by object_info
                "value": "x",
            }
        ]
        self.assertEqual(evaluate_selections(normalize(OBJECT_INFO).nodes, saved), [])

    async def test_selections_survive_a_real_refresh_cycle(self) -> None:
        service = self.make_service()
        await service.startup()
        self.object_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0] = [
            "placeholder-checkpoint-a.safetensors"
        ]
        snapshot = await service.refresh(force=True)

        issues = evaluate_selections(snapshot.nodes, self.SAVED)
        self.assertEqual([i["code"] for i in issues], ["choice_missing"])
        self.assertEqual(issues[0]["value"], "placeholder-checkpoint-b.safetensors")


# --- capability record -----------------------------------------------------


class CapabilityRecordTest(CatalogTestCase):
    async def test_installation_block_is_filled_from_system_stats(self) -> None:
        snapshot = await self.make_service().startup()
        installation = snapshot.capabilities["installation"]

        self.assertEqual(snapshot.capabilities["provenance"], "live")
        self.assertEqual(installation["comfyui_version"], "0.3.99")
        self.assertEqual(installation["torch_version"], "2.5.1+cu124")
        self.assertEqual(installation["device"]["name"], "synthetic-device")
        self.assertEqual(installation["node_packs"], ["simpleui_synthetic_pack"])

    async def test_version_strings_do_not_carry_build_paths(self) -> None:
        snapshot = await self.make_service().startup()
        self.assertEqual(snapshot.capabilities["installation"]["python_version"], "3.13.1")
        self.assertNotIn("C:\\build", json.dumps(snapshot.model_dump()))

    async def test_a_failing_system_stats_does_not_fail_the_refresh(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request.url.path)
            if request.url.path == "/object_info":
                return httpx.Response(200, json=self.object_info)
            return httpx.Response(500, json={"error": "boom"})

        client = ComfyClient(
            "http://127.0.0.1:8188",
            transport=httpx.MockTransport(handler),
            attempts=1,
            backoff_s=0,
        )
        self._clients.append(client)
        snapshot = await CatalogService(self.store, client).startup()

        self.assertEqual(snapshot.freshness.state, "fresh")
        self.assertTrue(snapshot.nodes)
        self.assertEqual(snapshot.capabilities["routes"]["system_stats"], "unavailable")


# --- credential handling ---------------------------------------------------


class CredentialLeakTest(CatalogTestCase):
    SECRET_PASSWORD = "sup3rs3cr3tpassword"
    SECRET_TOKEN = "Bearer TOPSECRETTOKENVALUE"

    def credentialed_client(self) -> ComfyClient:
        client = ComfyClient(
            f"http://operator:{self.SECRET_PASSWORD}@127.0.0.1:8188",
            transport=httpx.MockTransport(self.handler),
            headers={"Authorization": self.SECRET_TOKEN},
            attempts=1,
            backoff_s=0,
        )
        self._clients.append(client)
        return client

    def assert_clean(self, text: str, where: str) -> None:
        self.assertNotIn(self.SECRET_PASSWORD, text, f"password leaked into {where}")
        self.assertNotIn("TOPSECRETTOKENVALUE", text, f"auth header leaked into {where}")

    async def test_redact_url_strips_userinfo(self) -> None:
        self.assertEqual(
            redact_url("http://user:pass@127.0.0.1:8188/object_info"),
            "http://127.0.0.1:8188/object_info",
        )
        self.assertEqual(redact_url("http://127.0.0.1:8188"), "http://127.0.0.1:8188")

    async def test_no_credentials_in_responses_or_storage_on_failure(self) -> None:
        self.online = False
        service = CatalogService(self.store, self.credentialed_client())
        snapshot = await service.startup()

        self.assertEqual(snapshot.freshness.state, "unavailable")
        self.assert_clean(json.dumps(snapshot.model_dump()), "the API snapshot")

        stored = self.store.load()
        self.assert_clean(json.dumps({k: str(v) for k, v in stored.items()}), "the catalogs row")

    async def test_no_credentials_in_logs(self) -> None:
        self.online = False
        service = CatalogService(self.store, self.credentialed_client())

        with self.assertLogs("simpleui", level=logging.DEBUG) as captured:
            await service.startup()
        self.assert_clean("\n".join(captured.output), "the logs")
        self.assertTrue(captured.output, "the failure was not logged at all")

    async def test_no_credentials_after_a_successful_refresh(self) -> None:
        service = CatalogService(self.store, self.credentialed_client())
        snapshot = await service.startup()

        self.assertEqual(snapshot.freshness.state, "fresh")
        self.assert_clean(json.dumps(snapshot.model_dump()), "the API snapshot")
        stored = self.store.load()
        self.assert_clean(json.dumps({k: str(v) for k, v in stored.items()}), "the catalogs row")

    async def test_the_client_exception_message_is_sanitized(self) -> None:
        self.online = False
        client = self.credentialed_client()
        with self.assertRaises(ComfyUnavailable) as ctx:
            await client.get_json("/object_info")
        self.assert_clean(str(ctx.exception), "the exception message")
        self.assert_clean(ctx.exception.message, "the sanitized detail")


# --- client behaviour ------------------------------------------------------


class ComfyClientTest(CatalogTestCase):
    async def test_transient_failures_are_retried_then_reported(self) -> None:
        attempts = {"n": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(503, json={"error": "starting up"})
            return httpx.Response(200, json={"ok": True})

        client = ComfyClient(
            "http://127.0.0.1:8188",
            transport=httpx.MockTransport(flaky),
            attempts=3,
            backoff_s=0,
        )
        self._clients.append(client)
        with self.assertLogs("simpleui.comfy", level=logging.WARNING):
            self.assertEqual(await client.get_json("/object_info"), {"ok": True})
        self.assertEqual(attempts["n"], 3)

    async def test_a_client_error_is_not_retried(self) -> None:
        attempts = {"n": 0}

        def refuse(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(404, json={"error": "no such route"})

        client = ComfyClient(
            "http://127.0.0.1:8188", transport=httpx.MockTransport(refuse), attempts=3, backoff_s=0
        )
        self._clients.append(client)
        with self.assertRaises(ComfyUnavailable):
            await client.get_json("/object_info")
        self.assertEqual(attempts["n"], 1, "a 4xx contract error was retried")

    async def test_a_non_json_body_is_not_echoed(self) -> None:
        def html(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>C:\\ComfyUI\\secret\\path</html>")

        client = ComfyClient(
            "http://127.0.0.1:8188", transport=httpx.MockTransport(html), attempts=1
        )
        self._clients.append(client)
        with self.assertRaises(ComfyUnavailable) as ctx:
            await client.get_json("/object_info")
        self.assertEqual(ctx.exception.code, "upstream_malformed")
        self.assertNotIn("secret", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()


class ComboForms(unittest.TestCase):
    def test_newer_form_legacy_fallback_and_type_inference(self) -> None:
        from app.catalog.normalize import _combo_choices, _project_input

        def normalize_project(spec: Any, opts: dict[str, Any]) -> Any:
            return _project_input("X", "i", [spec, opts], {}, False)

        self.assertEqual(_combo_choices("COMBO", {"options": ["a", "b"]}), ["a", "b"])
        self.assertEqual(_combo_choices(["x", "y"], {}), ["x", "y"])
        self.assertEqual(_combo_choices("COMBO", {"options": []}), [])
        from app.catalog.normalize import same_choice

        self.assertTrue(same_choice(1, 1.0))
        self.assertFalse(same_choice(True, 1))
        self.assertFalse(same_choice("1", 1))
        projected, _ = normalize_project("COMBO", {"options": [8, 10.5, "x", True, None, [1]], "default": 8})
        self.assertEqual(projected["choices"], [8, 10.5, "x", True])
        self.assertEqual(projected["default"], 8)

    def test_remote_route_lists_are_filled_in_place(self) -> None:
        class Stub:
            async def get_json(self, path: str) -> Any:
                if path == "/internal/list":
                    return {"files": ["a.safetensors", "b.safetensors"]}
                raise ComfyUnavailable("upstream_unreachable", "down")

        def combo(route: str) -> dict[str, Any]:
            return {"input": {"required": {"m": ["COMBO", {"remote": {"route": route, "response_key": "files"}}]}}}

        raw = {"A": combo("/internal/list"), "B": combo("/gone"), "C": combo("//evil.example/x")}
        service = CatalogService.__new__(CatalogService)
        service._client = Stub()  # type: ignore[attr-defined]
        asyncio.run(service._fill_remote_combos(raw))
        self.assertEqual(raw["A"]["input"]["required"]["m"][1]["options"], ["a.safetensors", "b.safetensors"])
        self.assertNotIn("options", raw["B"]["input"]["required"]["m"][1])
        self.assertNotIn("options", raw["C"]["input"]["required"]["m"][1])
