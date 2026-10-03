"""Supervised ComfyUI event-socket listener.

ComfyUI sends ``progress``/``executing``/``executed``/... JSON frames and binary
latent previews only to the websocket session whose ``clientId`` matches the one
prompts were submitted with (``GenerationService.client_id``). This task keeps
that session open, feeds JSON events to ``GenerationService.process_event`` and
forwards previews to the *owning* profile only.

Events stay advisory (docs/ARCHITECTURE.md): the task never raises out of
``run()``, reconnects with capped exponential backoff, and runs a reconcile
after every (re)connect so terminal states missed while disconnected are
recovered from ComfyUI's history.

Binary frame formats (big-endian, ComfyUI ``server.py``):

* ``type 4`` (PREVIEW_IMAGE_WITH_METADATA, requires the
  ``supports_preview_metadata`` feature flag, which we negotiate on connect)::

      u32 4 | u32 meta_len | meta_len bytes JSON {prompt_id, node_id,
      image_type: "image/jpeg"|"image/png", ...} | image bytes

* ``type 1`` (PREVIEW_IMAGE, legacy)::

      u32 1 | u32 image_kind (1=JPEG, 2=PNG) | image bytes

  carries no prompt id, so it is attributed to the prompt of the most recent
  ``execution_start``/``executing`` event, and dropped if there is none.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets.asyncio.client
from websockets.exceptions import WebSocketException

from ..comfy_client import ComfyClient

LOGGER = logging.getLogger("simpleui.comfy.events")

MAX_PREVIEW_BYTES = 1_500_000
PREVIEW_MIN_INTERVAL_S = 0.5  # <= 2 previews/s per generation
MAX_FRAME_BYTES = 8 * 1024 * 1024
_TERMINAL = ("execution_success", "execution_error", "execution_interrupted")


class ComfyListener:
    def __init__(
        self,
        service: Any,
        comfy: ComfyClient,
        *,
        previews_enabled: Callable[[str], bool] = lambda owner_id: True,
        on_connect: Callable[[], Awaitable[None]] | None = None,
        connect: Callable[..., Any] = websockets.asyncio.client.connect,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service, self.comfy = service, comfy
        self._previews_enabled, self._on_connect = previews_enabled, on_connect
        self._connect = connect
        self._backoff_initial_s, self._backoff_max_s = backoff_initial_s, backoff_max_s
        self._clock = clock
        self._current_prompt: str | None = None
        self._last_preview: dict[str, float] = {}
        self._pending_preview: dict[str, tuple[str, bytes]] = {}
        self._preview_timers: dict[str, asyncio.TimerHandle] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._clear_previews()

    def _clear_previews(self) -> None:
        for timer in self._preview_timers.values():
            timer.cancel()
        self._preview_timers.clear()
        self._pending_preview.clear()
        self._last_preview.clear()

    def _cancel_preview(self, prompt_id: str) -> None:
        timer = self._preview_timers.pop(prompt_id, None)
        if timer is not None:
            timer.cancel()
        self._pending_preview.pop(prompt_id, None)
        self._last_preview.pop(prompt_id, None)

    async def run(self) -> None:
        delay, down = self._backoff_initial_s, False
        while True:
            try:
                async with self._connect(
                    self.comfy.ws_url(self.service.client_id),
                    additional_headers=self.comfy.headers or None,
                    max_size=MAX_FRAME_BYTES,
                ) as ws:
                    if down:
                        LOGGER.info(
                            "ComfyUI event socket reconnected (%s)", self.comfy.safe_url
                        )
                    down, delay = False, self._backoff_initial_s
                    # Ask for previews that name their prompt (feature flags
                    # must be the first message).
                    await ws.send(
                        json.dumps(
                            {
                                "type": "feature_flags",
                                "data": {"supports_preview_metadata": True},
                            }
                        )
                    )
                    await self._resync()
                    async for message in ws:
                        self.handle(message)
            except asyncio.CancelledError:
                raise
            except (OSError, WebSocketException, ValueError) as exc:
                # One warning per outage; never the exception text (a URL with
                # credentials can appear in connection errors).
                if not down:
                    LOGGER.warning(
                        "ComfyUI event socket unavailable (%s: %s); retrying with backoff",
                        self.comfy.safe_url,
                        type(exc).__name__,
                    )
                down = True
            except Exception:
                LOGGER.exception("ComfyUI event listener failed; restarting it")
                down = True
            self._current_prompt = None
            self._clear_previews()
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff_max_s)

    async def _resync(self) -> None:
        if self._on_connect is None:
            return
        try:
            await self._on_connect()
        except Exception:
            LOGGER.exception("Reconcile after ComfyUI event socket connect failed")

    def handle(self, message: str | bytes) -> None:
        """Process one frame. Bad frames are dropped, never raised."""
        try:
            if isinstance(message, bytes):
                self._handle_preview(message)
                return
            event = json.loads(message)
            if not isinstance(event, dict):
                return
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            kind = event.get("type")
            if kind in ("execution_start", "executing") and isinstance(
                data.get("prompt_id"), str
            ):
                self._current_prompt = (
                    data["prompt_id"] if data.get("node", "") is not None else None
                )
            if kind in _TERMINAL or (kind == "executing" and data.get("node") is None):
                prompt_id = str(data.get("prompt_id"))
                self._cancel_preview(prompt_id)
                if self._current_prompt == prompt_id:
                    self._current_prompt = None
            self.service.process_event(event)
        except Exception:
            LOGGER.exception("Dropped a malformed ComfyUI event frame")

    def _handle_preview(self, frame: bytes) -> None:
        if len(frame) < 8 or len(frame) > MAX_PREVIEW_BYTES + 4096:
            return
        kind, second = struct.unpack(">II", frame[:8])
        if kind == 4:
            meta = json.loads(frame[8 : 8 + second])
            prompt_id, image = meta.get("prompt_id"), frame[8 + second :]
            mime = meta.get("image_type")
        elif kind == 1 and second in (1, 2):
            prompt_id, image = self._current_prompt, frame[8:]
            mime = "image/png" if second == 2 else "image/jpeg"
        else:
            return
        if not isinstance(prompt_id, str) or mime not in ("image/jpeg", "image/png"):
            return
        if not image or len(image) > MAX_PREVIEW_BYTES:
            return
        owned = self.service.store.owner_for_prompt(prompt_id)
        if owned is None or not self._previews_enabled(owned[0]):
            return
        remaining = PREVIEW_MIN_INTERVAL_S - (
            self._clock() - self._last_preview.get(prompt_id, -1e9)
        )
        if remaining > 0:
            self._pending_preview[prompt_id] = (mime, image)
            if prompt_id not in self._preview_timers:
                self._preview_timers[prompt_id] = asyncio.get_running_loop().call_later(
                    remaining,
                    self._flush_preview,
                    prompt_id,
                )
            return
        self._cancel_preview(prompt_id)
        self._publish_preview(prompt_id, mime, image)

    def _flush_preview(self, prompt_id: str) -> None:
        self._preview_timers.pop(prompt_id, None)
        pending = self._pending_preview.pop(prompt_id, None)
        if pending is not None:
            try:
                self._publish_preview(prompt_id, *pending)
            except Exception:
                LOGGER.exception("Dropped a pending ComfyUI preview frame")

    def _publish_preview(self, prompt_id: str, mime: str, image: bytes) -> None:
        owned = self.service.store.owner_for_prompt(prompt_id)
        if owned is None or not self._previews_enabled(owned[0]):
            return
        self._last_preview[prompt_id] = self._clock()
        owner_id, generation_id = owned
        self.service.events.publish(
            owner_id,
            {
                "generation_id": generation_id,
                "type": "preview",
                "data": {
                    "mime": mime,
                    "image": base64.b64encode(image).decode("ascii"),
                },
            },
        )
