"""GEN-004: event listener, previews, tolerant cancel, increment seeds."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import unittest

import httpx
from app.comfy_client import ComfyClient, ComfyUnavailable
from app.generations.listener import ComfyListener

from tests.generations.test_generations import PROMPTS, GenerationTestCase
from tests.generations.test_routes import GenerationRouteTestCase

PID = PROMPTS["image"]


def meta_frame(prompt_id, image=b"\xff\xd8jpeg", mime="image/jpeg"):
    meta = json.dumps({"prompt_id": prompt_id, "image_type": mime}).encode()
    return struct.pack(">II", 4, len(meta)) + meta + image


def legacy_frame(image=b"\x89PNGdata", kind=2):
    return struct.pack(">II", 1, kind) + image


def js(kind, **data):
    return json.dumps({"type": kind, "data": data})


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_calls_accept_an_empty_upstream_body(self) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200)  # ComfyUI replies with no body at all

        client = ComfyClient(
            "http://127.0.0.1:8188", transport=httpx.MockTransport(handler)
        )
        await client.cancel_pending("p1")
        await client.cancel_job("p1")
        self.assertEqual(
            seen, [("/queue", {"delete": ["p1"]}), ("/interrupt", {"prompt_id": "p1"})]
        )
        with self.assertRaises(ComfyUnavailable):  # post_json still wants JSON
            await client.post_json("/queue", {})
        await client.aclose()

    async def test_get_file_reads_temp_results_through_view(self) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(request.url.params))
            if request.url.params["filename"] == "gone.png":
                return httpx.Response(404)
            return httpx.Response(
                200, content=b"bytes", headers={"content-type": "image/png"}
            )

        client = ComfyClient(
            "http://127.0.0.1:8188", transport=httpx.MockTransport(handler)
        )
        self.assertEqual(
            await client.get_file("a b.png", "sub", "temp"), (b"bytes", "image/png")
        )
        self.assertIsNone(await client.get_file("gone.png", "", "temp"))
        self.assertEqual(
            seen[0], {"filename": "a b.png", "subfolder": "sub", "type": "temp"}
        )
        await client.aclose()

    async def test_ws_url_follows_scheme_and_keeps_client_id(self) -> None:
        a, b = ComfyClient("http://h:8188"), ComfyClient("https://h/comfy/")
        self.assertEqual(a.ws_url("simpleui"), "ws://h:8188/ws?clientId=simpleui")
        self.assertEqual(b.ws_url("a b"), "wss://h/comfy/ws?clientId=a+b")
        await a.aclose()
        await b.aclose()


class FakeSocket:
    def __init__(self, messages, error=None):
        self.messages, self.sent, self.error = messages, [], error

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def __aiter__(self):
        for message in self.messages:
            yield message
        if self.error:
            raise self.error
        await asyncio.sleep(3600)  # stay connected until cancelled


class FakeConnect:
    """Each call consumes one script entry: an exception to raise or a FakeSocket."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.script.pop(0) if self.script else FakeSocket([])
        if isinstance(item, Exception):
            raise item

        class Ctx:
            async def __aenter__(_):
                return item

            async def __aexit__(_, *exc):
                return False

        return Ctx()


