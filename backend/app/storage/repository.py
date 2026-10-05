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
import hashlib
import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlite3 import IntegrityError, Row
from typing import Any

from .db import Database

DEFAULT_PROFILE_ID = "default"
PAGE_SIZE = 50

SEARCH_INPUTS = {
    "seed": frozenset({"seed", "noise_seed"}),
    "prompt": frozenset(
        {"text", "prompt", "positive", "negative", "positive_prompt", "negative_prompt"}
    ),
    "model": frozenset(
        {
            "model",
            "model_name",
            "checkpoint",
            "checkpoint_name",
            "ckpt_name",
            "unet_name",
            "diffusion_model",
            "diffusion_model_name",
            "lora_name",
            "vae_name",
            "clip_name",
            "clip_name1",
            "clip_name2",
            "clip_name3",
        }
    ),
}


def search_values(values: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Normalize scalar controls without coercing exact Python integers."""
    records = []
    for key, value in values.items():
        if type(value) not in (str, int, float, bool):
            continue
        name = key.rsplit(":", 1)[-1].casefold()
        field = next(
            (field for field, names in SEARCH_INPUTS.items() if name in names), "other"
        )
        text = (json.dumps(value) if type(value) is bool else str(value)).strip()
        if text:
            records.append((field, text, text.casefold()))
    return records


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id() -> str:
    return uuid.uuid4().hex


class RevisionConflict(Exception):
    """An optimistic write whose ``expected_revision`` no longer matches. -> 409."""


class LimitExceeded(Exception):
    """A per-owner cap was reached. -> 409."""


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
        return [
            dict(r)
            for r in self.db.query("SELECT * FROM profiles ORDER BY created_ms, id")
        ]

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

    def rename_workflow(
        self, owner_id: str, workflow_id: str, name: str
    ) -> dict[str, Any] | None:
        with self.db.write() as conn:
            cursor = conn.execute(
                "UPDATE workflows SET name = ?, updated_ms = ? WHERE id = ? AND owner_id = ?",
                (name, now_ms(), workflow_id, owner_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_workflow(owner_id, workflow_id)

    def replace_workflow_graph(
        self,
        owner_id: str,
        workflow_id: str,
        graph: dict[str, Any],
        expected_revision: int,
    ) -> int | None:
        """Store ``graph`` as revision N+1 and make it current, atomically.

        Returns the new revision, or None when the workflow is not owned.
        ``expected_revision`` is the revision the caller diffed against; if the
        workflow moved on meanwhile this raises ``RevisionConflict`` and nothing
        is written. Older revisions stay (generations keep the one they ran).
        """
        stamp = now_ms()
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT current_revision FROM workflows WHERE id = ? AND owner_id = ?",
                (workflow_id, owner_id),
            ).fetchone()
            if row is None:
                return None
            current = int(row["current_revision"])
            if current != expected_revision:
                raise RevisionConflict(
                    f"This workflow is at graph revision {current}, not {expected_revision}. "
                    "Reload it before replacing the graph."
                )
            revision = current + 1
            conn.execute(
                "INSERT INTO workflow_revisions (workflow_id, revision, graph_json, created_ms)"
                " VALUES (?, ?, ?, ?)",
                (
                    workflow_id,
                    revision,
                    json.dumps(graph, separators=(",", ":")),
                    stamp,
                ),
            )
            conn.execute(
                "UPDATE workflows SET current_revision = ?, updated_ms = ? WHERE id = ?",
                (revision, stamp, workflow_id),
            )
        return revision

    def get_workflow(self, owner_id: str, workflow_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM workflows WHERE id = ? AND owner_id = ?",
            (workflow_id, owner_id),
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

    def delete_workflow(self, owner_id: str, workflow_id: str) -> bool:
        """Delete a workflow the caller owns. Cascades to its revisions and
        saved workflow-scoped corrections; past generations keep their history
        with ``workflow_id`` set to null (migrations/001_initial.sql)."""
        with self.db.write() as conn:
            cursor = conn.execute(
                "DELETE FROM workflows WHERE id = ? AND owner_id = ?",
                (workflow_id, owner_id),
            )
            return cursor.rowcount > 0

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

    def list_mapping_overrides(
        self, owner_id: str, workflow_id: str
    ) -> list[dict[str, Any]]:
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
                f"DELETE FROM mapping_overrides WHERE {' AND '.join(where)}",
                tuple(params),
            )
            return cursor.rowcount

    # --- workflow layouts -------------------------------------------------

    def get_layout(self, owner_id: str, workflow_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM workflow_layouts WHERE owner_id = ? AND workflow_id = ?",
            (owner_id, workflow_id),
        )
        return dict(row) if row else None

    def save_layout(
        self,
        owner_id: str,
        workflow_id: str,
        *,
        layout: dict[str, Any],
        schema_signature: str,
        expected_revision: int,
    ) -> int:
        """Upsert the layout optimistically; returns its new revision.

        ``expected_revision`` 0 means "not saved yet". A mismatch raises
        ``RevisionConflict`` inside the transaction that read the revision and
        leaves the stored document untouched.
        """
        payload = json.dumps(layout, separators=(",", ":"))
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT revision FROM workflow_layouts WHERE owner_id = ? AND workflow_id = ?",
                (owner_id, workflow_id),
            ).fetchone()
            current = int(row["revision"]) if row else 0
            if expected_revision != current:
                raise RevisionConflict(
                    f"This layout is at revision {current}, not {expected_revision}. "
                    "Reload it before saving again."
                )
            revision = current + 1
            conn.execute(
                "INSERT INTO workflow_layouts (owner_id, workflow_id, layout_json,"
                " schema_signature, revision, updated_ms) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (owner_id, workflow_id) DO UPDATE SET layout_json = excluded.layout_json,"
                " schema_signature = excluded.schema_signature, revision = excluded.revision,"
                " updated_ms = excluded.updated_ms",
                (owner_id, workflow_id, payload, schema_signature, revision, now_ms()),
            )
        return revision

    def delete_layout(
        self, owner_id: str, workflow_id: str, expected_revision: int | None = None
    ) -> bool:
        """Reset to the automatic layout. With ``expected_revision`` (0 = none
        saved) a mismatch raises ``RevisionConflict`` in the same transaction."""
        with self.db.write() as conn:
            if expected_revision is not None:
                row = conn.execute(
                    "SELECT revision FROM workflow_layouts WHERE owner_id = ? AND workflow_id = ?",
                    (owner_id, workflow_id),
                ).fetchone()
                current = int(row["revision"]) if row else 0
                if current != expected_revision:
                    raise RevisionConflict(
                        f"This layout is at revision {current}, not {expected_revision}. "
                        "Reload it before resetting."
                    )
            cursor = conn.execute(
                "DELETE FROM workflow_layouts WHERE owner_id = ? AND workflow_id = ?",
                (owner_id, workflow_id),
            )
            return cursor.rowcount > 0

    # --- workflow presets -------------------------------------------------

    def list_presets(self, owner_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.query(
                "SELECT * FROM workflow_presets WHERE owner_id = ? AND workflow_id = ?"
                " ORDER BY updated_ms DESC, id DESC",
                (owner_id, workflow_id),
            )
        ]

    def create_preset(
        self,
        owner_id: str,
        workflow_id: str,
        name: str,
        values: dict[str, Any],
        limit: int,
    ) -> dict[str, Any] | None:
        """None when the workflow is not owned; ``LimitExceeded`` at ``limit`` presets."""
        preset_id, stamp = new_id(), now_ms()
        with self.db.write() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM workflows WHERE id = ? AND owner_id = ?",
                    (workflow_id, owner_id),
                ).fetchone()
                is None
            ):
                return None
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM workflow_presets WHERE owner_id = ? AND workflow_id = ?",
                (owner_id, workflow_id),
            ).fetchone()["n"]
            if count >= limit:
                raise LimitExceeded(
                    f"A workflow holds at most {limit} presets. Delete one first."
                )
            conn.execute(
                "INSERT INTO workflow_presets (id, owner_id, workflow_id, name, values_json,"
                " revision, created_ms, updated_ms) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    preset_id,
                    owner_id,
                    workflow_id,
                    name,
                    json.dumps(values, allow_nan=False),
                    stamp,
                    stamp,
                ),
            )
        return self.get_preset(owner_id, workflow_id, preset_id)

    def get_preset(
        self, owner_id: str, workflow_id: str, preset_id: str
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM workflow_presets WHERE id = ? AND owner_id = ? AND workflow_id = ?",
            (preset_id, owner_id, workflow_id),
        )
        return dict(row) if row else None

    def update_preset(
        self,
        owner_id: str,
        workflow_id: str,
        preset_id: str,
        name: str | None,
        values: dict[str, Any] | None,
        expected_revision: int,
    ) -> dict[str, Any] | None:
        """None when not found/owned; ``RevisionConflict`` on a stale revision."""
        with self.db.write() as conn:
            row = conn.execute(
                "SELECT revision FROM workflow_presets WHERE id = ? AND owner_id = ?"
                " AND workflow_id = ?",
                (preset_id, owner_id, workflow_id),
            ).fetchone()
            if row is None:
                return None
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict(
                    f"This preset is at revision {row['revision']}, not {expected_revision}. "
                    "Reload the presets before saving again."
                )
            conn.execute(
                "UPDATE workflow_presets SET name = COALESCE(?, name),"
                " values_json = COALESCE(?, values_json), revision = revision + 1, updated_ms = ?"
                " WHERE id = ? AND owner_id = ?",
                (
                    name,
                    None if values is None else json.dumps(values, allow_nan=False),
                    now_ms(),
                    preset_id,
                    owner_id,
                ),
            )
        return self.get_preset(owner_id, workflow_id, preset_id)

    def delete_preset(self, owner_id: str, workflow_id: str, preset_id: str) -> bool:
        with self.db.write() as conn:
            cursor = conn.execute(
                "DELETE FROM workflow_presets WHERE id = ? AND owner_id = ? AND workflow_id = ?",
                (preset_id, owner_id, workflow_id),
            )
            return cursor.rowcount > 0

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
                        None
                        if graph is None
                        else json.dumps(graph, separators=(",", ":")),
                        None
                        if effective_values is None
                        else json.dumps(effective_values),
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

    def get_generation(
        self, owner_id: str, generation_id: str
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM generations WHERE id = ? AND owner_id = ?",
            (generation_id, owner_id),
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
        self,
        owner_id: str,
        cursor: str | None = None,
        limit: int = PAGE_SIZE,
        active: bool = False,
    ) -> PageResult:
        extra = (
            "AND status IN ('submitting','submission_unknown','queued','running')"
            if active
            else ""
        )
        return self._page("generations", owner_id, cursor, limit, extra)

    # --- media ------------------------------------------------------------

    def _sync_search(self, owner_id: str) -> None:
        """Backfill in bounded transactions; triggers invalidate snapshots immediately."""
        while True:
            with self.db.write() as conn:
                rows = conn.execute(
                    "SELECT g.id, g.effective_values_json FROM generation_search_pending p"
                    " JOIN generations g ON g.id = p.generation_id WHERE g.owner_id = ? LIMIT 100",
                    (owner_id,),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    try:
                        values = json.loads(row["effective_values_json"])
                    except (ValueError, TypeError):
                        values = {}
                    records = search_values(values) if isinstance(values, dict) else []
                    conn.executemany(
                        "INSERT INTO generation_search VALUES (?, ?, ?, ?, ?)",
                        [(row["id"], owner_id, *record) for record in records],
                    )
                    conn.execute(
                        "DELETE FROM generation_search_pending WHERE generation_id = ?",
                        (row["id"],),
                    )
        # Functions belong to the current thread's SQLite connection.
        self.db.connect().create_function(
            "gallery_fold",
            1,
            lambda value: str(value or "").casefold(),
            deterministic=True,
        )
        self.db.connect().create_function(
            "gallery_filename",
            1,
            lambda path: path.replace("\\", "/").rsplit("/", 1)[-1],
            deterministic=True,
        )

    @staticmethod
    def _search_field(field: str) -> str:
        if field not in {"any", "prompt", "model", "seed", "workflow", "filename"}:
            raise ValueError("Unknown search field")
        return "" if field == "any" else " AND field = ?"

    def media_filter_suggestions(
        self, owner_id: str, query: str, limit: int = 10, search_field: str = "any"
    ) -> list[str]:
        query = query.strip().casefold()
        if not 2 <= len(query) <= 500:
            return []
        field_sql = self._search_field(search_field)
        self._sync_search(owner_id)
        sources, params = [], []
        if search_field not in {"workflow", "filename"}:
            sources.append(
                "SELECT value, folded FROM generation_search WHERE owner_id = ?"
                + field_sql
            )
            params.extend([owner_id] + ([search_field] if field_sql else []))
            sources.append(
                "SELECT s.value, s.folded FROM media_search s JOIN media m ON m.id = s.media_id"
                " AND m.owner_id = s.owner_id WHERE s.owner_id = ? AND m.hidden = 0"
                + field_sql
            )
            params.extend([owner_id] + ([search_field] if field_sql else []))
        if search_field in {"any", "workflow"}:
            sources.append(
                "SELECT w.name AS value, gallery_fold(w.name) AS folded FROM workflows w"
                " WHERE w.owner_id = ? AND EXISTS (SELECT 1 FROM generations g"
                " WHERE g.workflow_id = w.id AND g.owner_id = w.owner_id)"
            )
            params.append(owner_id)
        if search_field in {"any", "filename"}:
            sources.append(
                "SELECT gallery_filename(storage_path) AS value,"
                " gallery_fold(gallery_filename(storage_path)) AS folded FROM media"
                " WHERE owner_id = ? AND hidden = 0"
            )
            params.append(owner_id)
        rows = self.db.query(
            "SELECT MIN(value) AS value FROM (" + " UNION ALL ".join(sources) + ")"
            " WHERE instr(folded, ?) > 0 AND length(value) BETWEEN 2 AND 500"
            " AND instr(value, '/') = 0 AND instr(value, char(92)) = 0"
            " GROUP BY folded ORDER BY folded LIMIT ?",
            (*params, query, max(1, min(limit, 10))),
        )
        return [row["value"] for row in rows]

    def _media_search(
        self, owner_id: str, term: str, field: str
    ) -> tuple[str, list[Any]]:
        field_sql = self._search_field(field)
        self._sync_search(owner_id)
        predicates, params = [], []
        if field not in {"workflow", "filename"}:
            predicates.append(
                "EXISTS (SELECT 1 FROM generation_search s"
                " WHERE s.owner_id = media.owner_id AND s.generation_id = media.generation_id"
                " AND instr(s.folded, ?) > 0" + field_sql + ")"
            )
            params.extend([term.casefold()] + ([field] if field_sql else []))
            predicates.append(
                "EXISTS (SELECT 1 FROM media_search s WHERE s.owner_id = media.owner_id"
                " AND s.media_id = media.id AND instr(s.folded, ?) > 0"
                + field_sql
                + ")"
            )
            params.extend([term.casefold()] + ([field] if field_sql else []))
        if field in {"any", "workflow"}:
            predicates.append(
                "EXISTS (SELECT 1 FROM generations g JOIN workflows w ON w.id = g.workflow_id"
                " AND w.owner_id = g.owner_id WHERE g.id = media.generation_id"
                " AND g.owner_id = media.owner_id AND instr(gallery_fold(w.name), ?) > 0)"
            )
            params.append(term.casefold())
        if field in {"any", "filename"}:
            predicates.append(
                "instr(gallery_fold(gallery_filename(media.storage_path)), ?) > 0"
            )
            params.append(term.casefold())
        return "AND (" + " OR ".join(predicates) + ")", params

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

    def duplicate_candidates(self, owner_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.query(
                "SELECT m.*, h.sha256 AS cached_sha256, h.byte_size AS hash_size FROM media m"
                " LEFT JOIN media_hashes h ON h.media_id = m.id AND h.file_version = m.file_version"
                " WHERE m.owner_id = ? AND m.hidden = 0 ORDER BY m.id",
                (owner_id,),
            )
        ]

    def cache_media_hash(
        self, owner_id: str, media_id: str, version: str, size: int, digest: str
    ) -> None:
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO media_hashes (media_id, file_version, byte_size, sha256)"
                " SELECT id, file_version, ?, ? FROM media WHERE id = ? AND owner_id = ?"
                " AND file_version = ? AND hidden = 0"
                " ON CONFLICT(media_id) DO UPDATE SET file_version = excluded.file_version,"
                " byte_size = excluded.byte_size, sha256 = excluded.sha256",
                (size, digest, media_id, owner_id, version),
            )

    def list_collections(self, owner_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.query(
                "SELECT c.id, c.name, COUNT(m.id) AS count FROM collections c"
                " LEFT JOIN collection_media cm ON cm.collection_id = c.id AND cm.owner_id = c.owner_id"
                " LEFT JOIN media m ON m.id = cm.media_id AND m.owner_id = c.owner_id AND m.hidden = 0"
                " WHERE c.owner_id = ? GROUP BY c.id ORDER BY c.name COLLATE NOCASE, c.id",
                (owner_id,),
            )
        ]

    def save_collection(
        self, owner_id: str, name: str, collection_id: str | None = None
    ) -> dict[str, str] | None:
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ValueError("Collection name must contain 1 to 100 characters.")
        with self.db.write() as conn:
            if collection_id is None:
                collection_id = new_id()
                conn.execute(
                    "INSERT INTO collections (id, owner_id, name) VALUES (?, ?, ?)",
                    (collection_id, owner_id, name),
                )
            elif not conn.execute(
                "UPDATE collections SET name = ? WHERE id = ? AND owner_id = ?",
                (name, collection_id, owner_id),
            ).rowcount:
                return None
        return {"id": collection_id, "name": name}

    def delete_collection(self, owner_id: str, collection_id: str) -> bool:
        with self.db.write() as conn:
            return bool(
                conn.execute(
                    "DELETE FROM collections WHERE id = ? AND owner_id = ?",
                    (collection_id, owner_id),
                ).rowcount
            )

    def collection_members(
        self, owner_id: str, collection_id: str, ids: list[str], *, remove: bool = False
    ) -> int | None:
        ids = list(dict.fromkeys(ids))
        with self.db.write() as conn:
            if not conn.execute(
                "SELECT 1 FROM collections WHERE id = ? AND owner_id = ?",
                (collection_id, owner_id),
            ).fetchone():
                return None
            found = conn.execute(
                "SELECT id FROM media WHERE owner_id = ? AND id IN (SELECT value FROM json_each(?))",
                (owner_id, json.dumps(ids)),
            ).fetchall()
            if len(found) != len(ids):
                return None
            if remove:
                return conn.execute(
                    "DELETE FROM collection_media WHERE owner_id = ? AND collection_id = ?"
                    " AND media_id IN (SELECT value FROM json_each(?))",
                    (owner_id, collection_id, json.dumps(ids)),
                ).rowcount
            return conn.executemany(
                "INSERT OR IGNORE INTO collection_media (owner_id, collection_id, media_id) VALUES (?, ?, ?)",
                [(owner_id, collection_id, item) for item in ids],
            ).rowcount

    @staticmethod
    def _media_folder(path: str) -> tuple[str, tuple[Any, ...]]:
        """Virtual paths only; predicates intersect the regular gallery filters."""
        linked = (
            "EXISTS (SELECT 1 FROM generations g JOIN workflows w"
            " ON w.id = g.workflow_id AND w.owner_id = g.owner_id"
            " WHERE g.id = media.generation_id AND g.owner_id = media.owner_id"
        )
        if path in ("", "Date"):
            return "", ()
        if path == "Workflow":
            return f"AND {linked})", ()
        if path == "Unsorted":
            return f"AND NOT {linked})", ()
        if path == "Favorites":
            return "AND favorite = 1", ()
        if path == "Videos":
            return "AND media_kind = 'video'", ()
        if path == "Audio":
            return "AND media_kind = 'audio'", ()
        if path == "Collections" or re.fullmatch(r"Collections/[a-zA-Z0-9_-]+", path):
            sql = "AND EXISTS (SELECT 1 FROM collection_media cm WHERE cm.media_id = media.id AND cm.owner_id = media.owner_id"
            return (
                (sql + ")", ())
                if path == "Collections"
                else (sql + " AND cm.collection_id = ?)", (path.split("/")[1],))
            )
        if re.fullmatch(r"Workflow/[a-zA-Z0-9_-]+", path):
            return f"AND {linked} AND w.id = ?)", (path.split("/")[1],)
        if re.fullmatch(r"Date/[0-9]{4}(?:/[0-9]{2}){0,2}", path):
            parts = [int(part) for part in path.split("/")[1:]]
            start = datetime(*parts, *([1] * (3 - len(parts))), tzinfo=timezone.utc)
            if len(parts) == 1:
                end = datetime(parts[0] + 1, 1, 1, tzinfo=timezone.utc)
            elif len(parts) == 2:
                year, month = parts
                end = datetime(
                    year + (month == 12), month % 12 + 1, 1, tzinfo=timezone.utc
                )
            else:
                end = start + timedelta(days=1)
            return "AND media.created_ms >= ? AND media.created_ms < ?", (
                (start - datetime(1970, 1, 1, tzinfo=timezone.utc))
                // timedelta(milliseconds=1),
                (end - datetime(1970, 1, 1, tzinfo=timezone.utc))
                // timedelta(milliseconds=1),
            )
        raise ValueError("Invalid virtual folder path")

    def media_tree(self, owner_id: str, path: str = "") -> dict[str, Any]:
        folder_sql, params = self._media_folder(path)
        base = f"FROM media WHERE owner_id = ? AND hidden = 0 {folder_sql}"
        args = (owner_id, *params)
        count = self.db.query_one(f"SELECT COUNT(*) AS n {base}", args)["n"]
        children = []
        breadcrumbs = [{"path": "", "name": "All media"}]
        parts = path.split("/") if path else []
        for index, name in enumerate(parts):
            if parts[0] == "Collections" and index == 1:
                collection = self.db.query_one(
                    "SELECT name FROM collections WHERE owner_id = ? AND id = ?",
                    (owner_id, name),
                )
                name = collection["name"] if collection else "Unknown collection"
            if parts[0] == "Workflow" and index == 1:
                workflow = self.get_workflow(owner_id, name)
                if workflow is None:
                    # Never reveal whether another owner's workflow exists.
                    name = "Unknown workflow"
                else:
                    name = workflow["name"]
            breadcrumbs.append({"path": "/".join(parts[: index + 1]), "name": name})
        if not path:
            for branch in (
                "Date",
                "Workflow",
                "Favorites",
                "Videos",
                "Audio",
                "Unsorted",
                "Collections",
            ):
                sql, values = self._media_folder(branch)
                n = self.db.query_one(
                    f"SELECT COUNT(*) AS n FROM media WHERE owner_id = ? AND hidden = 0 {sql}",
                    (owner_id, *values),
                )["n"]
                children.append({"path": branch, "name": branch, "count": n})
        elif parts[0] == "Date" and len(parts) < 4:
            format_ = ("%Y", "%m", "%d")[len(parts) - 1]
            rows = self.db.query(
                f"SELECT strftime('{format_}', created_ms / 1000.0, 'unixepoch') AS name,"
                f" COUNT(*) AS n {base} GROUP BY name ORDER BY name DESC",
                args,
            )
            children = [
                {
                    "path": f"{path}/{row['name']}",
                    "name": row["name"],
                    "count": row["n"],
                }
                for row in rows
                if row["name"] is not None
            ]
        elif path == "Collections":
            children = [
                {
                    "path": f"Collections/{row['id']}",
                    "name": row["name"],
                    "count": row["count"],
                }
                for row in self.list_collections(owner_id)
            ]
        elif path == "Workflow":
            rows = self.db.query(
                "SELECT w.id, w.name, COUNT(*) AS n FROM media"
                " JOIN generations g ON g.id = media.generation_id AND g.owner_id = media.owner_id"
                " JOIN workflows w ON w.id = g.workflow_id AND w.owner_id = media.owner_id"
                " WHERE media.owner_id = ? AND media.hidden = 0"
                " GROUP BY w.id ORDER BY w.name COLLATE NOCASE, w.id",
                (owner_id,),
            )
            children = [
                {
                    "path": f"Workflow/{row['id']}",
                    "name": row["name"],
                    "count": row["n"],
                }
                for row in rows
            ]
        return {
            "path": path,
            "timezone": "UTC",
            "count": count,
            "children": children,
            "breadcrumbs": breadcrumbs,
        }

    def list_media(
        self,
        owner_id: str,
        cursor: str | None = None,
        limit: int = PAGE_SIZE,
        *,
        sort: str = "newest",
        path: str = "",
        collection_id: str | None = None,
        media_kind: str | None = None,
        favorite: bool | None = None,
        workflow_id: str | None = None,
        generation_id: str | None = None,
        created_after: int | None = None,
        created_before: int | None = None,
        prompt: str | None = None,
        search_field: str = "any",
    ) -> PageResult:
        folder_sql, folder_params = self._media_folder(path)
        clauses = ["AND hidden = 0", folder_sql]
        params: list[Any] = list(folder_params)
        if collection_id is not None:
            sql, values = self._media_folder(f"Collections/{collection_id}")
            clauses.append(sql)
            params.extend(values)
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
        if generation_id is not None:
            clauses.append("AND media.generation_id = ?")
            params.append(generation_id)
        if created_after is not None:
            clauses.append("AND media.created_ms >= ?")
            params.append(created_after)
        if created_before is not None:
            clauses.append("AND media.created_ms <= ?")
            params.append(created_before)
        if prompt:
            search_sql, search_params = self._media_search(
                owner_id, prompt, search_field
            )
            clauses.append(search_sql)
            params.extend(search_params)
        if sort not in {"newest", "oldest", "random"}:
            raise ValueError("Unknown gallery sort")
        seed = secrets.token_hex(16) if sort == "random" else None
        keyset = ""
        position = None
        if cursor:
            try:
                raw = base64.b64decode(
                    cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
                )
                saved = json.loads(raw)
                if saved["sort"] != sort:
                    raise ValueError("Cursor sort does not match request")
                seed, position, row_id = saved["seed"], saved["key"], saved["id"]
                if not isinstance(row_id, str) or not row_id:
                    raise ValueError("Invalid cursor id")
                if sort == "random":
                    if not all(
                        isinstance(value, str)
                        and len(value) == length
                        and all(c in "0123456789abcdef" for c in value)
                        for value, length in ((seed, 32), (position, 64))
                    ):
                        raise ValueError("Invalid random cursor")
                elif (
                    seed is not None
                    or type(position) is not int
                    or not 0 <= position <= 9_223_372_036_854_775_807
                ):
                    raise ValueError("Invalid time cursor")
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
                raise ValueError("Malformed gallery cursor") from exc
        order_key = "created_ms"
        if sort == "random":
            # ponytail: random scans/sorts matching rows; persisted ranks if gallery scale requires it.
            self.db.connect().create_function(
                "gallery_rank",
                2,
                lambda seed, row_id: hashlib.sha256(
                    f"{seed}:{row_id}".encode()
                ).hexdigest(),
                deterministic=True,
            )
            order_key = "gallery_rank(?, id)"
        direction, comparison = ("DESC", "<") if sort == "newest" else ("ASC", ">")
        if position is not None:
            keyset = f"AND ({order_key}, id) {comparison} (?, ?)"
            if sort == "random":
                params.append(seed)
            params.extend((position, row_id))
        if sort == "random":
            params.append(seed)
        limit = max(1, min(limit, 200))
        rows = self.db.query(
            f"SELECT * FROM media WHERE owner_id = ? {' '.join(clauses)} {keyset}"
            f" ORDER BY {order_key} {direction}, id {direction} LIMIT ?",
            (owner_id, *params, limit + 1),
        )
        page = [dict(row) for row in rows[:limit]]
        memberships: dict[str, list[dict[str, str]]] = {item["id"]: [] for item in page}
        if page:
            for membership in self.db.query(
                "SELECT cm.media_id, c.id, c.name FROM collection_media cm"
                " JOIN collections c ON c.id = cm.collection_id AND c.owner_id = cm.owner_id"
                " WHERE cm.owner_id = ? AND cm.media_id IN (SELECT value FROM json_each(?))"
                " ORDER BY c.name COLLATE NOCASE, c.id",
                (owner_id, json.dumps(list(memberships))),
            ):
                memberships[membership["media_id"]].append(
                    {"id": membership["id"], "name": membership["name"]}
                )
        for item in page:
            item["collections"] = memberships[item["id"]]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            key = (
                hashlib.sha256(f"{seed}:{last['id']}".encode()).hexdigest()
                if sort == "random"
                else last["created_ms"]
            )
            raw = json.dumps(
                {"sort": sort, "seed": seed, "key": key, "id": last["id"]}
            ).encode()
            next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return PageResult(items=page, next_cursor=next_cursor)

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
