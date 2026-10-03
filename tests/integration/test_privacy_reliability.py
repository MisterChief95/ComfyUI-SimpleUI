"""VERIFY-001 checks that only make sense against the assembled application.

Run from the repository root with ``backend`` on ``PYTHONPATH``; feature-level
edge cases stay in backend/tests rather than being repeated here.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.auth.security import SESSION_COOKIE
from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot
from app.config import Config
from app.generations import GenerationStore
from app.main import create_app
from app.storage import DEFAULT_PROFILE_ID
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect
from tests.auth.support import LOCAL_ORIGIN, AuthTestCase
from tests.uploads.support import png_bytes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PASSWORD = "default-password"
GRAPH = (FIXTURES / "graphs" / "image_loader_input.api.json").read_bytes()
IMAGE_GRAPH = (FIXTURES / "graphs" / "image_basic.api.json").read_bytes()
CATALOG = normalize(
    json.loads(
        (FIXTURES / "catalog" / "object_info.synthetic.json").read_text(
            encoding="utf-8"
        )
    )
)
SNAPSHOT = CatalogSnapshot(
    freshness=CatalogFreshness(state="fresh"), nodes=CATALOG.nodes, capabilities={}
)


class FakeUpstream:
    def __init__(self) -> None:
        self.submit_count = 0
        self.queue = {"queue_running": [], "queue_pending": []}
        self.history: dict = {}
        self.fail_poll = False

    async def submit_prompt(self, graph, **kwargs):
        self.submit_count += 1
        self.last_graph = graph
        return {"prompt_id": f"prompt-{self.submit_count}", "node_errors": {}}

    async def get_queue(self):
        if self.fail_poll:
            raise ConnectionError("ComfyUI restarted during polling")
        return self.queue

    async def get_history(self):
        if self.fail_poll:
            raise ConnectionError("ComfyUI restarted during polling")
        return self.history

    async def cancel_pending(self, prompt_id):
        return None


def successful_history(prompt_id: str, relative: str) -> dict:
    path = Path(relative)
    return {
        prompt_id: {
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": path.name,
                            "subfolder": path.parent.as_posix()
                            if path.parent != Path(".")
                            else "",
                            "type": "output",
                        }
                    ]
                }
            },
            "status": {"status_str": "success", "completed": True},
        }
    }


class AssembledAppTestCase(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        root = Path(self._tmp.name)
        self.output = root / "output"
        self.input = root / "input"
        self.output.mkdir()
        self.input.mkdir()
        self.app.state.settings.set_host("comfy_output_dir", str(self.output))
        self.app.state.settings.set_host("comfy_input_dir", str(self.input))

        async def fixed_snapshot():
            return SNAPSHOT

        self.app.state.catalog.snapshot = fixed_snapshot
        self.upstream = FakeUpstream()
        self.app.state.generations.upstream = self.upstream

    def sign_in(self, name: str, password: str = PASSWORD):
        client = self.local_client()
        response = self.login(client, name, password)
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def import_graph(self, client, graph: bytes = GRAPH) -> str:
        response = self.post(
            client,
            "/api/workflows?name=Private",
            content=graph,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def submit(self, client, workflow_id: str, *, edits: dict[str, str] | None = None):
        return self.post(
            client,
            "/api/generations",
            json={
                "workflow_id": workflow_id,
                "request_key": "request-1",
                "edits": edits or {},
                "seed_policy": "fixed",
            },
        )

    def websocket_headers(self, client) -> dict[str, str]:
        cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        return {"host": "localhost:8000", "cookie": cookie}


class CrossFeaturePrivacyTest(AssembledAppTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app.state.media.import_baseline()
        self.auth.activate_multi_user(PASSWORD)
        self.auth.create_profile("Bee", PASSWORD)

    def test_one_owned_flow_stays_private_across_every_surface_and_switch(self) -> None:
        owner = self.sign_in("Default")
        uploaded = self.post(
            owner,
            "/api/uploads?kind=image&filename=private.png",
            content=png_bytes(),
            headers={"content-type": "image/png"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        upload_id = uploaded.json()["id"]

        workflow_id = self.import_graph(owner)
        controls = owner.get(f"/api/workflows/{workflow_id}/controls").json()[
            "controls"
        ]
        picker = next(
            control for control in controls if control["binding_id"] == "2:image"
        )
        self.assertEqual(picker["component"], "file")
        catalog = owner.get("/api/catalog").text
        self.assertNotIn("placeholder-input-a.png", catalog)
        self.assertNotIn("placeholder-shared-mask.png", catalog)

        submitted = self.submit(owner, workflow_id, edits={"2:image": upload_id})
        self.assertEqual(submitted.status_code, 201, submitted.text)
        generation_id = submitted.json()["id"]
        staged = self.upstream.last_graph["2"]["inputs"]["image"]
        self.assertIn(DEFAULT_PROFILE_ID, staged)
        self.assertNotEqual(staged, upload_id)

        result = self.output / "private-result.png"
        Image.new("RGB", (16, 16), (20, 40, 60)).save(result)
        self.upstream.history = successful_history("prompt-1", result.name)
        self.app.state.generations._next_reconcile_s = 0
        detail = owner.get(f"/api/generations/{generation_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["output_state"], "ready")
        media_id = owner.get("/api/media").json()["items"][0]["id"]

        bee = self.sign_in("Bee")
        with bee.websocket_connect(
            "/api/events", headers=self.websocket_headers(bee)
        ) as socket:
            self.app.state.generation_events.publish(
                DEFAULT_PROFILE_ID, {"type": "private", "generation_id": generation_id}
            )
            self.app.state.generation_events.publish(
                bee.get("/api/session").json()["profile"]["id"],
                {"type": "mine", "generation_id": "bee-generation"},
            )
            self.assertEqual(socket.receive_json()["generation_id"], "bee-generation")

        self.assertEqual(bee.get("/api/workflows").json()["items"], [])
        self.assertEqual(
            bee.get(f"/api/workflows/{workflow_id}/controls").status_code, 404
        )
        self.assertEqual(bee.get("/api/generations").json()["items"], [])
        self.assertEqual(bee.get(f"/api/generations/{generation_id}").status_code, 404)
        self.assertEqual(bee.get("/api/media").json()["items"], [])
        for suffix in ("file", "download", "thumbnail"):
            self.assertEqual(
                bee.get(f"/api/media/{media_id}/{suffix}").status_code, 404
            )
        self.assertEqual(
            bee.get(
                f"/api/media/{media_id}/file", headers={"range": "bytes=0-3"}
            ).status_code,
            404,
        )
        self.assertEqual(bee.head(f"/api/media/{media_id}/file").status_code, 404)
        self.assertEqual(bee.get("/api/uploads").json()["items"], [])
        self.assertEqual(self.delete(bee, f"/api/uploads/{upload_id}").status_code, 404)

        # Reuse one cookie jar, as a browser does when switching profiles.
        switched = owner
        self.assertEqual(self.delete(switched, "/api/session").status_code, 200)
        self.assertEqual(self.login(switched, "Bee", PASSWORD).status_code, 200)
        self.assertEqual(switched.get("/api/workflows").json()["items"], [])
        self.assertEqual(
            switched.get(f"/api/generations/{generation_id}").status_code, 404
        )
        self.assertEqual(switched.get(f"/api/media/{media_id}/file").status_code, 404)
        self.assertEqual(switched.get("/api/uploads").json()["items"], [])

        default_again = self.sign_in("Default")
        self.assertEqual(
            default_again.get(f"/api/generations/{generation_id}").status_code, 200
        )
        self.assertEqual(
            default_again.get(f"/api/media/{media_id}/file").status_code, 200
        )
        self.assertEqual(
            [item["id"] for item in default_again.get("/api/uploads").json()["items"]],
            [upload_id],
        )

    def test_logout_invalidates_an_already_open_owner_socket(self) -> None:
        browser = self.sign_in("Default")
        headers = self.websocket_headers(browser)
        with browser.websocket_connect("/api/events", headers=headers) as socket:
            self.assertEqual(self.delete(browser, "/api/session").status_code, 200)
            self.assertEqual(self.login(browser, "Bee", PASSWORD).status_code, 200)
            self.app.state.generation_events.publish(
                DEFAULT_PROFILE_ID, {"type": "private", "generation_id": "old-owner"}
            )
            with self.assertRaises(WebSocketDisconnect) as caught:
                socket.receive_json()
            self.assertEqual(caught.exception.code, 4401)

    def test_expired_session_gets_401_from_every_authenticated_feature(self) -> None:
        browser = self.sign_in("Default")
        token = browser.cookies.get(SESSION_COOKIE)
        self.assertIsNotNone(token)
        self.auth.expire_session_now(token)

        for url in ("/api/workflows", "/api/generations", "/api/media", "/api/uploads"):
            with self.subTest(url=url):
                response = browser.get(url)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.headers["cache-control"], "private, no-store")


class BaselineRaceTest(AssembledAppTestCase):
    def test_activation_cannot_pass_while_the_media_import_is_in_flight(self) -> None:
        Image.new("RGB", (16, 16), (1, 2, 3)).save(self.output / "existing.png")
        entered, release = threading.Event(), threading.Event()
        real_index = self.app.state.media._index

        def paused_index(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return real_index(*args, **kwargs)

        self.app.state.media._index = paused_index
        result: list = []

        def import_media() -> None:
            result.append(self.post(self.local_client(), "/api/media/import"))

        worker = threading.Thread(target=import_media)
        worker.start()
        self.assertTrue(entered.wait(5), "media import never reached its first file")

        activation = self.post(
            self.local_client(),
            "/api/settings/multi-user/activate",
            json={"default_password": PASSWORD},
        )
        self.assertEqual(activation.status_code, 409, activation.text)
        self.assertFalse(self.auth.multi_user_enabled())

        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0].status_code, 200, result[0].text)
        activation = self.post(
            self.local_client(),
            "/api/settings/multi-user/activate",
            json={"default_password": PASSWORD},
        )
        self.assertEqual(activation.status_code, 204, activation.text)


class ReconciliationRouteTest(AssembledAppTestCase):
    def test_poll_failure_keeps_the_durable_detail_then_disk_full_surfaces_there(
        self,
    ) -> None:
        self.app.state.media.import_baseline()
        client = self.local_client()
        workflow_id = self.import_graph(client, IMAGE_GRAPH)
        submitted = self.submit(client, workflow_id)
        self.assertEqual(submitted.status_code, 201, submitted.text)
        generation_id = submitted.json()["id"]

        self.upstream.fail_poll = True
        self.app.state.generations._next_reconcile_s = 0
        with self.assertLogs("simpleui.generations", level="ERROR") as logged:
            cached = client.get(f"/api/generations/{generation_id}")
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertEqual(cached.json()["status"], "queued")
        self.assertIn("Generation reconciliation failed", logged.output[0])

        result = self.output / "result.png"
        Image.new("RGB", (16, 16), (4, 5, 6)).save(result)
        self.upstream.fail_poll = False
        self.upstream.history = successful_history("prompt-1", result.name)
        real_capture = self.app.state.media.capture_output

        def disk_full(*args, **kwargs):
            raise OSError("No space left on device")

        self.app.state.media.capture_output = disk_full
        self.app.state.generations._next_reconcile_s = 0
        failed_capture = client.get(f"/api/generations/{generation_id}")
        self.assertEqual(failed_capture.status_code, 200, failed_capture.text)
        self.assertEqual(failed_capture.json()["status"], "succeeded")
        self.assertEqual(failed_capture.json()["output_state"], "pending")
        self.assertIn(
            "No space", failed_capture.json()["error"]["output_capture"]["message"]
        )
        self.assertEqual(self.upstream.submit_count, 1)

        self.app.state.media.capture_output = real_capture
        self.app.state.generations._next_reconcile_s = 0
        recovered = client.get(f"/api/generations/{generation_id}")
        self.assertEqual(recovered.json()["output_state"], "ready")
        self.assertEqual(self.upstream.submit_count, 1)


class RestartRecoveryTest(unittest.TestCase):
    def test_history_disabled_restart_recovers_output_then_purges_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root / "build"
            output = root / "output"
            build.mkdir()
            output.mkdir()
            (build / "index.html").write_text("SPA", encoding="utf-8")
            config = Config.from_env(
                {
                    "SIMPLEUI_DATA_DIR": str(root / "data"),
                    "SIMPLEUI_STATIC_DIR": str(build),
                    "SIMPLEUI_COMFY_URL": "http://127.0.0.1:9",
                }
            )

            first = create_app(config)
            first.state.settings.set_host("comfy_output_dir", str(output))
            first.state.media.import_baseline()
            Image.new("RGB", (16, 16), (7, 8, 9)).save(output / "restarted.png")
            first.state.settings.set_profile(DEFAULT_PROFILE_ID, "store_history", False)
            store = GenerationStore(first.state.db)
            accepted = store.accept(
                DEFAULT_PROFILE_ID,
                request_key="restart-request",
                fingerprint="restart-fingerprint",
                resolve=lambda: (
                    {"1": {"class_type": "Synthetic", "inputs": {}}},
                    {"prompt": "private"},
                ),
            )
            store.update(
                DEFAULT_PROFILE_ID,
                accepted.row["id"],
                status="queued",
                prompt_id="restart-prompt",
            )
            first.state.db.close()

            restarted = create_app(config)
            upstream = FakeUpstream()
            upstream.history = successful_history("restart-prompt", "restarted.png")
            restarted.state.generations.upstream = upstream
            restarted.state.generations._reconcile_cooldown_s = 0
            with TestClient(
                restarted,
                base_url=LOCAL_ORIGIN,
                client=("127.0.0.1", 51000),
                raise_server_exceptions=False,
            ) as client:
                client.headers["origin"] = LOCAL_ORIGIN
                detail = client.get(f"/api/generations/{accepted.row['id']}")
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["output_state"], "ready")
                self.assertIsNone(detail.json()["effective_values"])
                self.assertEqual(len(client.get("/api/media").json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