class ListenerTests(GenerationTestCase):
    def listener(self, **kwargs):
        kwargs.setdefault("backoff_initial_s", 0.0)
        kwargs.setdefault("backoff_max_s", 0.0)
        return ComfyListener(self.service, self.comfy, **kwargs)

    def setUp(self) -> None:
        super().setUp()
        self.comfy = ComfyClient(
            "http://user:secret@127.0.0.1:8188", headers={"X-Proxy": "k"}
        )

    async def asyncSetUp(self) -> None:
        self.row = await self.submit()
        self.broker.pending_cap = 32
        self.sub = self.broker.subscribe("default")
        self.other = self.broker.subscribe("other")
        self.addAsyncCleanup(self.comfy.aclose)

    def drain(self, sub):
        out = []
        while not sub.queue.empty():
            out.append(sub.queue.get_nowait())
        return out

    async def test_listener_feeds_process_event_and_reconnects_with_resync(
        self,
    ) -> None:
        resyncs = []

        async def resync():
            resyncs.append(1)

        connect = FakeConnect(
            OSError("down"),
            OSError("down"),
            OSError("down"),
            FakeSocket(
                [js("execution_start", prompt_id=PID)], error=ConnectionResetError()
            ),
            FakeSocket([js("progress", prompt_id=PID, value=1, max=2)]),
        )
        listener = self.listener(connect=connect, on_connect=resync)
        with self.assertLogs("simpleui.comfy.events", "WARNING") as logs:
            listener.start()
            for _ in range(300):
                await asyncio.sleep(0.01)
                if len(connect.calls) >= 5 and len(resyncs) >= 2:
                    break
            await listener.stop()
        self.assertEqual(len(resyncs), 2)  # every successful connect reconciles
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(
            len(warnings), 2
        )  # one per outage (3 retries + 1 dropped socket), not per retry
        self.assertNotIn("secret", " ".join(r.getMessage() for r in logs.records))
        url, kwargs = connect.calls[0]
        self.assertTrue(url.endswith("/ws?clientId=simpleui"))
        self.assertEqual(kwargs["additional_headers"], {"X-Proxy": "k"})
        self.assertEqual(self.store.get("default", self.row["id"])["status"], "running")
        kinds = [e["type"] for e in self.drain(self.sub)]
        self.assertEqual(kinds, ["execution_start", "progress"])
        self.assertEqual(self.drain(self.other), [])

    async def test_listener_negotiates_preview_metadata_flag(self) -> None:
        sock = FakeSocket([])
        listener = self.listener(connect=FakeConnect(sock))
        listener.start()
        await asyncio.sleep(0.05)
        await listener.stop()
        self.assertEqual(
            sock.sent[0],
            {"type": "feature_flags", "data": {"supports_preview_metadata": True}},
        )

    async def test_malformed_frames_are_dropped_not_raised(self) -> None:
        listener = self.listener()
        bad_frames = (
            "not json",
            "[]",
            b"",
            b"\x00\x00\x00\x04\x00\x00\x00\x09{bad",
            js("progress", prompt_id=7),
        )
        with self.assertLogs("simpleui.comfy.events", "ERROR"):
            for bad in bad_frames:
                listener.handle(bad)
        self.assertEqual(self.drain(self.sub), [])

    async def test_metadata_preview_reaches_only_the_owner(self) -> None:
        listener = self.listener()
        listener.handle(meta_frame(PID))
        [event] = self.drain(self.sub)
        self.assertEqual(event["generation_id"], self.row["id"])
        self.assertEqual(event["type"], "preview")
        self.assertEqual(event["data"]["mime"], "image/jpeg")
        self.assertEqual(base64.b64decode(event["data"]["image"]), b"\xff\xd8jpeg")
        self.assertEqual(self.drain(self.other), [])

    async def test_legacy_preview_needs_an_executing_prompt_to_attribute(self) -> None:
        listener = self.listener()
        listener.handle(legacy_frame())
        self.assertEqual(self.drain(self.sub), [])  # unattributable: dropped
        listener.handle(js("execution_start", prompt_id=PID))
        listener.handle(legacy_frame())
        events = self.drain(self.sub)
        self.assertEqual([e["type"] for e in events], ["execution_start", "preview"])
        self.assertEqual(events[1]["data"]["mime"], "image/png")

    async def test_unknown_prompt_oversize_and_disabled_previews_are_dropped(
        self,
    ) -> None:
        listener = self.listener(previews_enabled=lambda owner: False)
        listener.handle(meta_frame(PID))
        self.assertEqual(self.drain(self.sub), [])
        listener = self.listener()
        listener.handle(meta_frame("not-ours"))
        listener.handle(meta_frame(PID, image=b"x" * 1_600_000))
        self.assertEqual(self.drain(self.sub), [])

    async def test_previews_are_throttled_per_generation(self) -> None:
        now = [0.0]
        listener = self.listener(clock=lambda: now[0])
        self.addAsyncCleanup(listener.stop)
        for t in (0.0, 0.1, 0.4, 0.6, 0.7, 1.2):
            now[0] = t
            listener.handle(meta_frame(PID))
        self.assertEqual(len(self.drain(self.sub)), 3)  # at t = 0.0, 0.6, 1.2

    async def test_preview_burst_flushes_only_the_latest_frame(self) -> None:
        listener = self.listener()
        self.addAsyncCleanup(listener.stop)
        listener.handle(meta_frame(PID, b"first"))
        listener.handle(meta_frame(PID, b"middle"))
        listener.handle(meta_frame(PID, b"last"))
        self.assertEqual(len(self.drain(self.sub)), 1)
        await asyncio.sleep(0.55)
        [event] = self.drain(self.sub)
        self.assertEqual(base64.b64decode(event["data"]["image"]), b"last")
        self.assertEqual(self.drain(self.other), [])
        self.assertEqual(listener._preview_timers, {})

    async def test_terminal_events_and_stop_cancel_pending_previews(self) -> None:
        for kind in (
            "execution_success",
            "execution_error",
            "execution_interrupted",
            "executing",
            "stop",
        ):
            with self.subTest(kind=kind):
                listener = self.listener()
                listener.handle(meta_frame(PID, b"first"))
                listener.handle(meta_frame(PID, b"pending"))
                if kind == "stop":
                    await listener.stop()
                else:
                    listener.handle(js(kind, prompt_id=PID, node=None))
                self.drain(self.sub)
                await asyncio.sleep(0.55)
                self.assertEqual(self.drain(self.sub), [])
                self.assertEqual(listener._pending_preview, {})
                self.assertEqual(listener._preview_timers, {})

    async def test_pending_preview_rechecks_profile_preference(self) -> None:
        enabled = [True]
        listener = self.listener(previews_enabled=lambda owner: enabled[0])
        self.addAsyncCleanup(listener.stop)
        listener.handle(meta_frame(PID))
        listener.handle(meta_frame(PID, b"pending"))
        self.drain(self.sub)
        enabled[0] = False
        await asyncio.sleep(0.55)
        self.assertEqual(self.drain(self.sub), [])

    async def test_final_executing_event_does_not_revive_a_finished_generation(
        self,
    ) -> None:
        self.store.update("default", self.row["id"], status="succeeded")
        self.service.process_event(
            {"type": "executing", "data": {"node": None, "prompt_id": PID}}
        )
        self.assertEqual(
            self.store.get("default", self.row["id"])["status"], "succeeded"
        )


