"""Private uploads forwarded to ComfyUI loader inputs.

Distinct from ``app/media/service.py``'s owned output gallery, but the same
approach carries over directly: opaque per-profile IDs, validated paths,
streamed I/O, never a client-supplied path or filename trusted as a location.

* An upload is validated and written to our own private store first (the
  canonical copy), streamed in bounded chunks so a large video is never fully
  buffered in memory.
* A supported upload is then forwarded through ComfyUI's upload API into a
  per-profile subfolder. The private copy remains canonical.
* Abandoned uploads (past a grace period, not referenced by any retained
  generation snapshot) are swept lazily on next access -- no scheduler exists
  in this codebase and this task does not add one.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..media.service import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..settings.service import SettingsStore
from ..storage.db import Database, in_thread
from ..storage.repository import PageResult, new_id, now_ms

#: Masks are uploaded files bound to the same LoadImage-style loader as a
#: reference image (its MASK output is index 1); there is no separate mask
#: node contract to support, so masks are simply PNGs -- no drawing editor.
MASK_EXTENSIONS = frozenset({".png"})

_KIND_EXTENSIONS: dict[str, frozenset[str]] = {
    "image": IMAGE_EXTENSIONS,
    "mask": MASK_EXTENSIONS,
    "video": VIDEO_EXTENSIONS,
    "audio": AUDIO_EXTENSIONS | VIDEO_EXTENSIONS,
}

#: Bounded read size: memory use for even a multi-GB video stays flat.
CHUNK_BYTES = 1024 * 1024
DEFAULT_GRACE_MS = 24 * 60 * 60 * 1000
_SWEEP_INTERVAL_S = 300.0

#: Kinds supported by ComfyUI's /upload/image byte-stream endpoint.
STAGEABLE_KINDS = frozenset({"image", "video", "audio"})


class UploadError(ValueError):
    """An upload request cannot be satisfied. ``code`` is client-safe."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Cursor:
    created_ms: int
    id: str


