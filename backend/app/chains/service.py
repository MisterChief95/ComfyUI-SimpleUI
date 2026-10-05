"""Chain definitions and the orchestrator that advances a run stage by stage.

A run never advances from a preview: the next stage starts only when the
previous generation succeeded and its output was captured (``output_state``
``ready``) into media. Anything else (failure, cancellation, an uncertain
submission, a missing output) pauses the run with a reason; ``resume`` then
retries the current stage under a new attempt key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from pydantic import Field

from ..contracts import Id, Model
from ..generations.store import ACTIVE
from ..storage.db import Database, in_thread
from ..storage.repository import new_id, now_ms


class ChainError(ValueError):
    """A client-safe problem with a chain definition or run."""


class StageLink(Model):
    #: Binding id of a file control in this stage's workflow.
    binding_id: Id
    #: Output node of the previous stage and the ordinal of its file output.
    from_output_node: Id
    ordinal: int = Field(default=0, ge=0)


class Stage(Model):
    workflow_id: Id
    #: Encoded edits, like ``SubmitRequest.edits``.
    edits: dict[str, str | bool | int | float] = Field(default_factory=dict)
    links: list[StageLink] = Field(default_factory=list, max_length=16)
    #: Pinned at save time; a run pauses if the workflow has changed since.
    workflow_revision: int | None = None


class ChainDefinition(Model):
    name: str = Field(min_length=1, max_length=200)
    stages: list[Stage] = Field(min_length=1, max_length=8)


#: ``submit(owner_id, workflow_id=, request_key=, edits=, inputs=)`` -> generation.
SubmitStage = Callable[..., Awaitable[Any]]


class ChainService:
    def __init__(
        self,
        db: Database,
        repository: Any,
        generations: Any,
        submit: SubmitStage,
    ) -> None:
        self.db, self.repository, self.generations = db, repository, generations
        self._submit = submit
        self._locks: dict[str, asyncio.Lock] = {}

    # --- definitions ------------------------------------------------------

    @staticmethod
    def _chain(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["stages"] = json.loads(item.pop("stages_json"))
        return item

    def create(self, owner_id: str, definition: ChainDefinition) -> dict[str, Any]:
        stages = []
        for index, stage in enumerate(definition.stages):
            workflow = self.repository.get_workflow(owner_id, stage.workflow_id)
            if workflow is None:
                raise ChainError(f"Stage {index + 1}: workflow was not found.")
            if index == 0 and stage.links:
                raise ChainError("The first stage has no previous output to link.")
            bindings = [link.binding_id for link in stage.links]
            if len(set(bindings)) != len(bindings):
                raise ChainError(f"Stage {index + 1}: a file input is linked twice.")
            stages.append(
                stage.model_copy(
                    update={"workflow_revision": int(workflow["current_revision"])}
                ).model_dump()
            )
        chain_id, stamp = new_id(), now_ms()
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO chains (id, owner_id, name, stages_json, created_ms, updated_ms)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chain_id,
                    owner_id,
                    definition.name,
                    json.dumps(stages, separators=(",", ":")),
                    stamp,
                    stamp,
                ),
            )
        return self.get(owner_id, chain_id)  # type: ignore[return-value]

    def get(self, owner_id: str, chain_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM chains WHERE id = ? AND owner_id = ?", (chain_id, owner_id)
        )
        return self._chain(row) if row else None

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        return [
            self._chain(r)
            for r in self.db.query(
                "SELECT * FROM chains WHERE owner_id = ? ORDER BY created_ms DESC",
                (owner_id,),
            )
        ]

    def delete(self, owner_id: str, chain_id: str) -> bool:
        with self.db.write() as conn:
            return (
                conn.execute(
                    "DELETE FROM chains WHERE id = ? AND owner_id = ?",
                    (chain_id, owner_id),
                ).rowcount
                > 0
            )

    # --- runs -------------------------------------------------------------

    def _run(self, owner_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM chain_runs WHERE id = ? AND owner_id = ?", (run_id, owner_id)
        )
        if row is None:
            return None
        item = dict(row)
        item["error"] = json.loads(item.pop("error_json") or "null")
        return item

    def _set(self, run_id: str, **fields: Any) -> None:
        if "error" in fields:
            fields["error_json"] = json.dumps(fields.pop("error"))
        assigns = ", ".join(f"{k} = ?" for k in fields)
        with self.db.write() as conn:
            conn.execute(
                f"UPDATE chain_runs SET {assigns}, updated_ms = ? WHERE id = ?",
                (*fields.values(), now_ms(), run_id),
            )

    def start(self, owner_id: str, chain_id: str, request_key: str) -> dict[str, Any]:
        if self.get(owner_id, chain_id) is None:
            raise ChainError("Chain was not found.")
        with self.db.write() as conn:
            existing = conn.execute(
                "SELECT id FROM chain_runs WHERE owner_id = ? AND request_key = ?",
                (owner_id, request_key),
            ).fetchone()
            if existing is None:
                run_id, stamp = new_id(), now_ms()
                conn.execute(
                    "INSERT INTO chain_runs (id, owner_id, chain_id, request_key, status,"
                    " stage_index, attempt, created_ms, updated_ms)"
                    " VALUES (?, ?, ?, ?, 'running', 0, 0, ?, ?)",
                    (run_id, owner_id, chain_id, request_key, stamp, stamp),
                )
            else:
                run_id = existing["id"]
        return self._run(owner_id, run_id)  # type: ignore[return-value]

    def get_run(self, owner_id: str, run_id: str) -> dict[str, Any] | None:
        run = self._run(owner_id, run_id)
        if run is None:
            return None
        rows = self.db.query(
            "SELECT id, client_request_key, status, output_state FROM generations"
            " WHERE owner_id = ? AND client_request_key LIKE ? ORDER BY created_ms",
            (owner_id, f"chain-{run_id}-%"),
        )
        run["generations"] = [
            {
                "generation_id": r["id"],
                "stage_index": int(r["client_request_key"].split("-")[-2]),
                "status": r["status"],
                "output_state": r["output_state"],
            }
            for r in rows
        ]
        return run

    def list_runs(self, owner_id: str, chain_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM chain_runs WHERE owner_id = ? AND chain_id = ?"
            " ORDER BY created_ms DESC LIMIT 50",
            (owner_id, chain_id),
        )
        return [self.get_run(owner_id, r["id"]) for r in rows]  # type: ignore[misc]

    def active_runs(self) -> list[tuple[str, str]]:
        return [
            (r["owner_id"], r["id"])
            for r in self.db.query(
                "SELECT owner_id, id FROM chain_runs WHERE status = 'running'"
            )
        ]

    def resume(self, owner_id: str, run_id: str) -> dict[str, Any]:
        run = self._run(owner_id, run_id)
        if run is None:
            raise ChainError("Run was not found.")
        if run["status"] != "paused":
            raise ChainError("Only a paused run can be resumed.")
        self._set(run_id, status="running", attempt=run["attempt"] + 1, error=None)
        return self._run(owner_id, run_id)  # type: ignore[return-value]

    async def cancel(self, owner_id: str, run_id: str) -> dict[str, Any]:
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            run = await in_thread(self._run, owner_id, run_id)
            if run is None:
                raise ChainError("Run was not found.")
            if run["status"] in ("succeeded", "cancelled"):
                return run
            await in_thread(lambda: self._set(run_id, status="cancelled", error=None))
            gen = await in_thread(self._generation, owner_id, self._key(run))
            if gen is not None and gen["status"] in ACTIVE:
                with contextlib.suppress(Exception):  # run is already cancelled
                    await self.generations.cancel(owner_id, gen["id"])
        return await in_thread(self._run, owner_id, run_id)  # type: ignore[return-value]

    # --- orchestration ----------------------------------------------------

    @staticmethod
    def _key(run: dict[str, Any]) -> str:
        # ``attempt`` only ever grows, so a key is never reused after a resume.
        return f"chain-{run['id']}-{run['stage_index']}-{run['attempt']}"

    def _generation(self, owner_id: str, key: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM generations WHERE owner_id = ? AND client_request_key = ?",
            (owner_id, key),
        )
        return dict(row) if row else None

    def _previous_generation(
        self, owner_id: str, run: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The stage before the current one: its newest succeeded attempt."""
        row = self.db.query_one(
            "SELECT * FROM generations WHERE owner_id = ? AND client_request_key LIKE ?"
            " AND status = 'succeeded' ORDER BY created_ms DESC LIMIT 1",
            (owner_id, f"chain-{run['id']}-{run['stage_index'] - 1}-%"),
        )
        return dict(row) if row else None

    def _output_media(
        self, owner_id: str, generation_id: str, node: str, ordinal: int
    ) -> str | None:
        row = self.db.query_one(
            "SELECT media_id FROM capture_attempts WHERE owner_id = ? AND generation_id = ?"
            " AND output_node = ? AND ordinal = ? AND state = 'ready' AND media_id IS NOT NULL",
            (owner_id, generation_id, node, ordinal),
        )
        return row["media_id"] if row else None

    async def _pause(self, run_id: str, message: str, **detail: Any) -> bool:
        await in_thread(
            lambda: self._set(
                run_id, status="paused", error={"message": message, **detail}
            )
        )
        return False

    async def advance(self, owner_id: str, run_id: str) -> dict[str, Any] | None:
        """Move a running run as far as it can go without waiting on ComfyUI."""
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            while True:
                run = await in_thread(self._run, owner_id, run_id)
                if run is None or run["status"] != "running":
                    return run
                if not await self._step(owner_id, run):
                    return await in_thread(self._run, owner_id, run_id)

    async def _step(self, owner_id: str, run: dict[str, Any]) -> bool:
        """Do one transition; True when the run changed and should be re-read."""
        chain = await in_thread(self.get, owner_id, run["chain_id"])
        if chain is None:
            return await self._pause(run["id"], "The chain no longer exists.")
        index, stages = run["stage_index"], chain["stages"]
        gen = await in_thread(self._generation, owner_id, self._key(run))
        if gen is None:
            return await self._submit_stage(owner_id, run, stages[index])

        status, output_state = gen["status"], gen["output_state"]
        if status == "submission_unknown":
            return await self._pause(
                run["id"],
                "ComfyUI may or may not have received this stage; check the queue, then resume.",
                generation_id=gen["id"],
            )
        if status in ACTIVE:
            return False
        if status != "succeeded":
            return await self._pause(
                run["id"], f"Stage {index + 1} {status}.", generation_id=gen["id"]
            )
        if output_state == "pending":
            return False  # output capture is still being retried
        if output_state != "ready":
            return await self._pause(
                run["id"],
                f"Stage {index + 1} did not produce a complete, captured output.",
                generation_id=gen["id"],
            )
        if index + 1 == len(stages):
            await in_thread(lambda: self._set(run["id"], status="succeeded"))
        else:
            await in_thread(lambda: self._set(run["id"], stage_index=index + 1))
        return True

    async def _submit_stage(
        self, owner_id: str, run: dict[str, Any], stage: dict[str, Any]
    ) -> bool:
        index = run["stage_index"]
        workflow = await in_thread(
            self.repository.get_workflow, owner_id, stage["workflow_id"]
        )
        if workflow is None or (
            stage.get("workflow_revision") is not None
            and int(workflow["current_revision"]) != stage["workflow_revision"]
        ):
            return await self._pause(
                run["id"],
                f"Stage {index + 1}: the workflow was changed or removed since the chain was saved.",
            )
        inputs: dict[str, dict[str, str]] = {}
        if stage["links"]:
            previous = await in_thread(self._previous_generation, owner_id, run)
            if previous is None:
                return await self._pause(run["id"], "The previous stage is missing.")
            for link in stage["links"]:
                media_id = await in_thread(
                    self._output_media,
                    owner_id,
                    previous["id"],
                    link["from_output_node"],
                    link["ordinal"],
                )
                if media_id is None:
                    return await self._pause(
                        run["id"],
                        f"Stage {index} produced no captured output at node "
                        f"{link['from_output_node']} #{link['ordinal']}.",
                        generation_id=previous["id"],
                    )
                inputs[link["binding_id"]] = {"source": "media", "id": media_id}
        try:
            await self._submit(
                owner_id,
                workflow_id=stage["workflow_id"],
                request_key=self._key(run),
                edits=stage["edits"],
                inputs=inputs,
            )
        except HTTPException as exc:
            if exc.status_code == 429:
                return False  # pending cap reached; retry on the next tick
            return await self._pause(
                run["id"], f"Stage {index + 1} was not submitted: {exc.detail}"
            )
        return True