class IncrementSeedTests(GenerationRouteTestCase):
    def test_increment_advances_across_submissions_and_follows_explicit_edits(
        self,
    ) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        seeds = []
        for key in ("a", "b", "c"):
            body = self.submit(client, workflow_id, key, seed_policy="increment").json()
            seeds.append(body["effective_values"]["5:seed"])
        self.assertEqual(seeds, ["123456790", "123456791", "123456792"])
        edited = self.submit(
            client, workflow_id, "d", edits={"5:seed": "500"}, seed_policy="increment"
        ).json()
        self.assertEqual(edited["effective_values"]["5:seed"], "500")
        after = self.submit(client, workflow_id, "e", seed_policy="increment").json()
        self.assertEqual(after["effective_values"]["5:seed"], "501")
        other = self.import_graph(
            client, name="Other"
        )  # a separate workflow starts from its own import
        first = self.submit(client, other, "f", seed_policy="increment").json()
        self.assertEqual(first["effective_values"]["5:seed"], "123456790")


class CancelRouteTests(GenerationRouteTestCase):
    def test_upstream_failure_during_cancel_is_503_not_500(self) -> None:
        async def down(prompt_id):
            raise ComfyUnavailable("upstream_unreachable", "ComfyUI is not reachable")

        self.upstream.cancel_pending = down
        client = self.local_client()
        workflow_id = self.import_graph(client)
        generation_id = self.submit(client, workflow_id, "req-1").json()["id"]
        response = self.post(client, f"/api/generations/{generation_id}/cancel")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "upstream_unavailable")


if __name__ == "__main__":
    unittest.main()
