"""Short SQLite transactions for the generation state machine."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..storage.db import Database
from ..storage.repository import (
    PageResult,
    _decode_cursor,
    _encode_cursor,
    new_id,
    now_ms,
)

ACTIVE = ("submitting", "submission_unknown", "queued", "running")
TERMINAL = frozenset(("succeeded", "failed", "cancelled", "interrupted", "unknown"))


class GenerationConflict(ValueError):
    pass


class GenerationBusy(ValueError):
    pass


@dataclass(frozen=True)
class AcceptedGeneration:
    row: dict[str, Any]
    is_new: bool


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    for column in ("graph_json", "effective_values_json", "error_json"):
        item[column.removesuffix("_json")] = (
            json.loads(item[column]) if item[column] else None
        )
    return item


class GenerationStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def accept(
        self,
        owner_id: str,
        *,
        request_key: str,
        fingerprint: str,
        resolve: Callable[[], tuple[dict[str, Any], dict[str, Any]]],
        workflow_id: str | None = None,
        workflow_revision: int | None = None,
        mapping_revision: int | None = None,
        global_pending_cap: int = 8,
        profile_pending_cap: int = 8,
    ) -> AcceptedGeneration:
        """Reserve the key before calling ``resolve`` and commit its exact result.

        ``BEGIN IMMEDIATE`` serializes competing request keys. An identical
        retry never invokes ``resolve``; a changed payload conflicts.
        """
        placeholders = ",".join("?" for _ in ACTIVE)
        with self.db.write() as conn:
            existing = conn.execute(
                "SELECT * FROM generations WHERE owner_id = ? AND client_request_key = ?",
                (owner_id, request_key),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise GenerationConflict(
                        "client request key was already used for another payload"
                    )
                return AcceptedGeneration(_decode(existing), False)

            global_count = conn.execute(
                f"SELECT COUNT(*) FROM generations WHERE status IN ({placeholders})",
                ACTIVE,
            ).fetchone()[0]
            owner_count = conn.execute(
                f"SELECT COUNT(*) FROM generations WHERE owner_id = ? AND status IN ({placeholders})",
                (owner_id, *ACTIVE),
            ).fetchone()[0]
            if global_count >= global_pending_cap or owner_count >= profile_pending_cap:
                raise GenerationBusy("generation pending limit reached")

            generation_id, stamp = new_id(), now_ms()
            conn.execute(
                "INSERT INTO generations (id, owner_id, workflow_id, workflow_revision,"
                " mapping_revision, client_request_key, request_fingerprint, status, created_ms, updated_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'submitting', ?, ?)",
                (
                    generation_id,
                    owner_id,
                    workflow_id,
                    workflow_revision,
                    mapping_revision,
                    request_key,
                    fingerprint,
                    stamp,
                    stamp,
                ),
            )
            graph, effective_values = resolve()  # key exists in this transaction first
            conn.execute(
                "UPDATE generations SET graph_json = ?, effective_values_json = ? WHERE id = ?",
                (
                    json.dumps(graph, separators=(",", ":")),
                    json.dumps(effective_values, separators=(",", ":")),
                    generation_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM generations WHERE id = ?", (generation_id,)
            ).fetchone()
        return AcceptedGeneration(_decode(row), True)

    def get(self, owner_id: str, generation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM generations WHERE id = ? AND owner_id = ?",
            (generation_id, owner_id),
        )
        return _decode(row) if row else None

    def last_effective_value(
        self, owner_id: str, workflow_id: str | None, binding_id: str
    ) -> Any:
        """The value this owner most recently submitted for ``binding_id`` in a workflow.

        Seed ``increment`` advances from this. Uses the caller's connection, so
        called from ``accept``'s ``resolve`` it sees a consistent snapshot.
        """
        row = self.db.query_one(
            "SELECT json_extract(effective_values_json, ?) AS value FROM generations"
            " WHERE owner_id = ? AND workflow_id IS ? AND json_extract(effective_values_json, ?) IS NOT NULL"
            " ORDER BY created_ms DESC, rowid DESC LIMIT 1",
            (f'$."{binding_id}"', owner_id, workflow_id, f'$."{binding_id}"'),
        )
        return row["value"] if row else None

    def recent_prompts(
        self,
        owner_id: str,
        workflow_id: str,
        binding_id: str,
        query: str = "",
        cursor: str | None = None,
        limit: int = 20,
    ) -> PageResult:
        params: list[Any] = [owner_id, workflow_id, binding_id, query]
        after = ""
        if cursor:
            stamp, row_id = _decode_cursor(cursor)
            after = " AND (created_ms < ? OR (created_ms = ? AND id < ?))"
            params.extend((stamp, stamp, row_id))
        params.append(limit + 1)
        rows = self.db.query(
            "WITH ranked AS (SELECT g.id, g.created_ms, j.value AS text,"
            " ROW_NUMBER() OVER (PARTITION BY j.value ORDER BY g.created_ms DESC, g.id DESC) AS rank"
            " FROM generations g, json_each(g.effective_values_json) j"
            " WHERE g.owner_id = ? AND g.workflow_id = ? AND j.key = ?"
            " AND j.type = 'text' AND length(trim(j.value)) > 0"
            " AND instr(lower(j.value), lower(?)) > 0)"
            " SELECT id, created_ms, text FROM ranked WHERE rank = 1"
            + after
            + " ORDER BY created_ms DESC, id DESC LIMIT ?",
            params,
        )
        page = [dict(row) for row in rows[:limit]]
        return PageResult(page, _encode_cursor(page[-1]) if len(rows) > limit else None)

    def owner_for_prompt(self, prompt_id: str) -> tuple[str, str] | None:
        row = self.db.query_one(
            "SELECT owner_id, id FROM generations WHERE upstream_prompt_id = ?",
            (prompt_id,),
        )
        return (row["owner_id"], row["id"]) if row else None

    def active(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE)
        return [
            _decode(row)
            for row in self.db.query(
                f"SELECT * FROM generations WHERE status IN ({placeholders}) OR output_state = 'pending'"
                " OR error_json LIKE '%\"output_capture\"%' ORDER BY created_ms",
                ACTIVE,
            )
        ]

    def update(
        self,
        owner_id: str,
        generation_id: str,
        *,
        status: str | None = None,
        prompt_id: str | None = None,
        output_state: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        with self.db.write() as conn:
            assignments, values = ["updated_ms = ?"], [now_ms()]
            for column, value in (
                ("status", status),
                ("upstream_prompt_id", prompt_id),
                ("output_state", output_state),
            ):
                if value is not None:
                    assignments.append(f"{column} = ?")
                    values.append(value)
            if error is not None:
                current = conn.execute(
                    "SELECT error_json FROM generations WHERE id = ? AND owner_id = ?",
                    (generation_id, owner_id),
                ).fetchone()
                merged = (
                    json.loads(current["error_json"])
                    if current and current["error_json"]
                    else {}
                )
                merged.update(error)
                assignments.append("error_json = ?")
                values.append(json.dumps(merged, separators=(",", ":")))
            values.extend((generation_id, owner_id))
            changed = conn.execute(
                f"UPDATE generations SET {', '.join(assignments)} WHERE id = ? AND owner_id = ?",
                tuple(values),
            ).rowcount
        return changed == 1

    def clear_error(self, owner_id: str, generation_id: str, key: str) -> None:
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT error_json FROM generations WHERE id = ? AND owner_id = ?",
                (generation_id, owner_id),
            ).fetchone()
            if row is None or not row["error_json"]:
                return
            error = json.loads(row["error_json"])
            error.pop(key, None)
            conn.execute(
                "UPDATE generations SET error_json = ?, updated_ms = ? WHERE id = ? AND owner_id = ?",
                (
                    json.dumps(error, separators=(",", ":")) if error else None,
                    now_ms(),
                    generation_id,
                    owner_id,
                ),
            )

    def purge_snapshot(self, owner_id: str, generation_id: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE generations SET graph_json = NULL, effective_values_json = NULL, updated_ms = ?"
                " WHERE id = ? AND owner_id = ? AND status IN ('succeeded','failed','cancelled','interrupted','unknown')"
                " AND output_state != 'pending'",
                (now_ms(), generation_id, owner_id),
            )
