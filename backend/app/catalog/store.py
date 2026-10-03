"""Persistence for the cached catalog snapshot.

Follows the DATA-001 conventions in app/storage/: calls here block and are
reached from async code through ``in_thread``, writes go through
``Database.write()`` so they are one short transaction, and no network or
filesystem work happens inside that transaction.

It is deliberately *not* a method on ``Repository``: that module's contract is
that every read and write is owner-scoped, and the catalog is a single global
snapshot. Keeping it separate means ``Repository`` has no row that ignores
``owner_id``.

One row, ``id = 'current'``, in the ``catalogs`` table from migration 001:

* ``raw_json`` / ``normalized_json`` hold the **last good** catalog. A failed
  refresh never clears them.
* ``error_json`` holds the **last failure**, and is cleared by a success. It
  therefore coexists with good data: that is the ``stale`` state.
* ``fetched_ms`` is the last *successful* fetch, never a failed attempt.

``normalized_json`` carries the schema hash, the withheld choice lists and the
capability record, so no migration is needed to add them. Only the ``nodes``
half of it is ever projected to a client.
"""

from __future__ import annotations

import json
from typing import Any

from ..storage.db import Database
from ..storage.repository import now_ms

ROW_ID = "current"


class CatalogStore:
    """Blocking access to the single cached catalog row."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def load(self) -> dict[str, Any] | None:
        """The cached row, or None when this installation has never fetched."""
        row = self.db.query_one("SELECT * FROM catalogs WHERE id = ?", (ROW_ID,))
        if row is None:
            return None
        record = dict(row)
        record["normalized"] = (
            json.loads(record["normalized_json"]) if record["normalized_json"] else None
        )
        record["error"] = (
            json.loads(record["error_json"]) if record["error_json"] else None
        )
        return record

    def load_raw(self) -> Any | None:
        """The raw upstream payload. Server-side only — never returned to a client."""
        row = self.db.query_one("SELECT raw_json FROM catalogs WHERE id = ?", (ROW_ID,))
        return json.loads(row["raw_json"]) if row and row["raw_json"] else None

    def save_success(
        self,
        *,
        source_url: str,
        raw: Any,
        normalized: dict[str, Any],
        content_hash: str,
        fetched_ms: int | None = None,
    ) -> None:
        """Replace the snapshot atomically and clear the last error.

        The caller validates the payload's shape (normalization succeeded)
        before calling: a snapshot is only ever replaced by a validated one.
        """
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO catalogs (id, source_url, raw_json, normalized_json, content_hash,"
                " fetched_ms, state, error_json) VALUES (?, ?, ?, ?, ?, ?, 'fresh', NULL)"
                " ON CONFLICT (id) DO UPDATE SET source_url = excluded.source_url,"
                " raw_json = excluded.raw_json, normalized_json = excluded.normalized_json,"
                " content_hash = excluded.content_hash, fetched_ms = excluded.fetched_ms,"
                " state = 'fresh', error_json = NULL",
                (
                    ROW_ID,
                    source_url,
                    json.dumps(raw, separators=(",", ":")),
                    json.dumps(normalized, separators=(",", ":")),
                    content_hash,
                    fetched_ms if fetched_ms is not None else now_ms(),
                ),
            )

    def record_failure(self, *, source_url: str, error: dict[str, Any]) -> None:
        """Record a failed attempt **without** touching the last good catalog.

        With cached data present this is ``stale``; with none it is ``error``,
        which is the explicit first-run-offline state.
        """
        error = {**error, "at_ms": now_ms()}
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO catalogs (id, source_url, raw_json, normalized_json, content_hash,"
                " fetched_ms, state, error_json) VALUES (?, ?, NULL, NULL, '', 0, 'error', ?)"
                " ON CONFLICT (id) DO UPDATE SET error_json = excluded.error_json,"
                " state = CASE WHEN catalogs.normalized_json IS NULL THEN 'error' ELSE 'stale' END",
                (ROW_ID, source_url, json.dumps(error, separators=(",", ":"))),
            )
