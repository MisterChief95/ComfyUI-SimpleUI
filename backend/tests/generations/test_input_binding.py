"""INTEGRATE-003: a file-typed edit is staged and bound before submission.

app/mapping/input_adapters.bind_upload turns an upload id into the graph
literal ComfyUI expects; this checks the wiring in the submit route calls it
(staging is real filesystem I/O, so it must run before the resolve() closure
that executes inside the store's write transaction -- see routes.py).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot

from tests.auth.support import AuthTestCase
from tests.uploads.support import png_bytes

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
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
PASSWORD = "default-password"


class FakeUpstream:
    def __init__(self) -> None:
        self.submit_count = 0
        self.last_graph: dict | None = None

    async def submit_prompt(self, graph, **kwargs):
        self.submit_count += 1
        self.last_graph = graph
        return {"prompt_id": "p1", "node_errors": {}}

    async def get_queue(self):
        return {"queue_running": [], "queue_pending": []}

    async def get_history(self):
        return {}

    async def cancel_pending(self, prompt_id):
        pass

    async def upload_image(self, path, *, filename, subfolder):
        return {"name": filename, "subfolder": subfolder, "type": "input"}


class UploadBindingTest(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()

        async def fixed_snapshot():
            return SNAPSHOT

        self.app.state.catalog.snapshot = fixed_snapshot
        self.upstream = FakeUpstream()
        self.app.state.generations.upstream = self.upstream
        self.app.state.uploads.comfy = self.upstream

    def import_graph(self, client):
        body = (FIXTURES / "graphs" / "image_loader_input.api.json").read_bytes()
        response = self.post(
            client,
            "/api/workflows?name=Loader",
            content=body,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def upload(self, client, kind: str, filename: str, data: bytes) -> str:
        response = self.post(
            client, f"/api/uploads?kind={kind}&filename={filename}", content=data
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def submit(self, client, workflow_id: str, edits: dict, request_key: str = "req-1"):
        return self.post(
            client,
            "/api/generations",
            json={
                "workflow_id": workflow_id,
                "request_key": request_key,
                "seed_policy": "fixed",
                "edits": edits,
            },
        )

    def test_a_file_edit_is_uploaded_and_bound_before_submission(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        upload_id = self.upload(client, "image", "ref.png", png_bytes())

        response = self.submit(client, workflow_id, {"2:image": upload_id})
        self.assertEqual(response.status_code, 201, response.text)

        submitted_value = self.upstream.last_graph["2"]["inputs"]["image"]
        self.assertNotEqual(
            submitted_value, upload_id, "the raw upload id must never reach ComfyUI"
        )
        self.assertTrue(
            submitted_value.startswith("simpleui/default/"), submitted_value
        )

    def test_a_foreign_upload_id_is_refused_and_nothing_is_left_half_submitted(
        self,
    ) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        foreign_upload = self.upload(bee, "image", "ref.png", png_bytes())

        response = self.submit(owner, workflow_id, {"2:image": foreign_upload})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.upstream.submit_count, 0)

        # The idempotency key was never occupied by the failed attempt: a
        # retry with a real upload under the same key still succeeds.
        real_upload = self.upload(owner, "image", "ref.png", png_bytes())
        retried = self.submit(owner, workflow_id, {"2:image": real_upload})
        self.assertEqual(retried.status_code, 201, retried.text)


if __name__ == "__main__":
    unittest.main()
