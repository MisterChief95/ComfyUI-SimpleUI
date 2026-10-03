"""INTEGRATE-001: the assembled app, through create_app and real HTTP requests.

Every other suite exercises one package. This one only asserts that main.py
actually wires them together: the catalog, workflow and media routers are
reachable ahead of the /api catch-all, their guards are the shared auth
guards, and the lifespan starts and stops cleanly with ComfyUI unavailable.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.auth.security import CSRF_HEADER
from app.config import Config
from app.main import create_app
from fastapi.testclient import TestClient

from tests.auth.support import LOCAL_ORIGIN, AuthTestCase

PASSWORD = "default-password"

#: Smallest thing the importer accepts, with a seed beyond 2**53 so the
#: exact-integer rule is exercised end to end rather than in a unit test.
GRAPH = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 12345678901234567890, "steps": 20, "model": ["4", 0]},
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd.safetensors"},
    },
}


class RoutingTest(AuthTestCase):
    def test_owned_routes_answer_instead_of_the_api_catch_all(self) -> None:
        """Registration order, asserted behaviourally.

        FastAPI matches in registration order, so a feature router included
        after /api/{path:path} can never run: its URLs come back as the
        catch-all's "No API route" 404 instead. Checking the responses rather
        than the route list keeps this honest across FastAPI versions, which
        do not all expose included routers as flat Route objects.
        """
        client = self.local_client()
        for url in ("/api/catalog", "/api/workflows", "/api/media", "/api/session"):
            response = client.get(url)
            self.assertEqual(response.status_code, 200, f"{url}: {response.text}")

        # The catch-all still owns everything nobody claimed.
        unknown = client.get("/api/nope")
        self.assertEqual(unknown.status_code, 404)
        self.assertIn("No API route", unknown.json()["error"]["message"])

    def test_services_are_shared_on_app_state(self) -> None:
        for name in (
            "catalog",
            "workflows",
            "media",
            "repository",
            "auth",
            "settings",
            "db",
        ):
            self.assertTrue(
                hasattr(self.app.state, name), f"app.state.{name} is missing"
            )


class CatalogRouteTest(AuthTestCase):
    def test_snapshot_is_sanitized_and_carries_no_raw_catalog(self) -> None:
        body = self.local_client().get("/api/catalog").json()
        # ComfyUI is not running under test, so this is the offline projection.
        self.assertEqual(body["freshness"]["state"], "unavailable")
        self.assertEqual(body["nodes"], {})
        self.assertNotIn("raw", body)
        self.assertEqual(
            self.local_client().get("/api/catalog").headers["cache-control"],
            "private, no-store",
        )

    def test_catalog_requires_a_session_once_multi_user_is_on(self) -> None:
        self.enable_multi_user(PASSWORD)
        self.assertEqual(self.lan_client().get("/api/catalog").status_code, 401)

    def test_refresh_is_refused_cross_site(self) -> None:
        response = self.local_client().post(
            "/api/catalog/refresh", headers={"origin": "http://evil.example"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_refresh_is_refused_without_the_double_submit_token(self) -> None:
        self.enable_multi_user(PASSWORD)
        client = self.local_client()
        self.assertEqual(self.login(client, "Default", PASSWORD).status_code, 200)

        # Same-origin, valid session cookie, missing header: still refused.
        bare = client.post("/api/catalog/refresh")
        self.assertEqual(bare.status_code, 403)
        self.assertIn("CSRF", bare.json()["error"]["message"])

        wrong = client.post(
            "/api/catalog/refresh", headers={CSRF_HEADER: "not-the-token"}
        )
        self.assertEqual(wrong.status_code, 403)
        # 403 rather than 404 already proves the route exists and it is the
        # guard refusing. The positive side of the same shared dependency is
        # asserted in MediaRouteTest, which needs no upstream call to answer.


class WorkflowRouteTest(AuthTestCase):
    def import_graph(self, client, name: str = "Portrait", graph=None):
        return self.post(
            client,
            f"/api/workflows?name={name}",
            content=json.dumps(graph if graph is not None else GRAPH),
            headers={"content-type": "application/json"},
        )

    def test_import_then_list_and_project_controls(self) -> None:
        client = self.local_client()
        created = self.import_graph(client)
        self.assertEqual(created.status_code, 201, created.text)
        workflow_id = created.json()["id"]

        listed = client.get("/api/workflows").json()
        self.assertEqual([w["id"] for w in listed["items"]], [workflow_id])
        self.assertEqual(listed["items"][0]["name"], "Portrait")

        controls = client.get(f"/api/workflows/{workflow_id}/controls")
        self.assertEqual(controls.status_code, 200, controls.text)
        schema = controls.json()
        self.assertEqual(schema["workflow_id"], workflow_id)
        self.assertEqual(schema["revision"], 1)
        # No catalog under test: controls exist but are explicitly unvalidated,
        # which is a blocking condition rather than a guessed default.
        self.assertTrue(
            schema["blocking"], "an unavailable catalog must block submission"
        )
        seeds = [c for c in schema["controls"] if c["input_name"] == "seed"]
        self.assertEqual([c["value"] for c in seeds], ["12345678901234567890"])

    def test_a_ui_export_is_refused_with_a_structured_422(self) -> None:
        response = self.import_graph(
            self.local_client(), graph={"nodes": [], "links": [], "extra": {}}
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()["error"]
        self.assertEqual(body["code"], "unprocessable")
        self.assertIn("Export (API)", body["message"])

    def test_import_needs_csrf_and_controls_are_owner_scoped(self) -> None:
        self.enable_multi_user(PASSWORD)
        default = self.local_client()
        self.login(default, "Default", PASSWORD)
        workflow_id = self.import_graph(default).json()["id"]

        self.assertEqual(
            default.post("/api/workflows?name=Sneaky", content="{}").status_code, 403
        )

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        self.assertEqual(bee.get("/api/workflows").json()["items"], [])
        self.assertEqual(
            bee.get(f"/api/workflows/{workflow_id}/controls").status_code, 404
        )

    def test_deleting_a_workflow_removes_it_from_the_list(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client).json()["id"]

        self.assertEqual(
            self.delete(client, f"/api/workflows/{workflow_id}").status_code, 204
        )
        self.assertEqual(client.get("/api/workflows").json()["items"], [])
        self.assertEqual(
            client.get(f"/api/workflows/{workflow_id}/controls").status_code, 404
        )

    def test_deleting_an_unowned_or_missing_workflow_is_a_404(self) -> None:
        self.enable_multi_user(PASSWORD)
        default = self.local_client()
        self.login(default, "Default", PASSWORD)
        workflow_id = self.import_graph(default).json()["id"]

        self.assertEqual(self.delete(default, "/api/workflows/nope").status_code, 404)

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        self.assertEqual(
            self.delete(bee, f"/api/workflows/{workflow_id}").status_code, 404
        )
        # Untouched for the owner.
        self.assertEqual(len(default.get("/api/workflows").json()["items"]), 1)


class MediaRouteTest(AuthTestCase):
    def test_gallery_is_reachable_and_the_baseline_gate_is_the_real_one(self) -> None:
        self.assertEqual(self.local_client().get("/api/media").json()["items"], [])
        # main.py replaced auth's placeholder gate with the media service, so
        # activation refuses until a local import has run.
        self.assertEqual(self.auth.baseline_gate, self.app.state.media.baseline_status)
        self.assertFalse(self.auth.baseline_gate(self.db).ready)

    def test_local_import_is_refused_from_the_lan(self) -> None:
        response = self.post(self.lan_client(), "/api/media/import")
        self.assertEqual(response.status_code, 403)

    def test_a_mutation_passes_the_guard_once_the_csrf_token_is_sent(self) -> None:
        self.enable_multi_user(PASSWORD)
        client = self.local_client()
        self.login(client, "Default", PASSWORD)

        self.assertEqual(
            client.put("/api/media/nope", json={"hidden": True}).status_code, 403
        )
        # Same request with the double-submit header reaches the handler, which
        # then reports the unknown item: the guard passed, it did not swallow.
        allowed = self.put(client, "/api/media/nope", json={"hidden": True})
        self.assertEqual(allowed.status_code, 404)
        self.assertEqual(allowed.json()["error"]["code"], "not_found")


class LifespanTest(unittest.TestCase):
    """Startup and shutdown with nothing listening upstream."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        build = root / "build"
        build.mkdir(parents=True)
        (build / "index.html").write_text("<!doctype html>SPA shell", encoding="utf-8")
        self.config = Config.from_env(
            {
                # Discard port: refused immediately, so startup meets a real
                # unreachable ComfyUI without waiting for a timeout.
                "SIMPLEUI_COMFY_URL": "http://127.0.0.1:9",
                "SIMPLEUI_DATA_DIR": str(root / "data"),
                "SIMPLEUI_STATIC_DIR": str(build),
            }
        )
        self.addCleanup(self._tmp.cleanup)

    def test_app_starts_and_serves_while_comfyui_is_unavailable(self) -> None:
        app = create_app(self.config)
        self.addCleanup(app.state.db.close)
        with TestClient(
            app, base_url=LOCAL_ORIGIN, client=("127.0.0.1", 51000)
        ) as client:
            self.assertEqual(client.get("/api/health").json()["status"], "ok")
            snapshot = client.get("/api/catalog").json()
            self.assertEqual(snapshot["freshness"]["state"], "unavailable")
            self.assertFalse(snapshot["freshness"]["submission_allowed"])
            # The UI is still served; an offline catalog is not a fatal error.
            self.assertIn("SPA shell", client.get("/gallery").text)

    def test_shutdown_closes_the_upstream_http_client(self) -> None:
        app = create_app(self.config)
        self.addCleanup(app.state.db.close)
        with TestClient(
            app, base_url=LOCAL_ORIGIN, client=("127.0.0.1", 51000)
        ) as client:
            client.get("/api/catalog")
            comfy = app.state.catalog._client
            self.assertFalse(comfy._client.is_closed)
        # The startup refresh task is cancelled and awaited before this, so a
        # clean exit here also means shutdown did not hang on it.
        self.assertTrue(comfy._client.is_closed, "shutdown left the HTTP client open")


if __name__ == "__main__":
    unittest.main()
