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

    def test_the_submitted_graph_can_be_read_back_by_its_owner_only(self) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        generation_id = self.submit(owner, workflow_id, "req-1").json()["id"]

        graph = owner.get(f"/api/generations/{generation_id}/graph")
        self.assertEqual(graph.status_code, 200)
        self.assertIn("5", graph.json())  # node ids are the keys of an API graph

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        self.assertEqual(bee.get(f"/api/generations/{generation_id}/graph").status_code, 404)

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
        self.assertEqual(bee.get("/api/generations?status=active").json()["items"], [])
        self.assertEqual(self.post(bee, f"/api/generations/{generation_id}/retry",
                                   json={"request_key": "foreign-retry"}).status_code, 404)

    def test_submission_requires_a_session(self) -> None:
        self.enable_multi_user(PASSWORD)
        anonymous = self.local_client()
        response = self.submit(anonymous, "whatever", "req-1")
        self.assertEqual(response.status_code, 401)


class QueueAndRetryTest(GenerationRouteTestCase):
    def test_active_filter_excludes_terminal_rows_and_paginates(self) -> None:
        client = self.local_client()
        workflow = self.import_graph(client)
        ids = [self.submit(client, workflow, f"queue-{i}").json()["id"] for i in range(3)]
        self.app.state.generations.store.update("default", ids[0], status="failed", output_state="unavailable")
        self.upstream.queue["queue_pending"] = [[0, "prompt-1"]]
        first = client.get("/api/generations?status=active&limit=1").json()
        second = client.get("/api/generations?status=active&limit=1&cursor=" + first["next_cursor"]).json()
        self.assertEqual({first["items"][0]["id"], second["items"][0]["id"]}, set(ids[1:]))
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(client.get("/api/generations?status=anything").status_code, 422)

    def test_retry_copies_the_execution_snapshot_and_is_idempotent(self) -> None:
        client = self.local_client()
        workflow = self.import_graph(client)
        source = self.submit(client, workflow, "source", edits={
            "2:text": "the original prompt", "5:seed": "9007199254740993"
        }).json()
        store = self.app.state.generations.store
        store.update("default", source["id"], status="cancelled", output_state="unavailable")
        original = store.get("default", source["id"])
        sent = []

        async def recording(graph, **kwargs):
            sent.append(graph)
            self.upstream.submit_count += 1
            return {"prompt_id": "retry-prompt"}

        self.upstream.submit_prompt = recording
        path = f"/api/generations/{source['id']}/retry"
        first = self.post(client, path, json={"request_key": "retry-key"})
        again = self.post(client, path, json={"request_key": "retry-key"})
        self.assertEqual(first.status_code, 201, first.text)
        self.assertNotEqual(first.json()["id"], source["id"])
        self.assertEqual(first.json()["id"], again.json()["id"])
        self.assertEqual(sent, [original["graph"]])
        self.assertEqual(first.json()["effective_values"], original["effective_values"])
        self.assertEqual(self.upstream.submit_count, 2)

    def test_retry_refuses_active_uncertain_or_purged_snapshots(self) -> None:
        client = self.local_client()
        workflow = self.import_graph(client)
        source = self.submit(client, workflow, "source").json()
        path = f"/api/generations/{source['id']}/retry"
        store = self.app.state.generations.store
        self.upstream.queue["queue_pending"] = [[0, "prompt-1"]]
        for status in ("queued", "running", "submission_unknown", "unknown"):
            store.update("default", source["id"], status=status)
            response = self.post(client, path, json={"request_key": "refused-" + status})
            self.assertEqual(response.status_code, 409, response.text)
        store.update("default", source["id"], status="cancelled", output_state="unavailable")
        store.purge_snapshot("default", source["id"])
        self.assertEqual(self.post(client, path, json={"request_key": "purged"}).status_code, 409)
        self.assertEqual(self.upstream.submit_count, 1)


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

    def test_an_open_socket_alone_drives_a_generation_to_completion(self) -> None:
        """GEN-003: no client GET, just an open socket -- as the real app does.

        This app runs no perpetual background poller and the frontend only
        ever refetches a generation when an event tells it to (it does not
        itself poll /api/generations). Before this fix, a job that only
        reached a terminal state through history reconciliation (as any fully
        cached ComfyUI run does) could sit at output_state 'pending' forever
        with nothing to ever notice or announce it finished.
        """
        client = self.local_client()
        workflow_id = self.import_graph(client)
        submitted = self.submit(client, workflow_id, "req-1").json()
        self.assertEqual((submitted["status"], submitted["output_state"]), ("queued", "pending"))

        self.upstream.history = {
            "prompt-1": {"outputs": {}, "status": {"status_str": "success", "completed": True}}
        }
        # TestClient's websocket_connect dials ws://testserver by default,
        # which does not match the loopback Host/Origin local_client() set up
        # for its plain HTTP requests (see the other socket test above).
        with client.websocket_connect("/api/events", headers={"host": "localhost:8000"}) as socket:
            event = socket.receive_json()
        self.assertEqual(event, {"generation_id": submitted["id"], "type": "reconciled", "data": {}})

        final = client.get(f"/api/generations/{submitted['id']}").json()
        self.assertEqual((final["status"], final["output_state"]), ("succeeded", "unavailable"))


