"""CHAIN-001: chain definitions and the stage-by-stage orchestrator.

Stage submission is replaced by a recorder so these tests exercise only the
orchestration rules: when to advance, when to pause, and that a stage key is
never submitted twice.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from tests.auth.support import AuthTestCase

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


class ChainTest(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.local_client()
        self.owner = self.app.state.auth.list_profiles()[0]["id"]
        self.chains = self.app.state.chains
        self.submitted: dict[str, dict] = {}

        async def submit(owner_id, **request):
            key = request["request_key"]
            if key not in self.submitted:  # same idempotency as the real store
                self.submitted[key] = request
                self._insert_generation(owner_id, key, request["workflow_id"])

        self.chains._submit = submit
        body = (FIXTURES / "graphs" / "image_loader_input.api.json").read_bytes()
        response = self.post(
            self.client,
            "/api/workflows?name=Loader",
            content=body,
            headers={"content-type": "application/json"},
        )
        self.workflow_id = response.json()["id"]

    def _insert_generation(self, owner_id, key, workflow_id) -> None:
        now = int(time.time() * 1000)
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO generations (id, owner_id, workflow_id, client_request_key,"
                " request_fingerprint, status, output_state, created_ms, updated_ms)"
                " VALUES (?, ?, ?, ?, 'f', 'queued', 'pending', ?, ?)",
                (f"gen-{key}", owner_id, workflow_id, key, now, now),
            )

    def _finish(self, key, status="succeeded", output_state="ready", media=None):
        with self.db.write() as conn:
            conn.execute(
                "UPDATE generations SET status = ?, output_state = ? WHERE id = ?",
                (status, output_state, f"gen-{key}"),
            )
        if media:
            conn = self.db.connect()
            conn.execute(
                "PRAGMA foreign_keys = OFF"
            )  # fake ids stand in for media rows
            with self.db.write() as conn:
                conn.execute(
                    "INSERT INTO capture_attempts (id, owner_id, generation_id, source_id,"
                    " relative_path, file_version, output_node, ordinal, state, media_id,"
                    " updated_ms) VALUES (?, ?, ?, 'captures', ?, 'v', '9', 0, 'ready', ?, 0)",
                    (f"c-{key}", self.owner, f"gen-{key}", f"{key}.png", media),
                )
            conn.execute("PRAGMA foreign_keys = ON")

    def advance(self, run_id):
        return asyncio.run(self.chains.advance(self.owner, run_id))

    def chain(self, link_node="9"):
        return self.chains.create(
            self.owner,
            __import__(
                "app.chains.service", fromlist=["x"]
            ).ChainDefinition.model_validate(
                {
                    "name": "T2I then upscale",
                    "stages": [
                        {"workflow_id": self.workflow_id},
                        {
                            "workflow_id": self.workflow_id,
                            "links": [
                                {
                                    "binding_id": "2:image",
                                    "from_output_node": link_node,
                                    "ordinal": 0,
                                }
                            ],
                        },
                    ],
                }
            ),
        )

    def test_advances_only_after_captured_output_and_binds_media(self) -> None:
        run = self.chains.start(self.owner, self.chain()["id"], "k1")
        run = self.advance(run["id"])
        key0 = f"chain-{run['id']}-0-0"
        self.assertEqual(list(self.submitted), [key0])

        self.assertEqual(self.advance(run["id"])["stage_index"], 0)  # still queued
        self._finish(key0, output_state="pending")
        self.assertEqual(self.advance(run["id"])["stage_index"], 0)  # capture pending
        self._finish(key0, media="media-1")
        run = self.advance(run["id"])
        key1 = f"chain-{run['id']}-1-0"
        self.assertEqual(run["stage_index"], 1)
        self.assertEqual(
            self.submitted[key1]["inputs"],
            {"2:image": {"source": "media", "id": "media-1"}},
        )

        self._finish(key1, media=None)
        self.assertEqual(self.advance(run["id"])["status"], "succeeded")
        self.assertEqual(len(self.submitted), 2, "no stage is ever submitted twice")

    def test_failure_pauses_and_resume_uses_a_new_attempt(self) -> None:
        run = self.chains.start(self.owner, self.chain()["id"], "k2")
        run = self.advance(run["id"])
        key0 = f"chain-{run['id']}-0-0"
        self._finish(key0, status="failed", output_state="unavailable")
        run = self.advance(run["id"])
        self.assertEqual(run["status"], "paused")
        self.assertIn("failed", run["error"]["message"])

        self.chains.resume(self.owner, run["id"])
        run = self.advance(run["id"])
        self.assertEqual(run["status"], "running")
        self.assertIn(f"chain-{run['id']}-0-1", self.submitted)

    def test_missing_output_pauses_instead_of_guessing(self) -> None:
        run = self.chains.start(self.owner, self.chain(link_node="404")["id"], "k3")
        run = self.advance(run["id"])
        self._finish(f"chain-{run['id']}-0-0", media="media-1")
        run = self.advance(run["id"])
        self.assertEqual(run["status"], "paused")
        self.assertIn("no captured output", run["error"]["message"])
        self.assertEqual(len(self.submitted), 1)

    def test_start_is_idempotent_and_owner_scoped_over_http(self) -> None:
        chain = self.chain()
        first = self.chains.start(self.owner, chain["id"], "same")
        again = self.chains.start(self.owner, chain["id"], "same")
        self.assertEqual(first["id"], again["id"])
        listing = self.client.get("/api/chains")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(json.loads(listing.text)["items"][0]["id"], chain["id"])
        self.assertEqual(self.client.get("/api/chains/nope").status_code, 404)
