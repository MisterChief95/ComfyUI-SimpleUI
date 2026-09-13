"""Backend smoke checks: config validation, health, errors, and SPA serving.

Run from backend/:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Config, ConfigError
from app.contracts import ControlDescriptor, ErrorEnvelope, ExactInt, Page
from app.main import create_app
from pydantic import BaseModel, ValidationError


class ConfigTest(unittest.TestCase):
    def test_rejects_bad_comfy_url_and_missing_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as ctx:
                Config.from_env(
                    {
                        "SIMPLEUI_COMFY_URL": "not-a-url",
                        "SIMPLEUI_DATA_DIR": tmp,
                        "SIMPLEUI_STATIC_DIR": str(Path(tmp) / "nope"),
                    }
                )
        message = str(ctx.exception)
        self.assertIn("SIMPLEUI_COMFY_URL", message)
        self.assertIn("No built UI", message)

    def test_dev_mode_allows_missing_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.from_env(
                {"SIMPLEUI_DATA_DIR": tmp, "SIMPLEUI_STATIC_DIR": tmp, "SIMPLEUI_DEV": "1"}
            )
        self.assertTrue(config.dev_mode)
        self.assertEqual(config.comfy_url, "http://127.0.0.1:8188")


class ContractTest(unittest.TestCase):
    def test_exact_int_survives_beyond_js_safe_range(self) -> None:
        class Holder(BaseModel):
            seed: ExactInt

        big = "12345678901234567890"
        self.assertEqual(Holder(seed=big).seed, big)
        with self.assertRaises(ValidationError):
            Holder(seed="1.5")

    def test_control_descriptor_and_page_round_trip(self) -> None:
        control = ControlDescriptor(
            binding_id="b1",
            node_id="3",
            class_type="KSampler",
            input_name="seed",
            logical_type="int",
            value="12345678901234567890",
            component="seed",
            group="generation",
            order=0,
            label="Seed",
            inference_reason="input name matched seed rule",
        )
        page = Page[ControlDescriptor](items=[control], next_cursor="c2")
        restored = Page[ControlDescriptor].model_validate(page.model_dump())
        self.assertEqual(restored.items[0].value, "12345678901234567890")
        self.assertEqual(restored.next_cursor, "c2")

    def test_unknown_request_fields_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Page[int].model_validate({"items": [1], "owner_id": "someone-else"})


class AppTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        build = root / "build"
        (build / "_app").mkdir(parents=True)
        (build / "index.html").write_text("<!doctype html>SPA shell", encoding="utf-8")
        (build / "_app" / "start.js").write_text("// bundle", encoding="utf-8")
        config = Config.from_env(
            {"SIMPLEUI_DATA_DIR": str(root / "data"), "SIMPLEUI_STATIC_DIR": str(build)}
        )
        app = create_app(config)
        self.client = TestClient(app, raise_server_exceptions=False)
        # LIFO: the database closes before the temporary directory is removed,
        # or Windows refuses to delete the still-open file.
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(app.state.db.close)

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["time_ms"].isdigit())
        self.assertTrue(response.headers["x-request-id"])

    def test_unknown_api_route_is_a_structured_404(self) -> None:
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        envelope = ErrorEnvelope.model_validate(response.json())
        self.assertEqual(envelope.error.code, "not_found")
        self.assertTrue(envelope.error.request_id)

    def test_index_and_asset_are_served(self) -> None:
        self.assertIn("SPA shell", self.client.get("/").text)
        self.assertEqual(self.client.get("/_app/start.js").status_code, 200)

    def test_deep_link_returns_the_shell(self) -> None:
        response = self.client.get("/gallery/42")
        self.assertEqual(response.status_code, 200)
        self.assertIn("SPA shell", response.text)

    def test_missing_asset_stays_a_404(self) -> None:
        response = self.client.get("/_app/missing.js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_traversal_is_refused(self) -> None:
        response = self.client.get("/../config.py")
        self.assertNotEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