class TypedEditsTest(GenerationRouteTestCase):
    """LAYOUT-001: edits are string | boolean | number, encoded like ControlDescriptor.value."""

    def setUp(self) -> None:
        super().setUp()
        raw = json.loads((FIXTURES / "catalog" / "object_info.synthetic.json").read_text(encoding="utf-8"))
        raw["TypedNode"] = {
            **raw["SaveImage"],
            "name": "TypedNode",
            "input": {"required": {
                "flag": ["BOOLEAN", {"default": False}],
                "count": ["INT", {"default": 1, "min": 0, "max": 10}],
                "big": ["INT", {"default": 0, "min": 0, "max": 2**64 - 1}],
                "scale": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0}],
                "note": ["STRING", {"default": "x"}],
            }},
            "input_order": {"required": ["flag", "count", "big", "scale", "note"]},
        }
        snapshot = CatalogSnapshot(
            freshness=CatalogFreshness(state="fresh"), nodes=normalize(raw).nodes, capabilities={}
        )

        async def typed_snapshot():
            return snapshot

        self.app.state.catalog.snapshot = typed_snapshot
        self.sent: list[dict] = []
        original = self.upstream.submit_prompt

        async def recording(graph, **kwargs):
            self.sent.append(graph)
            return await original(graph, **kwargs)

        self.upstream.submit_prompt = recording
        self.client = self.local_client()
        graph = {"1": {"class_type": "TypedNode", "inputs": {
            "flag": False, "count": 1, "big": 0, "scale": 1.0, "note": "x"}}}
        response = self.post(
            self.client, "/api/workflows?name=Typed",
            content=json.dumps(graph), headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.workflow_id = response.json()["id"]

    def test_json_booleans_numbers_and_exact_ints_submit_with_their_types(self) -> None:
        response = self.submit(self.client, self.workflow_id, "typed-1", edits={
            "1:flag": True, "1:count": 7, "1:big": "18446744073709551615",
            "1:scale": 2.5, "1:note": "hello",
        })
        self.assertEqual(response.status_code, 201, response.text)
        inputs = self.sent[-1]["1"]["inputs"]
        self.assertIs(inputs["flag"], True)
        self.assertEqual((inputs["count"], inputs["big"]), (7, 2**64 - 1))
        self.assertEqual((inputs["scale"], inputs["note"]), (2.5, "hello"))
        # Stored values keep the ControlDescriptor.value encoding.
        effective = response.json()["effective_values"]
        self.assertEqual(effective["1:flag"], True)
        self.assertEqual(effective["1:count"], "7")
        self.assertEqual(effective["1:big"], "18446744073709551615")

    def test_a_large_json_integer_keeps_every_digit(self) -> None:
        response = self.client.post(
            "/api/generations",
            content=(
                '{"workflow_id": "%s", "request_key": "typed-big", "seed_policy": "fixed",'
                ' "edits": {"1:big": 9007199254740993}}' % self.workflow_id
            ),
            headers={**self.csrf_headers(self.client), "content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.sent[-1]["1"]["inputs"]["big"], 9007199254740993)
        self.assertEqual(response.json()["effective_values"]["1:big"], "9007199254740993")

    def test_a_number_is_not_turned_into_text_and_text_is_not_a_bool(self) -> None:
        refused = self.submit(self.client, self.workflow_id, "typed-2", edits={"1:note": 5})
        self.assertEqual(refused.status_code, 422, refused.text)
        refused = self.submit(self.client, self.workflow_id, "typed-3", edits={"1:flag": "yes"})
        self.assertEqual(refused.status_code, 422, refused.text)
        refused = self.submit(self.client, self.workflow_id, "typed-4", edits={"1:flag": 1})
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertEqual(self.upstream.submit_count, 0)

    def test_legacy_boolean_strings_are_still_accepted_and_normalized(self) -> None:
        for key, text, expected in (("typed-5", "true", True), ("typed-6", "false", False)):
            response = self.submit(self.client, self.workflow_id, key, edits={"1:flag": text})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertIs(self.sent[-1]["1"]["inputs"]["flag"], expected)
            self.assertIs(response.json()["effective_values"]["1:flag"], expected)
        self.assertEqual(
            self.submit(self.client, self.workflow_id, "typed-7", edits={"1:flag": "True"}).status_code, 422
        )

    def test_idempotency_fingerprint_covers_non_string_values(self) -> None:
        first = self.submit(self.client, self.workflow_id, "typed-8", edits={"1:flag": True, "1:scale": 2.5})
        again = self.submit(self.client, self.workflow_id, "typed-8", edits={"1:scale": 2.5, "1:flag": True})
        self.assertEqual((again.status_code, again.json()["id"]), (201, first.json()["id"]))
        self.assertEqual(self.upstream.submit_count, 1)
        changed = self.submit(self.client, self.workflow_id, "typed-8", edits={"1:flag": False, "1:scale": 2.5})
        self.assertEqual(changed.status_code, 409, changed.text)


if __name__ == "__main__":
    unittest.main()
