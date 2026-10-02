"""GEN-002 durable submission, correlation, and recovery checks.

Run from backend/:  .venv/Scripts/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.events import EventBroker
from app.generations.service import version_supports_targeted_interrupt
from app.generations import (
    CancellationUnavailable,
    GenerationBusy,
    GenerationConflict,
    GenerationService,
    GenerationStore,
)
from app.storage import Database

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
PROMPTS = {
    "image": "00000000-0000-4000-8000-000000000001",
    "video": "00000000-0000-4000-8000-000000000002",
    "cached": "00000000-0000-4000-8000-000000000003",
    "partial": "00000000-0000-4000-8000-000000000004",
}


def events(name: str):
    return [json.loads(line) for line in
            (FIXTURES / "events" / name).read_text(encoding="utf-8").splitlines()]


def history(name: str):
    return json.loads((FIXTURES / "outputs" / name).read_text(encoding="utf-8"))


class FakeUpstream:
    def __init__(self) -> None:
        self.submit_count = 0
        self.response = {"prompt_id": PROMPTS["image"], "node_errors": {}}
        self.raise_submit = False
        self.queue = {"queue_running": [], "queue_pending": []}
        self.history = {}
        self.cancelled = []

    async def submit_prompt(self, graph, **kwargs):
        self.submit_count += 1
        self.last_graph, self.last_kwargs = graph, kwargs
        if self.raise_submit:
            raise TimeoutError("response lost")
        return self.response

    async def get_queue(self):
        return self.queue

    async def get_history(self):
        return self.history

    async def cancel_pending(self, prompt_id):
        self.cancelled.append(prompt_id)


class FakeMedia:
    def __init__(self) -> None:
        self.keys = set()
        self.calls = 0
        self.fail = False

    def capture_output(self, owner_id, generation_id, path, *, output_node, ordinal, preview):
        self.calls += 1
        if self.fail:
            raise OSError("No space left on device")
        self.keys.add((owner_id, generation_id, output_node, ordinal, path))
        return str(len(self.keys))


class GenerationTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO profiles (id, name, is_default, created_ms) VALUES ('other', 'Other', 0, 1)"
            )
        self.store = GenerationStore(self.db)
        self.upstream = FakeUpstream()
        self.media = FakeMedia()
        self.broker = EventBroker(pending_cap=2)
        self.service = GenerationService(
            self.store, self.upstream, media=self.media, events=self.broker
        )
        self.resolutions = 0

    def resolve(self, seed=123):
        def run():
            self.resolutions += 1
            return ({"5": {"class_type": "KSampler", "inputs": {"seed": seed}}},
                    {"5:seed": str(seed)})
        return run

    async def submit(self, **kwargs):
        return await self.service.submit(
            "default", request_key=kwargs.pop("request_key", "request-1"),
            request_payload=kwargs.pop("request_payload", {"workflow": "wf", "policy": "random"}),
            resolve=kwargs.pop("resolve", self.resolve()), **kwargs,
        )


class SubmissionTests(GenerationTestCase):
    async def test_request_key_is_reserved_before_one_seed_resolution(self) -> None:
        first = await self.submit()
        second = await self.submit(resolve=self.resolve(999))

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.resolutions, 1)
        self.assertEqual(self.upstream.submit_count, 1)
        self.assertEqual(second["graph"]["5"]["inputs"]["seed"], 123)
        self.assertEqual(second["effective_values"], {"5:seed": "123"})

    async def test_same_key_changed_payload_conflicts_before_resolution(self) -> None:
        await self.submit()
        with self.assertRaises(GenerationConflict):
            await self.submit(request_payload={"workflow": "different"}, resolve=self.resolve(999))
        self.assertEqual(self.resolutions, 1)

    async def test_owner_and_exact_snapshot_exist_before_upstream_side_effect(self) -> None:
        original = self.upstream.submit_prompt

        async def inspect(graph, **kwargs):
            row = self.db.query_one("SELECT * FROM generations")
            self.assertEqual(row["owner_id"], "default")
            self.assertIsNotNone(row["graph_json"])
            self.assertIn("simpleui_generation_id", kwargs["extra_data"])
            return await original(graph, **kwargs)

        self.upstream.submit_prompt = inspect
        row = await self.submit()
        self.assertEqual(row["status"], "queued")
        self.assertIsNone(self.store.get("other", row["id"]))

    async def test_lost_response_is_never_blindly_resubmitted(self) -> None:
        self.upstream.raise_submit = True
        row = await self.submit()
        self.assertEqual(row["status"], "submission_unknown")

        retry = await self.submit(resolve=self.resolve(999))
        self.assertEqual(retry["id"], row["id"])
        self.assertEqual(self.upstream.submit_count, 1)
        self.assertEqual(self.resolutions, 1)

    async def test_pending_caps_are_enforced_inside_acceptance(self) -> None:
        capped = GenerationService(self.store, self.upstream, global_pending_cap=1)
        await capped.submit(
            "default", request_key="one", request_payload={"n": 1}, resolve=self.resolve()
        )
        with self.assertRaises(GenerationBusy):
            await capped.submit(
                "other", request_key="two", request_payload={"n": 2}, resolve=self.resolve()
            )


class EventTests(GenerationTestCase):
    async def test_events_are_correlated_to_persisted_owner(self) -> None:
        row = await self.submit()
        mine, other = self.broker.subscribe("default"), self.broker.subscribe("other")
        event = next(e for e in events("image_run.events.jsonl") if e["type"] == "progress")
        self.assertTrue(self.service.process_event(event))
        self.assertEqual((await mine.receive())["generation_id"], row["id"])
        self.assertTrue(other.queue.empty())
        self.assertEqual(self.store.get("default", row["id"])["status"], "running")

    async def test_foreign_and_unattributable_previews_are_dropped(self) -> None:
        await self.submit()
        subscription = self.broker.subscribe("default")
        for event in events("foreign_and_preview.events.jsonl"):
            self.assertFalse(self.service.process_event(event))
        self.assertTrue(subscription.queue.empty())

    async def test_slow_subscriber_cannot_stall_or_grow_without_bound(self) -> None:
        await self.submit()
        subscription = self.broker.subscribe("default")
        owned = [e for e in events("image_run.events.jsonl") if e.get("data", {}).get("prompt_id")]
        for event in owned:
            self.service.process_event(event)
        self.assertEqual(subscription.queue.qsize(), 2)
        self.assertEqual(self.store.active()[0]["status"], "running")


class ReconciliationTests(GenerationTestCase):
    async def test_image_and_video_histories_attach_outputs_to_original_owner(self) -> None:
        for kind, filename in (("image", "history_image.json"), ("video", "history_video.json")):
            with self.subTest(kind=kind):
                prompt_id = PROMPTS[kind]
                self.upstream.response = {"prompt_id": prompt_id, "node_errors": {}}
                row = await self.submit(request_key=f"request-{kind}", request_payload={"kind": kind})
                self.upstream.history = history(filename)
                await self.service.reconcile()
                final = self.store.get("default", row["id"])
                self.assertEqual(final["status"], "succeeded")
                self.assertEqual(final["output_state"], "ready" if kind == "image" else "partial")
                self.assertTrue(all(key[0] == "default" for key in self.media.keys))

    async def test_animated_metadata_alongside_a_clean_output_still_reaches_ready(self) -> None:
        """GEN-003: SaveWEBM's real history entry carries an 'animated': [true]
        sibling key next to 'images' -- confirmed live against ComfyUI 0.35.0.
        The synthetic fixture never modeled this, so the scanner's own
        assumption that every list value is a list of file descriptors
        mis-counted that boolean flag as a malformed output and downgraded an
        otherwise fully successful save from 'ready' to 'partial'.
        """
        prompt_id = PROMPTS["video"]
        self.upstream.response = {"prompt_id": prompt_id, "node_errors": {}}
        row = await self.submit()
        self.upstream.history = {
            prompt_id: {
                "outputs": {
                    "11": {
                        "images": [{"filename": "clip.webm", "subfolder": "", "type": "output"}],
                        "animated": [True],
                    }
                },
                "status": {"status_str": "success", "completed": True},
            }
        }
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual((final["status"], final["output_state"]), ("succeeded", "ready"))

    async def test_fully_cached_success_requires_history_reconciliation(self) -> None:
        self.upstream.response = {"prompt_id": PROMPTS["cached"], "node_errors": {}}
        row = await self.submit()
        for event in events("fully_cached_run.events.jsonl"):
            self.service.process_event(event)
        self.assertEqual(self.store.get("default", row["id"])["status"], "running")

        # GEN-003: a fully cached job produces no execution_success websocket
        # event, and process_event() above never touches output_state -- the
        # frontend only ever refetches a generation when a matching event
        # tells it to (GenerationFormState._watch), so this poll alone must
        # publish one or the UI is stuck showing the last state it fetched.
        subscription = self.broker.subscribe("default")
        self.upstream.history = {
            PROMPTS["cached"]: {"outputs": {}, "status": {"status_str": "success", "completed": True}}
        }
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual((final["status"], final["output_state"]), ("succeeded", "unavailable"))
        self.assertEqual((await subscription.receive())["generation_id"], row["id"])

    async def test_reconcile_is_silent_when_nothing_moved(self) -> None:
        row = await self.submit()
        subscription = self.broker.subscribe("default")
        self.upstream.queue = {
            "queue_running": [[0, PROMPTS["image"], {}, {}]], "queue_pending": []
        }
        await self.service.reconcile()  # queued -> running: one event
        self.assertEqual((await subscription.receive())["generation_id"], row["id"])

        await self.service.reconcile()  # still running upstream: no change, no event
        self.assertTrue(subscription.queue.empty())

    async def test_accepted_node_errors_and_partial_results_survive(self) -> None:
        edge = history("history_edge_cases.json")
        accepted = edge["_submission_rejected"]["accepted_with_node_errors"]
        self.upstream.response = accepted
        row = await self.submit()
        self.upstream.history = {PROMPTS["partial"]: edge[PROMPTS["partial"]]}
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual((final["status"], final["output_state"]), ("failed", "partial"))
        self.assertIn("10", final["error"]["node_errors"])
        self.assertTrue(final["error"]["history_errors"])

    async def test_duplicate_history_is_idempotent(self) -> None:
        row = await self.submit()
        self.upstream.history = history("history_image.json")
        await self.service.reconcile()
        # Replayed terminal delivery through the same association code.
        self.service._apply_history(self.store.get("default", row["id"]),
                                    self.upstream.history[PROMPTS["image"]])
        self.assertEqual(len(self.media.keys), 1)

    async def test_disk_full_never_reruns_execution(self) -> None:
        self.media.fail = True
        row = await self.submit()
        self.upstream.history = history("history_image.json")
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["output_state"], "pending")
        self.assertIn("No space", final["error"]["output_capture"]["message"])
        self.assertEqual(self.upstream.submit_count, 1)

        self.media.fail = False
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual(final["output_state"], "ready")
        self.assertNotIn("output_capture", final["error"] or {})
        self.assertEqual(self.upstream.submit_count, 1)

    async def test_terminal_event_still_reconciles_partial_outputs(self) -> None:
        edge = history("history_edge_cases.json")
        self.upstream.response = {"prompt_id": PROMPTS["partial"], "node_errors": {}}
        row = await self.submit()
        error_event = events("partial_failure.events.jsonl")[-1]
        self.service.process_event(error_event)
        self.assertEqual(self.store.get("default", row["id"])["status"], "failed")

        self.upstream.history = {PROMPTS["partial"]: edge[PROMPTS["partial"]]}
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual((final["status"], final["output_state"]), ("failed", "partial"))

    async def test_uncertain_submission_recovers_by_marker_after_restart(self) -> None:
        self.upstream.raise_submit = True
        row = await self.submit()
        prompt_id = "recovered-upstream-id"
        self.upstream.raise_submit = False
        self.upstream.history = {
            prompt_id: {
                "prompt": [0, prompt_id, {}, {"simpleui_generation_id": row["id"]}, []],
                "outputs": {}, "status": {"status_str": "success", "completed": True},
            }
        }
        restarted = GenerationService(GenerationStore(self.db), self.upstream)
        await restarted.reconcile(history_retention={"default": False})
        final = self.store.get("default", row["id"])
        self.assertEqual((final["upstream_prompt_id"], final["status"]), (prompt_id, "succeeded"))
        self.assertIsNone(final["graph"])
        self.assertIsNone(final["effective_values"])
        self.assertEqual(self.upstream.submit_count, 1)

    async def test_uncertain_submission_recovers_from_queue_marker(self) -> None:
        self.upstream.raise_submit = True
        row = await self.submit()
        prompt_id = "queued-upstream-id"
        self.upstream.queue = {
            "queue_running": [],
            "queue_pending": [[0, prompt_id, {}, {"simpleui_generation_id": row["id"]}]],
        }
        await self.service.reconcile()
        final = self.store.get("default", row["id"])
        self.assertEqual((final["upstream_prompt_id"], final["status"]), (prompt_id, "queued"))
        self.assertEqual(self.upstream.submit_count, 1)

    async def test_unproven_restart_state_becomes_unknown_but_keeps_recovery_snapshot(self) -> None:
        self.upstream.raise_submit = True
        row = await self.submit()
        await self.service.reconcile(history_retention={"default": False})
        final = self.store.get("default", row["id"])
        self.assertEqual(final["status"], "unknown")
        self.assertIsNotNone(final["graph"])


class CancellationTests(GenerationTestCase):
    async def test_pending_cancel_targets_only_owned_prompt(self) -> None:
        row = await self.submit()
        await self.service.cancel("default", row["id"])
        self.assertEqual(self.upstream.cancelled, [PROMPTS["image"]])
        self.assertEqual(self.store.get("default", row["id"])["status"], "cancelled")
        with self.assertRaises(CancellationUnavailable):
            await self.service.cancel("other", row["id"])

    async def test_running_cancel_has_no_global_fallback(self) -> None:
        row = await self.submit()
        self.store.update("default", row["id"], status="running")
        with self.assertRaises(CancellationUnavailable):
            await self.service.cancel("default", row["id"])
        self.assertEqual(self.upstream.cancelled, [])

    async def _cancel_with_version(self, version, status):
        self.upstream.cancel_job = lambda pid: self._job_cancels.append(pid) or asyncio.sleep(0)
        self._job_cancels = []
        async def supported():  # evaluated per cancel, like the catalog-backed one in main.py
            return version_supports_targeted_interrupt(version)
        self.service._targeted_interrupt_supported = supported
        row = await self.submit()
        self.store.update("default", row["id"], status=status)
        return row

    async def test_supported_version_interrupts_on_both_paths(self) -> None:
        row = await self._cancel_with_version("0.38.0", "queued")
        await self.service.cancel("default", row["id"])
        self.assertEqual(self._job_cancels, [PROMPTS["image"]])
        row = await self._cancel_with_version("v0.38.1", "running")
        await self.service.cancel("default", row["id"])
        self.assertEqual(self._job_cancels, [PROMPTS["image"]])  # reset by helper; one call

    async def test_old_or_unknown_version_never_interrupts(self) -> None:
        for version in ("0.37.9", "0.35.0", None, "", "garbage"):
            row = await self._cancel_with_version(version, "queued")
            await self.service.cancel("default", row["id"])  # queue delete only
            self.assertEqual(self._job_cancels, [], version)
            self.assertEqual(self.store.get("default", row["id"])["status"], "cancelled")
            row = await self._cancel_with_version(version, "running")
            with self.assertRaises(CancellationUnavailable):
                await self.service.cancel("default", row["id"])
            self.assertEqual(self._job_cancels, [], version)

    def test_version_parsing_tolerates_suffixes(self) -> None:
        for good in ("0.38.0", "v0.38.1", "0.38.0+abc", "0.40", "1.0.0", "0.38.0.dev1"):
            self.assertTrue(version_supports_targeted_interrupt(good), good)
        for bad in ("0.37.99", "0.9", "x0.38.0", None):
            self.assertFalse(version_supports_targeted_interrupt(bad), bad)


if __name__ == "__main__":
    unittest.main()
