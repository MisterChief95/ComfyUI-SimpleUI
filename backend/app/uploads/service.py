"""Private, streamed uploads staged for ComfyUI loader inputs.

Distinct from ``app/media/service.py``'s owned output gallery, but the same
approach carries over directly: opaque per-profile IDs, validated paths,
streamed I/O, never a client-supplied path or filename trusted as a location.

* An upload is validated and written to our own private store first (the
  canonical copy), streamed in bounded chunks so a large video is never fully
  buffered in memory.
* A supported (image/mask) upload is then staged as a derived copy into the
  configured ComfyUI input directory, under a per-profile subfolder --
  "scoped ComfyUI input staging" -- so one profile's filename can never
  collide with or be substituted for another's. Video has no established
  ComfyUI upload route (docs/ARCHITECTURE.md), so it is stored but never
  staged; ``app/mapping/input_adapters.py`` turns that into an actionable
  diagnostic rather than a silent failure.
* Abandoned uploads (past a grace period, not referenced by any retained
  generation snapshot) are swept lazily on next access -- no scheduler exists
  in this codebase and this task does not add one.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..media.service import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from ..settings.service import SettingsStore
from ..storage.db import Database
from ..storage.repository import PageResult, new_id, now_ms

#: Masks are uploaded files bound to the same LoadImage-style loader as a
#: reference image (its MASK output is index 1); there is no separate mask
#: node contract to support, so masks are simply PNGs -- no drawing editor.
MASK_EXTENSIONS = frozenset({".png"})

_KIND_EXTENSIONS: dict[str, frozenset[str]] = {
    "image": IMAGE_EXTENSIONS,
    "mask": MASK_EXTENSIONS,
    "video": VIDEO_EXTENSIONS,
}

#: Bounded read size: memory use for even a multi-GB video stays flat.
CHUNK_BYTES = 1024 * 1024
DEFAULT_GRACE_MS = 24 * 60 * 60 * 1000
_SWEEP_INTERVAL_S = 300.0

#: Only a bare filename under the ComfyUI input directory is an established
#: upload contract today (LoadImage's ``image`` input). Anything else --
#: notably video -- has no adapter and is refused with a diagnostic instead
#: of a guess (docs/ARCHITECTURE.md "Uploads are stored privately...").
#: ``classify()`` never returns "mask" (a mask is just a PNG, classified
#: "image"); it is bound through this very same adapter, per the LoadImage
#: MASK-output convention module docstrings above describe.
STAGEABLE_KINDS = frozenset({"image"})


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

    def __init__(self, db: Database, settings: SettingsStore, data_dir: Path) -> None:
        self.db = db
        self.settings = settings
        self.private_root = (data_dir / "uploads").resolve()
        self.private_root.mkdir(parents=True, exist_ok=True)
        self._last_sweep = 0.0

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
            raise UploadError("invalid_image", "That file is not a readable image.") from exc

    # --- reading --------------------------------------------------------------

    def get(self, owner_id: str, upload_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM uploads WHERE id = ? AND owner_id = ?", (upload_id, owner_id)
        )
        return dict(row) if row else None

    def classify(self, row: dict[str, Any]) -> str:
        """image/video/other, derived from the stored extension (no extra column)."""
        suffix = Path(row["storage_path"]).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        return "other"

    def list_uploads(
        self, owner_id: str, cursor: str | None, limit: int, media_kind: str | None = None
    ) -> PageResult:
        """Filtered, cursor-paginated listing for "selection filtering" in the UI.

        # ponytail: loads one owner's whole upload list into Python to filter by
        # kind, rather than a SQL WHERE on a stored column. Fine at personal-app
        # scale (a handful of staged inputs); if that ever changes, store kind
        # as a column and filter in SQL like Repository.list_media does.
        """
        self._maybe_sweep()
        limit = max(1, min(limit, 200))
        rows = [dict(r) for r in self.db.query(
            "SELECT * FROM uploads WHERE owner_id = ? ORDER BY created_ms DESC, id DESC",
            (owner_id,),
        )]
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
        next_cursor = _encode_cursor(page[-1]) if len(rows) > start + limit and page else None
        return PageResult(items=page, next_cursor=next_cursor)

    # --- staging into ComfyUI's input directory -------------------------------

    def ensure_staged(self, owner_id: str, upload_id: str) -> str:
        """Idempotently stage the upload; return the filename ComfyUI expects.

        Never trusts anything but our own id -> path lookups: a foreign or
        unknown ``upload_id`` raises the same ``upload_missing`` diagnostic a
        deleted one would, so an attacker (or a stale UI) cannot distinguish
        "not yours" from "does not exist".
        """
        row = self.get(owner_id, upload_id)
        if row is None:
            raise UploadError("upload_missing", "That upload was not found; choose a replacement.")
        kind = self.classify(row)
        if kind not in STAGEABLE_KINDS:
            raise UploadError(
                "unsupported_loader_adapter",
                f"No ComfyUI upload route is established for {kind} inputs yet.",
            )
        if row["staged_name"]:
            staged = self._safe_join(self._input_root(), row["staged_name"])
            if staged is not None and staged.is_file():
                return row["staged_name"]
            # The staged copy is gone (input folder cleared, moved, ...); restage.
        source = self.private_root / row["storage_path"]
        if not source.is_file():
            raise UploadError(
                "upload_missing", "The uploaded file is no longer available; choose a replacement."
            )
        input_root = self._input_root()
        staged_relative = f"simpleui/{owner_id}/{row['id']}{Path(row['storage_path']).suffix}"
        target = self._safe_join(input_root, staged_relative)
        if target is None:
            raise UploadError(
                "input_dir_unavailable", "The configured ComfyUI input folder is unusable."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise UploadError(
                "input_dir_unavailable", "Could not stage the upload into the ComfyUI input folder."
            ) from exc
        with self.db.write() as conn:
            conn.execute(
                "UPDATE uploads SET staged_name = ? WHERE id = ? AND owner_id = ?",
                (staged_relative, row["id"], owner_id),
            )
        return staged_relative

    def _input_root(self) -> Path:
        raw = self.settings.host_value("comfy_input_dir")
        if not raw:
            raise UploadError(
                "input_dir_unavailable",
                "Choose a ComfyUI input folder in local settings before using uploaded inputs.",
            )
        try:
            root = Path(raw).resolve(strict=True)
        except OSError as exc:
            raise UploadError(
                "input_dir_unavailable", "The configured ComfyUI input folder is unavailable."
            ) from exc
        if not root.is_dir():
            raise UploadError(
                "input_dir_unavailable", "The configured ComfyUI input folder is not a directory."
            )
        return root

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
        if row["staged_name"]:
            try:
                staged = self._safe_join(self._input_root(), row["staged_name"])
            except UploadError:
                staged = None
            if staged is not None:
                staged.unlink(missing_ok=True)
        with self.db.write() as conn:
            conn.execute("DELETE FROM uploads WHERE id = ?", (row["id"],))

    def _maybe_sweep(self) -> None:
        now = time.monotonic()
        if now - self._last_sweep < _SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        self.sweep_abandoned()

    def sweep_abandoned(self, *, grace_ms: int = DEFAULT_GRACE_MS, now: int | None = None) -> int:
        """Delete uploads older than ``grace_ms`` that no retained generation
        snapshot still references. Called lazily (see ``_maybe_sweep``); this
        codebase has no background job scheduler and this task does not add one.
        """
        cutoff = (now_ms() if now is None else now) - grace_ms
        rows = [dict(r) for r in self.db.query("SELECT * FROM uploads WHERE created_ms < ?", (cutoff,))]
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

    # --- path safety (mirrors media/service.py's junction/symlink guard) -----

    def _safe_under(self, root: Path, path: Path) -> bool:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return False
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                return False
        return True

    def _safe_join(self, root: Path, relative: str) -> Path | None:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            return None
        path = root / candidate
        return path if self._safe_under(root, path) else None


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