class UploadService:
    """Blocking service; routes run every public operation in a worker thread."""

    def __init__(
        self, db: Database, settings: SettingsStore, data_dir: Path, comfy: Any
    ) -> None:
        self.db = db
        self.settings = settings
        self.private_root = (data_dir / "uploads").resolve()
        self.private_root.mkdir(parents=True, exist_ok=True)
        self._last_sweep = 0.0
        self.comfy = comfy

    # --- validation ---------------------------------------------------------

    def validate(self, kind: str, filename: str) -> str:
        """The lowercased extension for ``filename``, or raise ``UploadError``."""
        allowed = _KIND_EXTENSIONS.get(kind)
        if allowed is None:
            raise UploadError("unsupported_kind", f"Unsupported upload kind {kind!r}.")
        ext = Path(filename or "").suffix.lower()
        if ext not in allowed:
            raise UploadError(
                "unsupported_type",
                f"{ext or 'That file type'} is not accepted for a {kind} upload. "
                f"Supported extensions: {', '.join(sorted(allowed))}.",
            )
        return ext

    def max_bytes(self) -> int:
        return int(self.settings.host_value("upload_max_bytes"))

    # --- accepting a streamed upload -----------------------------------------

    def temp_path(self, owner_id: str, ext: str) -> tuple[str, Path]:
        """A fresh opaque id and the (not yet existing) temp file to write into."""
        upload_id = new_id()
        owner_dir = self.private_root / owner_id
        owner_dir.mkdir(parents=True, exist_ok=True)
        return upload_id, owner_dir / f"{upload_id}{ext}.part"

    def finalize(
        self, owner_id: str, upload_id: str, ext: str, kind: str, temp: Path, size: int
    ) -> dict[str, Any]:
        """Validate the fully-written temp file, commit it, and record the row.

        The caller has already streamed and size-bounded the body; this step
        only validates content and performs the atomic rename + DB insert.
        """
        target = temp.with_suffix("")  # "<id><ext>.part" -> "<id><ext>"
        try:
            if kind in ("image", "mask"):
                self._verify_image(temp)
            os.replace(temp, target)
        except (OSError, UploadError):
            temp.unlink(missing_ok=True)
            raise
        relative = f"{owner_id}/{target.name}"
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO uploads (id, owner_id, storage_path, staged_name, media_type,"
                " byte_size, state, created_ms) VALUES (?, ?, ?, NULL, ?, ?, 'ready', ?)",
                (upload_id, owner_id, relative, media_type, str(size), now_ms()),
            )
        row = self.get(owner_id, upload_id)
        assert row is not None  # just inserted under this same owner_id
        return row

    def _verify_image(self, path: Path) -> None:
        try:
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise UploadError(
                "invalid_image", "That file is not a readable image."
            ) from exc

    # --- reading --------------------------------------------------------------

    def get(self, owner_id: str, upload_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM uploads WHERE id = ? AND owner_id = ?", (upload_id, owner_id)
        )
        return dict(row) if row else None

    def classify(self, row: dict[str, Any]) -> str:
        """image/video/audio/other, derived from the stored extension."""
        suffix = Path(row["storage_path"]).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in AUDIO_EXTENSIONS:
            return "audio"
        return "other"

    def list_uploads(
        self,
        owner_id: str,
        cursor: str | None,
        limit: int,
        media_kind: str | None = None,
    ) -> PageResult:
        """Filtered, cursor-paginated listing for "selection filtering" in the UI.

        # ponytail: loads one owner's whole upload list into Python to filter by
        # kind, rather than a SQL WHERE on a stored column. Fine at personal-app
        # scale (a handful of staged inputs); if that ever changes, store kind
        # as a column and filter in SQL like Repository.list_media does.
        """
        self._maybe_sweep()
        limit = max(1, min(limit, 200))
        rows = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM uploads WHERE owner_id = ? ORDER BY created_ms DESC, id DESC",
                (owner_id,),
            )
        ]
        if media_kind:
            rows = [r for r in rows if self.classify(r) == media_kind]
        start = 0
        if cursor:
            after = _decode_cursor(cursor)
            start = len(rows)
            for index, row in enumerate(rows):
                if (row["created_ms"], row["id"]) < (after.created_ms, after.id):
                    start = index
                    break
        page = rows[start : start + limit]
        next_cursor = (
            _encode_cursor(page[-1]) if len(rows) > start + limit and page else None
        )
        return PageResult(items=page, next_cursor=next_cursor)

    # --- staging through ComfyUI's upload API ----------------------------------

    async def ensure_staged(
        self, owner_id: str, upload_id: str, expected_kind: str
    ) -> str:
        """Upload the private copy once; return the path ComfyUI expects.

        Never trusts anything but our own id -> path lookups: a foreign or
        unknown ``upload_id`` raises the same ``upload_missing`` diagnostic a
        deleted one would, so an attacker (or a stale UI) cannot distinguish
        "not yours" from "does not exist".
        """
        row = await in_thread(self.get, owner_id, upload_id)
        if row is None:
            raise UploadError(
                "upload_missing", "That upload was not found; choose a replacement."
            )
        kind = self.classify(row)
        if expected_kind not in STAGEABLE_KINDS or kind not in STAGEABLE_KINDS:
            raise UploadError(
                "unsupported_loader_adapter",
                f"No ComfyUI upload route is established for {kind} inputs yet.",
            )
        if kind != expected_kind and not (expected_kind == "audio" and kind == "video"):
            raise UploadError(
                "upload_kind_mismatch", f"This control needs a {expected_kind} file."
            )
        if row["staged_name"]:
            return row["staged_name"]
        source = self.private_root / row["storage_path"]
        if not source.is_file():
            raise UploadError(
                "upload_missing",
                "The uploaded file is no longer available; choose a replacement.",
            )
        ext = Path(row["storage_path"]).suffix.lower()
        subfolder = f"simpleui/{owner_id}"
        name = f"{row['id']}{ext}"
        result = await self.comfy.upload_image(
            source, filename=name, subfolder=subfolder
        )
        if not isinstance(result, dict):
            raise UploadError(
                "invalid_upload_response",
                "ComfyUI returned an invalid upload response.",
            )
        returned_name, returned_subfolder, returned_type = (
            result.get("name"),
            result.get("subfolder"),
            result.get("type"),
        )
        if (
            not isinstance(returned_name, str)
            or Path(returned_name).name != returned_name
            or "\\" in returned_name
            or ":" in returned_name
            or any(ord(char) < 32 for char in returned_name)
            or not returned_name.lower().endswith(ext)
            or returned_subfolder != subfolder
            or returned_type != "input"
        ):
            raise UploadError(
                "invalid_upload_response",
                "ComfyUI returned an unexpected upload location.",
            )
        staged_name = f"{returned_subfolder}/{returned_name}"
        await in_thread(self._save_staged_name, owner_id, row["id"], staged_name)
        return staged_name

    def _save_staged_name(
        self, owner_id: str, upload_id: str, staged_name: str
    ) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE uploads SET staged_name = ? WHERE id = ? AND owner_id = ?",
                (staged_name, upload_id, owner_id),
            )

    # --- deletion and grace-period cleanup ------------------------------------

    def delete(self, owner_id: str, upload_id: str) -> bool:
        """Explicit user-requested removal, regardless of the grace period."""
        row = self.get(owner_id, upload_id)
        if row is None:
            return False
        self._purge(row)
        return True

    def _purge(self, row: dict[str, Any]) -> None:
        # Files first: a crash here leaves a DB row (still discoverable and
        # retried by the next sweep or delete), never an orphan file with no
        # record of it.
        (self.private_root / row["storage_path"]).unlink(missing_ok=True)
        # ComfyUI owns its uploaded copy; no filesystem path is available here.
        with self.db.write() as conn:
            conn.execute("DELETE FROM uploads WHERE id = ?", (row["id"],))

    def _maybe_sweep(self) -> None:
        now = time.monotonic()
        if now - self._last_sweep < _SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        self.sweep_abandoned()

    def sweep_abandoned(
        self, *, grace_ms: int = DEFAULT_GRACE_MS, now: int | None = None
    ) -> int:
        """Delete uploads older than ``grace_ms`` that no retained generation
        snapshot still references. Called lazily (see ``_maybe_sweep``); this
        codebase has no background job scheduler and this task does not add one.
        """
        cutoff = (now_ms() if now is None else now) - grace_ms
        rows = [
            dict(r)
            for r in self.db.query(
                "SELECT * FROM uploads WHERE created_ms < ?", (cutoff,)
            )
        ]
        removed = 0
        for row in rows:
            if self._is_referenced(row):
                continue
            self._purge(row)
            removed += 1
        return removed

    def _is_referenced(self, row: dict[str, Any]) -> bool:
        """Kept while any generation with a retained snapshot mentions it.

        A generation's submission graph is a copy (mapping/submission.py) that
        carries the *staged filename*, never the raw upload id, so that is what
        is searched for. ``graph_json``/``effective_values_json`` are NULL once
        history retention purges them (storage/repository.py
        purge_generation_snapshot) -- at that point the generation itself no
        longer references the file, regardless of status.

        # ponytail: a LIKE substring scan over retained generations, O(rows).
        # Fine at personal-app scale; if this needs an index, the upgrade is a
        # generation_uploads join table populated by GEN-001 at submission time.
        """
        needle = row["staged_name"] or row["id"]
        pattern = f"%{needle}%"
        hit = self.db.query_one(
            "SELECT 1 FROM generations WHERE (graph_json LIKE ? OR effective_values_json LIKE ?) LIMIT 1",
            (pattern, pattern),
        )
        return hit is not None


def _encode_cursor(row: dict[str, Any]) -> str:
    raw = f"{row['created_ms']}:{row['id']}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> _Cursor:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_ms, _, row_id = base64.urlsafe_b64decode(padded).decode().partition(":")
        return _Cursor(created_ms=int(created_ms), id=row_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Malformed cursor: {cursor!r}") from exc
