"""Owner-scoped repository methods.

Every read and write here takes ``owner_id`` and puts it in the WHERE clause,
so another profile's row is simply not found — there is no "fetch then check"
path that can be forgotten. ``owner_id`` comes from the session, never from a
request body (see app/contracts.py).

Listing uses keyset pagination on ``(created_ms, id)``: a row inserted while a
client pages does not shift rows onto a page that was already read, and no row
is skipped. Offset pagination would do both.

Calls here block. From async code use ``await in_thread(repo.method, ...)``.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from sqlite3 import IntegrityError, Row
from typing import Any

from .db import Database

DEFAULT_PROFILE_ID = "default"
PAGE_SIZE = 50


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id() -> str:
    return uuid.uuid4().hex


class RevisionConflict(Exception):
    """An optimistic write whose ``expected_revision`` no longer matches. -> 409."""


@dataclass(frozen=True)
class PageResult:
    """Rows plus the cursor for the next page (None at the end of the list)."""

    items: list[dict[str, Any]]
    next_cursor: str | None


def _encode_cursor(row: Row | dict[str, Any]) -> str:
    raw = f"{row['created_ms']}:{row['id']}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_ms, _, row_id = base64.urlsafe_b64decode(padded).decode().partition(":")
        return int(created_ms), row_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Malformed cursor: {cursor!r}") from exc


class Repository:
    """Blocking data access. One instance is safe to share across threads."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # --- profiles ---------------------------------------------------------

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM profiles WHERE id = ?", (profile_id,))
        return dict(row) if row else None

    def list_profiles(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM profiles ORDER BY created_ms, id")]

    def create_profile(self, name: str, password_hash: str | None = None) -> str:
        """Create an additional (non-default) profile. Names are unique."""
        profile_id = new_id()
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO profiles (id, name, password_hash, is_default, created_ms)"
                " VALUES (?, ?, ?, 0, ?)",
                (profile_id, name, password_hash, now_ms()),
            )
        return profile_id

    # --- workflows --------------------------------------------------------

    def create_workflow(self, owner_id: str, name: str, graph: dict[str, Any]) -> str:
        """Store a workflow and its immutable revision 1 graph in one transaction."""
        workflow_id = new_id()
        stamp = now_ms()
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO workflows (id, owner_id, name, current_revision, created_ms, updated_ms)"
                " VALUES (?, ?, ?, 1, ?, ?)",
                (workflow_id, owner_id, name, stamp, stamp),
            )
            conn.execute(
                "INSERT INTO workflow_revisions (workflow_id, revision, graph_json, created_ms)"
                " VALUES (?, 1, ?, ?)",
                (workflow_id, json.dumps(graph, separators=(",", ":")), stamp),
            )
        return workflow_id

    def get_workflow(self, owner_id: str, workflow_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM workflows WHERE id = ? AND owner_id = ?", (workflow_id, owner_id)
        )
        return dict(row) if row else None

    def get_workflow_graph(
        self, owner_id: str, workflow_id: str, revision: int
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT r.graph_json FROM workflow_revisions r JOIN workflows w ON w.id = r.workflow_id"
            " WHERE r.workflow_id = ? AND r.revision = ? AND w.owner_id = ?",
            (workflow_id, revision, owner_id),
        )
        return json.loads(row["graph_json"]) if row else None

    def list_workflows(
        self, owner_id: str, cursor: str | None = None, limit: int = PAGE_SIZE
    ) -> PageResult:
        return self._page("workflows", owner_id, cursor, limit)

    # --- mapping corrections ---------------------------------------------

    def save_mapping_override(
        self,
        owner_id: str,
        *,
        scope: str,
        workflow_id: str | None,
        selector: str,
        schema_signature: str,
        presentation: dict[str, Any],
        expected_revision: int,
    ) -> int:
        """Upsert one correction optimistically; returns its new revision.

        ``expected_revision`` 0 means "not saved yet". Any mismatch raises
        ``RevisionConflict`` inside the same transaction that read the current
        revision, so two tabs saving at once cannot both win.
        """
        stamp = now_ms()
        payload = json.dumps(presentation, separators=(",", ":"))
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT id, revision FROM mapping_overrides WHERE owner_id = ? AND scope = ?"
                " AND IFNULL(workflow_id, '') = IFNULL(?, '') AND binding_selector = ?",
                (owner_id, scope, workflow_id, selector),
            ).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision != current:
                raise RevisionConflict(
                    f"This correction is at revision {current}, not {expected_revision}. "
                    "Reload the mapping editor before saving again."
                )
            revision = current + 1
            if row is None:
                conn.execute(
                    "INSERT INTO mapping_overrides (id, owner_id, scope, workflow_id,"
                    " binding_selector, schema_signature, presentation_json, revision, updated_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id(),
                        owner_id,
                        scope,
                        workflow_id,
                        selector,
                        schema_signature,
                        payload,
                        revision,
                        stamp,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE mapping_overrides SET schema_signature = ?, presentation_json = ?,"
                    " revision = ?, updated_ms = ? WHERE id = ? AND owner_id = ?",
                    (schema_signature, payload, revision, stamp, row["id"], owner_id),
                )
        return revision

    def list_mapping_overrides(self, owner_id: str, workflow_id: str) -> list[dict[str, Any]]:
        """This workflow's own corrections plus the profile's node templates."""
        return [
            dict(row)
            for row in self.db.query(
                "SELECT * FROM mapping_overrides WHERE owner_id = ?"
                " AND (workflow_id = ? OR scope = 'node_class')"
                " ORDER BY scope, binding_selector",
                (owner_id, workflow_id),
            )
        ]

    def delete_mapping_overrides(
        self,
        owner_id: str,
        *,
        workflow_id: str | None = None,
        scope: str | None = None,
        selector: str | None = None,
    ) -> int:
        """Reset: drop matching corrections so the derived base schema returns."""
        where = ["owner_id = ?"]
        params: list[Any] = [owner_id]
        if workflow_id is not None:
            where.append("workflow_id = ?")
            params.append(workflow_id)
        if scope is not None:
            where.append("scope = ?")
            params.append(scope)
        if selector is not None:
            where.append("binding_selector = ?")
            params.append(selector)
        with self.db.write() as conn:
            cursor = conn.execute(
                f"DELETE FROM mapping_overrides WHERE {' AND '.join(where)}", tuple(params)
            )
            return cursor.rowcount

    # --- generations ------------------------------------------------------

    def create_generation(
        self,
        owner_id: str,
        *,
        client_request_key: str,
        request_fingerprint: str,
        workflow_id: str | None = None,
        workflow_revision: int | None = None,
        graph: dict[str, Any] | None = None,
        effective_values: dict[str, Any] | None = None,
        status: str = "submitting",
    ) -> str:
        """Persist a generation before contacting ComfyUI.

        An identical retry of the same request key returns the existing
        generation id; a different fingerprint under that key raises
        ``IntegrityError`` for the caller to turn into a 409.
        """
        existing = self.db.query_one(
            "SELECT id, request_fingerprint FROM generations"
            " WHERE owner_id = ? AND client_request_key = ?",
            (owner_id, client_request_key),
        )
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint:
                raise IntegrityError(
                    "client_request_key reused with a different payload"
                )
            return existing["id"]

        generation_id = new_id()
        stamp = now_ms()
        try:
            with self.db.write() as conn:
                conn.execute(
                    "INSERT INTO generations (id, owner_id, workflow_id, workflow_revision,"
                    " client_request_key, request_fingerprint, graph_json, effective_values_json,"
                    " status, created_ms, updated_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        generation_id,
                        owner_id,
                        workflow_id,
                        workflow_revision,
                        client_request_key,
                        request_fingerprint,
                        None if graph is None else json.dumps(graph, separators=(",", ":")),
                        None if effective_values is None else json.dumps(effective_values),
                        status,
                        stamp,
                        stamp,
                    ),
                )
        except IntegrityError:
            # Lost the race against a concurrent identical submission.
            row = self.db.query_one(
                "SELECT id, request_fingerprint FROM generations"
                " WHERE owner_id = ? AND client_request_key = ?",
                (owner_id, client_request_key),
            )
            if row is None or row["request_fingerprint"] != request_fingerprint:
                raise
            return row["id"]
        return generation_id

    def get_generation(self, owner_id: str, generation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM generations WHERE id = ? AND owner_id = ?", (generation_id, owner_id)
        )
        return dict(row) if row else None

    def update_generation_status(
        self,
        owner_id: str,
        generation_id: str,
        *,
        status: str,
        upstream_prompt_id: str | None = None,
        output_state: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        """Returns False when the generation is missing or owned by someone else."""
        with self.db.write() as conn:
            cursor = conn.execute(
                "UPDATE generations SET status = ?, updated_ms = ?,"
                " upstream_prompt_id = COALESCE(?, upstream_prompt_id),"
                " output_state = COALESCE(?, output_state),"
                " error_json = COALESCE(?, error_json)"
                " WHERE id = ? AND owner_id = ?",
                (
                    status,
                    now_ms(),
                    upstream_prompt_id,
                    output_state,
                    None if error is None else json.dumps(error),
                    generation_id,
                    owner_id,
                ),
            )
            return cursor.rowcount == 1

    def purge_generation_snapshot(self, owner_id: str, generation_id: str) -> bool:
        """History retention off: drop prompt/graph, keep provenance and status."""
        with self.db.write() as conn:
            cursor = conn.execute(
                "UPDATE generations SET graph_json = NULL, effective_values_json = NULL,"
                " updated_ms = ? WHERE id = ? AND owner_id = ?",
                (now_ms(), generation_id, owner_id),
            )
            return cursor.rowcount == 1

    def list_generations(
        self, owner_id: str, cursor: str | None = None, limit: int = PAGE_SIZE
    ) -> PageResult:
        return self._page("generations", owner_id, cursor, limit)

    # --- media ------------------------------------------------------------

    def record_media(
        self,
        owner_id: str,
        *,
        storage_path: str,
        file_version: str,
        media_kind: str,
        media_type: str,
        state: str = "indexed",
        generation_id: str | None = None,
        output_node: str | None = None,
        ordinal: int | None = None,
    ) -> str:
        """Idempotent by (generation, output node, ordinal, file version).

        A replayed history delivery returns the existing media id instead of
        creating a second gallery card.
        """
        media_id = new_id()
        stamp = now_ms()
        with self.db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO media (id, owner_id, generation_id, output_node, ordinal,"
                " storage_path, file_version, media_kind, media_type, state, created_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    media_id,
                    owner_id,
                    generation_id,
                    output_node,
                    ordinal,
                    storage_path,
                    file_version,
                    media_kind,
                    media_type,
                    state,
                    stamp,
                ),
            )
            row = conn.execute(
                "SELECT id FROM media WHERE IFNULL(generation_id, '') = IFNULL(?, '')"
                " AND IFNULL(output_node, '') = IFNULL(?, '')"
                " AND IFNULL(ordinal, -1) = IFNULL(?, -1) AND file_version = ?",
                (generation_id, output_node, ordinal, file_version),
            ).fetchone()
        return row["id"]

    def get_media(self, owner_id: str, media_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM media WHERE id = ? AND owner_id = ?", (media_id, owner_id)
        )
        return dict(row) if row else None

    def list_media(
        self,
        owner_id: str,
        cursor: str | None = None,
        limit: int = PAGE_SIZE,
        *,
        media_kind: str | None = None,
        favorite: bool | None = None,
        workflow_id: str | None = None,
        created_after: int | None = None,
        created_before: int | None = None,
        prompt: str | None = None,
    ) -> PageResult:
        clauses = ["AND hidden = 0"]
        params: list[Any] = []
        for column, value in (
            ("media_kind", media_kind),
            ("favorite", None if favorite is None else int(favorite)),
        ):
            if value is not None:
                clauses.append(f"AND {column} = ?")
                params.append(value)
        if workflow_id is not None:
            clauses.append(
                "AND EXISTS (SELECT 1 FROM generations g WHERE g.id = media.generation_id"
                " AND g.owner_id = media.owner_id AND g.workflow_id = ?)"
            )
            params.append(workflow_id)
        if created_after is not None:
            clauses.append("AND media.created_ms >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("AND media.created_ms <= ?")
            params.append(created_before)
        if prompt:
            escaped = prompt.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append(
                "AND EXISTS (SELECT 1 FROM generations g WHERE g.id = media.generation_id"
                " AND g.owner_id = media.owner_id AND g.effective_values_json LIKE ? ESCAPE '\\')"
            )
            params.append(f"%{escaped}%")
        return self._page(
            "media", owner_id, cursor, limit,
            extra=" ".join(clauses), extra_params=tuple(params),
        )

    # --- shared paging ----------------------------------------------------

    _PAGEABLE = frozenset({"workflows", "generations", "media", "uploads"})

    def _page(
        self,
        table: str,
        owner_id: str,
        cursor: str | None,
        limit: int,
        extra: str = "",
        extra_params: tuple[Any, ...] = (),
    ) -> PageResult:
        if table not in self._PAGEABLE:  # table name is interpolated below
            raise ValueError(f"Not a pageable table: {table}")
        limit = max(1, min(limit, 200))
        params: list[Any] = [owner_id]
        keyset = ""
        if cursor:
            created_ms, row_id = _decode_cursor(cursor)
            keyset = "AND (created_ms, id) < (?, ?)"
            params += [created_ms, row_id]
        params.extend(extra_params)
        params.append(limit + 1)
        rows = self.db.query(
            f"SELECT * FROM {table} WHERE owner_id = ? {keyset} {extra}"
            " ORDER BY created_ms DESC, id DESC LIMIT ?",
            tuple(params),
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return PageResult(
            items=[dict(r) for r in page],
            next_cursor=_encode_cursor(page[-1]) if has_more and page else None,
        )
