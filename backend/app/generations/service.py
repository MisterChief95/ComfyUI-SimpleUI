"""ComfyUI-facing generation orchestration with no blind retries."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any

from ..comfy_client import ComfyRejected
from ..events import EventBroker
from .store import GenerationStore

LOGGER = logging.getLogger("simpleui.generations")


class CancellationUnavailable(ValueError):
    pass


# Older ComfyUI ignores prompt_id on POST /interrupt and stops whatever is
# running (possibly another profile's job). Targeted interrupt was verified live
# on 0.38.0 (docs/API.md, cancel semantics); anything older or unparseable is unsafe.
MIN_TARGETED_INTERRUPT_VERSION = (0, 38, 0)


def version_supports_targeted_interrupt(version: str | None) -> bool:
    """True when ``version`` ("0.38.0", "v0.38.1", "0.38.0+abc") is >= the verified minimum."""
    match = re.match(r"\s*v?(\d+)\.(\d+)(?:\.(\d+))?", version or "")
    return (
        bool(match)
        and tuple(int(p or 0) for p in match.groups()) >= MIN_TARGETED_INTERRUPT_VERSION
    )


def fingerprint_request(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class GenerationService:
    """One process owns submission, event correlation, and reconciliation."""

    def __init__(
        self,
        store: GenerationStore,
        upstream: Any,
        *,
        media: Any | None = None,
        events: EventBroker | None = None,
        client_id: str = "simpleui",
        global_pending_cap: int = 8,
        profile_pending_cap: int = 8,
        targeted_interrupt_supported: Callable[[], Awaitable[bool]] | None = None,
        reconcile_cooldown_s: float = 1.5,
    ) -> None:
        self.store, self.upstream, self.media = store, upstream, media
        self.events = events or EventBroker()
        self.client_id = client_id
        self.global_pending_cap = global_pending_cap
        self.profile_pending_cap = profile_pending_cap
        # Evaluated at cancel time: the catalog (and its recorded version) may refresh.
        self._targeted_interrupt_supported = targeted_interrupt_supported
        self._reconcile_cooldown_s = reconcile_cooldown_s
        self._next_reconcile_s = 0.0

    async def submit(
        self,
        owner_id: str,
        *,
        request_key: str,
        request_payload: dict[str, Any],
        resolve: Any,
        workflow_id: str | None = None,
        workflow_revision: int | None = None,
        mapping_revision: int | None = None,
        store_history: bool = True,
        input_records: list[tuple[str, str, str, str | None, str]] | None = None,
    ) -> dict[str, Any]:
        accepted = self.store.accept(
            owner_id,
            request_key=request_key,
            fingerprint=fingerprint_request(request_payload),
            resolve=resolve,
            workflow_id=workflow_id,
            workflow_revision=workflow_revision,
            mapping_revision=mapping_revision,
            input_records=input_records or [],
            global_pending_cap=self.global_pending_cap,
            profile_pending_cap=self.profile_pending_cap,
        )
        if not accepted.is_new:
            return accepted.row

        generation_id = accepted.row["id"]
        extra_data = {"simpleui_generation_id": generation_id}
        try:
            response = await self.upstream.submit_prompt(
                accepted.row["graph"], client_id=self.client_id, extra_data=extra_data
            )
        except ComfyRejected as exc:
            # A 400 validation answer is definite: nothing was queued.
            rejection = {
                **exc.rejection,
                "node_errors": [
                    {**e, "field": f"{e['node_id']}.inputs.{e['input_name']}"}
                    if "input_name" in e
                    else e
                    for e in exc.rejection["node_errors"]
                ],
            }
            self.store.update(
                owner_id,
                generation_id,
                status="failed",
                output_state="unavailable",
                error={"rejection": rejection},
            )
            return self.store.get(owner_id, generation_id)
        except Exception:  # noqa: BLE001 - any failure leaves the outcome unknown
            # The request may have reached ComfyUI. Retrying here could execute
            # it twice, so only reconciliation may move this record forward.
            self.store.update(owner_id, generation_id, status="submission_unknown")
            return self.store.get(owner_id, generation_id)

        prompt_id = response.get("prompt_id") if isinstance(response, dict) else None
        node_errors = (
            response.get("node_errors") if isinstance(response, dict) else None
        )
        if not prompt_id:
            self.store.update(
                owner_id,
                generation_id,
                status="failed",
                output_state="unavailable",
                error={"submission": response},
            )
        else:
            error = {"node_errors": node_errors} if node_errors else None
            self.store.update(
                owner_id,
                generation_id,
                status="queued",
                prompt_id=str(prompt_id),
                error=error,
            )
        # store_history is intentionally acted on only after terminal
        # reconciliation and output association, never at submission time.
        row = self.store.get(owner_id, generation_id)
        row["store_history"] = store_history
        return row

    def process_event(self, event: dict[str, Any]) -> bool:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str):
            return False
        owned = self.store.owner_for_prompt(prompt_id)
        if owned is None:
            return False
        owner_id, generation_id = owned
        kind = event.get("type")
        # {"node": null} closes a job (history decides how it ended): it is not
        # a sign the job is running, and every progress tick after the first
        # would otherwise be a redundant write.
        if kind in (
            "execution_start",
            "executing",
            "progress",
            "execution_cached",
        ) and not (kind == "executing" and data.get("node") is None):
            row = self.store.get(owner_id, generation_id)
            if row is not None and row["status"] in (
                "submitting",
                "submission_unknown",
                "queued",
            ):
                self.store.update(owner_id, generation_id, status="running")
        elif kind == "execution_interrupted":
            self.store.update(
                owner_id, generation_id, status="cancelled", error={"execution": data}
            )
        elif kind == "execution_error":
            self.store.update(
                owner_id, generation_id, status="failed", error={"execution": data}
            )
        # execution_success is advisory. History is the terminal authority,
        # including fully cached jobs with no executed-node events.
        # The UI only needs which node finished; node output (e.g. Show Text prompt
        # text) is never displayed, so it is not sent to the browser.
        relayed = (
            {k: v for k, v in data.items() if k != "output"}
            if kind == "executed"
            else data
        )
        self.events.publish(
            owner_id, {"generation_id": generation_id, "type": kind, "data": relayed}
        )
        return True

    async def reconcile(
        self, *, history_retention: dict[str, bool] | None = None
    ) -> None:
        history_retention = history_retention or {}
        active_rows = self.store.active()
        if not active_rows:
            # Skip the upstream round trip entirely: an open events socket now
            # calls this on every RECONCILE_POLL_S tick (app/events/routes.py),
            # so idle polling must stay cheap.
            return
        queue = await self.upstream.get_queue()
        history = await self.upstream.get_history()
        queue_entries = self._queue_entries(queue)
        history_entries = history if isinstance(history, dict) else {}

        for row in active_rows:
            before = (row["status"], row["output_state"])
            prompt_id = row.get("upstream_prompt_id")
            if not prompt_id:
                prompt_id = self._find_marker(row["id"], queue_entries, history_entries)
                if prompt_id:
                    self.store.update(row["owner_id"], row["id"], prompt_id=prompt_id)
            if prompt_id and prompt_id in history_entries:
                associated = await self._apply_history(row, history_entries[prompt_id])
                if not history_retention.get(row["owner_id"], True) and associated:
                    self.store.purge_snapshot(row["owner_id"], row["id"])
            elif prompt_id and prompt_id in queue_entries:
                self.store.update(
                    row["owner_id"], row["id"], status=queue_entries[prompt_id][0]
                )
            elif row["status"] not in ("failed", "cancelled", "interrupted", "unknown"):
                self.store.update(row["owner_id"], row["id"], status="unknown")

            # process_event() publishes for the live ComfyUI websocket, but a
            # fully cached job (or any state this poll alone discovers, such
            # as an upstream restart recovery) produces no such event -- this
            # reconciliation path is otherwise silent, and the frontend only
            # refetches a generation when an event tells it to (GEN-003: a
            # generation that only ever finishes through history looked stuck
            # at output_state 'pending' in the UI even once the backend knew
            # better). Publish once here whenever this poll actually moved
            # the row, regardless of which branch above did it.
            after = self.store.get(row["owner_id"], row["id"])
            if after is not None and (after["status"], after["output_state"]) != before:
                self.events.publish(
                    row["owner_id"],
                    {"generation_id": row["id"], "type": "reconciled", "data": {}},
                )

    async def reconcile_if_due(
        self, *, history_retention: dict[str, bool] | None = None
    ) -> None:
        """Best-effort ``reconcile``, throttled and never raising.

        Called from read routes (list/detail) instead of running a perpetual
        background poll loop -- this app has no persistent poller anywhere
        else either (see app/catalog/service.py's cooldown, not a loop), and a
        local desktop UI is reliably polling to watch progress whenever it
        matters. A one-shot ``reconcile()`` also runs at process startup for
        restart recovery (app/main.py).
        """
        now = time.monotonic()
        if now < self._next_reconcile_s:
            return
        self._next_reconcile_s = now + self._reconcile_cooldown_s
        try:
            await self.reconcile(history_retention=history_retention)
        except Exception:
            LOGGER.exception("Generation reconciliation failed")

    async def cancel(self, owner_id: str, generation_id: str) -> None:
        row = self.store.get(owner_id, generation_id)
        if row is None or not row.get("upstream_prompt_id"):
            raise CancellationUnavailable("generation is not safely cancellable")
        prompt_id = row["upstream_prompt_id"]
        targeted = hasattr(self.upstream, "cancel_job") and bool(
            self._targeted_interrupt_supported
            and await self._targeted_interrupt_supported()
        )
        if row["status"] == "queued" and hasattr(self.upstream, "cancel_pending"):
            await self.upstream.cancel_pending(prompt_id)
            if targeted:
                # Our "queued" may be stale: the job could have just started,
                # which deleting from the queue does not stop. A targeted
                # interrupt is a no-op unless this very prompt is running.
                await self.upstream.cancel_job(prompt_id)
            # Never ran, so no outputs will ever appear.
            self.store.update(
                owner_id, generation_id, status="cancelled", output_state="unavailable"
            )
            return
        if row["status"] == "running" and targeted:
            await self.upstream.cancel_job(prompt_id)
            return
        raise CancellationUnavailable(
            "this ComfyUI installation has no verified per-job cancellation"
        )

    async def _apply_history(self, row: dict[str, Any], entry: Any) -> bool:
        entry = entry if isinstance(entry, dict) else {}
        outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        messages = (
            status.get("messages") if isinstance(status.get("messages"), list) else []
        )
        kinds = {item[0] for item in messages if isinstance(item, list) and item}
        if "execution_interrupted" in kinds:
            execution = "cancelled"
        elif status.get("status_str") == "success" and status.get("completed") is True:
            execution = "succeeded"
        else:
            execution = "failed"

        saved = failures = unknown = 0
        for node_id, output in outputs.items():
            if not isinstance(output, dict):
                unknown += 1
                continue
            recognized = False
            text_only = all(
                k == "text"
                and isinstance(v, list)
                and all(isinstance(t, str) for t in v)
                for k, v in output.items()
            )
            for key, descriptors in output.items():
                if not isinstance(descriptors, list):
                    continue
                # A node can attach non-file metadata alongside its real
                # outputs -- SaveWEBM's real ComfyUI history entry carries
                # "animated": [true] next to "images", confirmed against a
                # live install (GEN-003; the earlier synthetic fixture never
                # modeled this and so never caught it). A list with no
                # dict-shaped, filenamed entry at all is not a claimed file
                # output, so it must not count as an unrecognized one either.
                if not any(
                    isinstance(d, dict) and "filename" in d for d in descriptors
                ):
                    continue
                for ordinal, descriptor in enumerate(descriptors):
                    if not isinstance(descriptor, dict) or not isinstance(
                        descriptor.get("filename"), str
                    ):
                        unknown += 1
                        continue
                    recognized = key in ("images", "gifs", "videos", "audio")
                    if not recognized:
                        unknown += 1
                        continue
                    preview = descriptor.get("type") == "temp"
                    subfolder = str(descriptor.get("subfolder") or "")
                    path = PurePosixPath(subfolder, descriptor["filename"]).as_posix()
                    if self.media is None:
                        saved += 1
                        continue
                    try:
                        if preview:
                            # Preview Image results live in ComfyUI's temp
                            # folder, which the API does not disclose: read
                            # them through /view and keep a private copy.
                            fetched = await self._fetch_temp(descriptor, subfolder)
                            if fetched is None:
                                unknown += 1  # ComfyUI already cleaned it up
                                continue
                            self.media.capture_preview(
                                row["owner_id"],
                                row["id"],
                                path,
                                fetched,
                                output_node=str(node_id),
                                ordinal=ordinal,
                            )
                        else:
                            self.media.capture_output(
                                row["owner_id"],
                                row["id"],
                                path,
                                output_node=str(node_id),
                                ordinal=ordinal,
                                preview=False,
                            )
                        saved += 1
                    except Exception as exc:  # noqa: BLE001 - recorded on the row, not raised
                        failures += 1
                        self.store.update(
                            row["owner_id"],
                            row["id"],
                            error={"output_capture": {"message": str(exc)}},
                        )
            if output and not recognized and not text_only:
                unknown += 1

        if saved and not failures and not unknown and execution == "succeeded":
            output_state = "ready"
        elif saved:
            output_state = "partial"
        elif failures:
            output_state = "pending"
        elif unknown:
            output_state = "unavailable"
        else:
            output_state = "unavailable"
        runtime_errors = [
            item[1]
            for item in messages
            if isinstance(item, list)
            and len(item) > 1
            and item[0] in ("execution_error", "execution_interrupted")
        ]
        error = {"history_errors": runtime_errors} if runtime_errors else None
        self.store.update(
            row["owner_id"],
            row["id"],
            status=execution,
            output_state=output_state,
            error=error,
        )
        if failures == 0:
            self.store.clear_error(row["owner_id"], row["id"], "output_capture")
        return failures == 0 and unknown == 0

    async def _fetch_temp(
        self, descriptor: dict[str, Any], subfolder: str
    ) -> bytes | None:
        get_file = getattr(self.upstream, "get_file", None)
        if get_file is None:
            raise RuntimeError("ComfyUI client cannot read temp results")
        result = await get_file(descriptor["filename"], subfolder, "temp")
        return None if result is None else result[0]

    @staticmethod
    def _queue_entries(queue: Any) -> dict[str, tuple[str, Any]]:
        result: dict[str, tuple[str, Any]] = {}
        if not isinstance(queue, dict):
            return result
        for key, state in (("queue_running", "running"), ("queue_pending", "queued")):
            for entry in queue.get(key, []):
                if isinstance(entry, list) and len(entry) > 1:
                    result[str(entry[1])] = (state, entry)
                elif isinstance(entry, dict) and entry.get("prompt_id"):
                    result[str(entry["prompt_id"])] = (state, entry)
        return result

    @staticmethod
    def _find_marker(
        local_id: str, queue: dict[str, tuple[str, Any]], history: dict[str, Any]
    ) -> str | None:
        for prompt_id, (_, entry) in queue.items():
            if local_id in json.dumps(entry, separators=(",", ":")):
                return prompt_id
        for prompt_id, entry in history.items():
            if local_id in json.dumps(entry, separators=(",", ":")):
                return str(prompt_id)
        return None
