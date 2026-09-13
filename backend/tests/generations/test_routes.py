"""INTEGRATE-002: the generation service reachable over real HTTP.

test_generations.py exercises GenerationService/GenerationStore directly with
a fake upstream; this file only asserts the wiring on top of that -- routes
registered ahead of the catch-all, ownership isolation, idempotent
resubmission, and capability-safe cancellation -- the same way
test_integration.py does for the earlier services.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot

from tests.auth.support import AuthTestCase

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
GRAPH_NAME = "image_basic.api.json"
CATALOG = normalize(
    json.loads((FIXTURES / "catalog" / "object_info.synthetic.json").read_text(encoding="utf-8"))
)
SNAPSHOT = CatalogSnapshot(freshness=CatalogFreshness(state="fresh"), nodes=CATALOG.nodes, capabilities={})

PASSWORD = "default-password"


class FakeUpstream:
    """Same shape as test_generations.py's fake: no real ComfyUI in this suite."""

    def __init__(self) -> None:
        self.submit_count = 0
        self.response = {"prompt_id": "prompt-1", "node_errors": {}}
        self.queue = {"queue_running": [], "queue_pending": []}
        self.history: dict = {}
        self.cancelled: list[str] = []

    async def submit_prompt(self, graph, **kwargs):
        self.submit_count += 1
        return self.response

    async def get_queue(self):
        return self.queue

    async def get_history(self):
        return self.history

    async def cancel_pending(self, prompt_id):
        self.cancelled.append(prompt_id)


class GenerationRouteTestCase(AuthTestCase):
    """A logged-in client, a fixed catalog snapshot, and a fake upstream."""

    def setUp(self) -> None:
        super().setUp()

        async def fixed_snapshot():
            return SNAPSHOT

        self.app.state.catalog.snapshot = fixed_snapshot
        self.upstream = FakeUpstream()
        self.app.state.generations.upstream = self.upstream

    def import_graph(self, client, name: str = "Portrait"):
        body = (FIXTURES / "graphs" / GRAPH_NAME).read_bytes()
        response = self.post(
            client, f"/api/workflows?name={name}",
            content=body, headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def submit(
        self, client, workflow_id: str, request_key: str,
        edits: dict | None = None, seed_policy: str = "fixed",
    ):
        # "fixed" makes assertions deterministic; the profile default
        # ("random") is exercised by app.mapping.submission's own tests.
        return self.post(
            client, "/api/generations",
            json={
                "workflow_id": workflow_id, "request_key": request_key,
                "edits": edits or {}, "seed_policy": seed_policy,
            },
        )


class SubmissionAndIdempotencyTest(GenerationRouteTestCase):
    def test_routes_answer_ahead_of_the_catch_all(self) -> None:
        client = self.local_client()
        self.assertEqual(client.get("/api/generations").status_code, 200)

    def test_submit_returns_a_durable_generation_bound_to_the_graph(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)

        response = self.submit(client, workflow_id, "req-1")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["output_state"], "pending")
        self.assertEqual(self.upstream.submit_count, 1)

        detail = client.get(f"/api/generations/{body['id']}").json()
        self.assertEqual(detail["id"], body["id"])
        # The seed control's stored default survives -- it was not re-randomized
        # just because a seed policy exists (WORKFLOW_MAPPING.md).
        self.assertEqual(detail["effective_values"]["5:seed"], "123456789")

    def test_repeating_a_request_key_never_resubmits_upstream(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)

        first = self.submit(client, workflow_id, "req-1").json()
        second = self.submit(client, workflow_id, "req-1").json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.upstream.submit_count, 1, "a retried request key must not resubmit")

    def test_reusing_a_request_key_with_a_changed_payload_conflicts(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)

        self.submit(client, workflow_id, "req-1")
        changed = self.submit(client, workflow_id, "req-1", edits={"2:text": "a different prompt"})
        self.assertEqual(changed.status_code, 409)

    def test_a_lost_submit_response_never_triggers_a_blind_resubmit(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)

        async def raise_timeout(graph, **kwargs):
            self.upstream.submit_count += 1
            raise TimeoutError("response lost")

        self.upstream.submit_prompt = raise_timeout
        uncertain = self.submit(client, workflow_id, "req-1").json()
        self.assertEqual(uncertain["status"], "submission_unknown")

        # The client (or a reconnect) retries with the same key: still no
        # second call to ComfyUI, because retrying an unknown side effect is
        # exactly what this key exists to prevent.
        self.upstream.submit_prompt = self.upstream.__class__().submit_prompt
        again = self.submit(client, workflow_id, "req-1").json()
        self.assertEqual(again["id"], uncertain["id"])
        self.assertEqual(self.upstream.submit_count, 1)


class OwnershipIsolationTest(GenerationRouteTestCase):
    def test_another_profile_sees_nothing_by_listing_or_by_guessing_the_id(self) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        generation_id = self.submit(owner, workflow_id, "req-1").json()["id"]

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)

        self.assertEqual(bee.get("/api/generations").json()["items"], [])
        self.assertEqual(bee.get(f"/api/generations/{generation_id}").status_code, 404)

    def test_submission_requires_a_session(self) -> None:
        self.enable_multi_user(PASSWORD)
        anonymous = self.local_client()
        response = self.submit(anonymous, "whatever", "req-1")
        self.assertEqual(response.status_code, 401)


class CancellationTest(GenerationRouteTestCase):
    def test_owner_can_cancel_a_still_queued_generation(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        generation_id = self.submit(client, workflow_id, "req-1").json()["id"]

        response = self.post(client, f"/api/generations/{generation_id}/cancel")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.upstream.cancelled, ["prompt-1"])

    def test_a_foreign_or_unknown_id_is_refused_the_same_way(self) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        generation_id = self.submit(owner, workflow_id, "req-1").json()["id"]

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)

        foreign = self.post(bee, f"/api/generations/{generation_id}/cancel")
        unknown = self.post(bee, "/api/generations/does-not-exist/cancel")
        self.assertEqual(foreign.status_code, 409)
        self.assertEqual(unknown.status_code, 409)
        self.assertEqual(foreign.json()["error"]["message"], unknown.json()["error"]["message"])

    def test_there_is_no_global_cancel_route(self) -> None:
        client = self.local_client()
        # No path takes a bare verb: cancellation always names one generation.
        self.assertEqual(self.post(client, "/api/generations/cancel").status_code, 404)


class EventStreamTest(GenerationRouteTestCase):
    def test_an_authenticated_socket_receives_only_its_own_owner_events(self) -> None:
        self.enable_multi_user(PASSWORD)
        client = self.local_client()
        self.login(client, "Default", PASSWORD)

        # TestClient's websocket_connect always dials ws://testserver and does
        # not attach the client's cookie jar the way its plain HTTP requests
        # do; a real browser's Host/Origin/Cookie always agree, so this only
        # papers over the harness, not the route's actual origin/auth checks.
        cookie_header = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        headers = {"host": "localhost:8000", "cookie": cookie_header}
        with client.websocket_connect("/api/events", headers=headers) as socket:
            self.app.state.generation_events.publish("someone-else", {"type": "noise"})
            self.app.state.generation_events.publish("default", {"type": "queued", "generation_id": "g1"})
            self.assertEqual(socket.receive_json(), {"type": "queued", "generation_id": "g1"})

    def test_an_unauthenticated_socket_is_refused(self) -> None:
        self.enable_multi_user(PASSWORD)
        client = self.local_client()
        with self.assertRaises(Exception):
            with client.websocket_connect("/api/events"):
                pass


if __name__ == "__main__":
    unittest.main()
