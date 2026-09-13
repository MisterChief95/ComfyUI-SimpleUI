"""ComfyUI-facing generation orchestration with no blind retries."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import PurePosixPath
from typing import Any

from ..events import EventBroker
from .store import GenerationBusy, GenerationConflict, GenerationStore

LOGGER = logging.getLogger("simpleui.generations")


class CancellationUnavailable(ValueError):
    pass


def fingerprint_request(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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
        per_job_cancel: bool = False,
        reconcile_cooldown_s: float = 1.5,
    ) -> None:
        self.store, self.upstream, self.media = store, upstream, media
        self.events = events or EventBroker()
        self.client_id = client_id
        self.global_pending_cap = global_pending_cap
        self.profile_pending_cap = profile_pending_cap
        self.per_job_cancel = per_job_cancel
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
    ) -> dict[str, Any]:
        accepted = self.store.accept(
            owner_id,
            request_key=request_key,
            fingerprint=fingerprint_request(request_payload),
            resolve=resolve,
            workflow_id=workflow_id,
            workflow_revision=workflow_revision,
            mapping_revision=mapping_revision,
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
        except Exception:
            # The request may have reached ComfyUI. Retrying here could execute
            # it twice, so only reconciliation may move this record forward.
            self.store.update(owner_id, generation_id, status="submission_unknown")
            return self.store.get(owner_id, generation_id)

        prompt_id = response.get("prompt_id") if isinstance(response, dict) else None
        node_errors = response.get("node_errors") if isinstance(response, dict) else None
        if not prompt_id:
            self.store.update(
                owner_id, generation_id, status="failed", output_state="unavailable",
                error={"submission": response},
            )
        else:
            error = {"node_errors": node_errors} if node_errors else None
            self.store.update(
                owner_id, generation_id, status="queued", prompt_id=str(prompt_id), error=error
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
        if kind in ("execution_start", "executing", "progress", "execution_cached"):
            self.store.update(owner_id, generation_id, status="running")
        elif kind == "execution_interrupted":
            self.store.update(owner_id, generation_id, status="cancelled", error={"execution": data})
        elif kind == "execution_error":
            self.store.update(owner_id, generation_id, status="failed", error={"execution": data})
        # execution_success is advisory. History is the terminal authority,
        # including fully cached jobs with no executed-node events.
        self.events.publish(owner_id, {"generation_id": generation_id, "type": kind, "data": data})
        return True

    async def reconcile(self, *, history_retention: dict[str, bool] | None = None) -> None:
        history_retention = history_retention or {}
        queue = await self.upstream.get_queue()
        history = await self.upstream.get_history()
        queue_entries = self._queue_entries(queue)
        history_entries = history if isinstance(history, dict) else {}

        for row in self.store.active():
            prompt_id = row.get("upstream_prompt_id")
            if not prompt_id:
                prompt_id = self._find_marker(row["id"], queue_entries, history_entries)
                if prompt_id:
                    self.store.update(row["owner_id"], row["id"], prompt_id=prompt_id)
            if prompt_id and prompt_id in history_entries:
                associated = self._apply_history(row, history_entries[prompt_id])
                if not history_retention.get(row["owner_id"], True) and associated:
                    self.store.purge_snapshot(row["owner_id"], row["id"])
            elif prompt_id and prompt_id in queue_entries:
                self.store.update(row["owner_id"], row["id"], status=queue_entries[prompt_id][0])
            elif row["status"] not in ("failed", "cancelled", "interrupted", "unknown"):
                self.store.update(row["owner_id"], row["id"], status="unknown")

    async def reconcile_if_due(self, *, history_retention: dict[str, bool] | None = None) -> None:
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
        if row["status"] == "queued" and hasattr(self.upstream, "cancel_pending"):
            await self.upstream.cancel_pending(prompt_id)
            self.store.update(owner_id, generation_id, status="cancelled")
            return
        if row["status"] == "running" and self.per_job_cancel and hasattr(self.upstream, "cancel_job"):
            await self.upstream.cancel_job(prompt_id)
            return
        raise CancellationUnavailable("this ComfyUI installation has no verified per-job cancellation")

    def _apply_history(self, row: dict[str, Any], entry: Any) -> bool:
        entry = entry if isinstance(entry, dict) else {}
        outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        messages = status.get("messages") if isinstance(status.get("messages"), list) else []
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
            for key, descriptors in output.items():
                if not isinstance(descriptors, list):
                    continue
                for ordinal, descriptor in enumerate(descriptors):
                    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("filename"), str):
                        unknown += 1
                        continue
                    recognized = key in ("images", "gifs", "videos")
                    if not recognized:
                        unknown += 1
                        continue
                    preview = descriptor.get("type") == "temp"
                    path = PurePosixPath(str(descriptor.get("subfolder") or ""), descriptor["filename"]).as_posix()
                    if preview:
                        continue
                    if self.media is None:
                        saved += 1
                        continue
                    try:
                        self.media.capture_output(
                            row["owner_id"], row["id"], path,
                            output_node=str(node_id), ordinal=ordinal, preview=False,
                        )
                        saved += 1
                    except Exception as exc:
                        failures += 1
                        self.store.update(
                            row["owner_id"], row["id"],
                            error={"output_capture": {"message": str(exc)}},
                        )
            if output and not recognized:
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
        runtime_errors = [item[1] for item in messages
                          if isinstance(item, list) and len(item) > 1
                          and item[0] in ("execution_error", "execution_interrupted")]
        error = {"history_errors": runtime_errors} if runtime_errors else None
        self.store.update(
            row["owner_id"], row["id"], status=execution, output_state=output_state, error=error
        )
        if failures == 0:
            self.store.clear_error(row["owner_id"], row["id"], "output_capture")
        return failures == 0 and unknown == 0

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
