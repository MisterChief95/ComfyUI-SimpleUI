"""Filesystem media work kept behind owned database rows and validated roots."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..auth.service import BaselineStatus
from ..generations.store import TERMINAL
from ..settings.service import SettingsStore
from ..storage.db import Database
from ..storage.repository import DEFAULT_PROFILE_ID, Repository, new_id, now_ms

IMAGE_EXTENSIONS = frozenset(
    {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
VIDEO_EXTENSIONS = frozenset({".avi", ".mkv", ".mov", ".mp4", ".webm"})


class MediaError(ValueError):
    pass


@dataclass(frozen=True)
class LocatedMedia:
    row: dict[str, Any]
    path: Path


def _identity(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}"


def _version(path: Path) -> str:
    """Identity *and* version.

    ``media_ingestion_key`` (migration 001) is unique on
    ``(generation_id, output_node, ordinal, file_version)``, so for indexed
    files -- which have no generation, node or ordinal -- ``file_version`` is
    the whole key. Size+mtime alone collides between two distinct files
    written in the same tick, which aborted the scan with an IntegrityError.
    Including dev:ino keeps distinct files distinct, and a rewrite still
    changes size/mtime (or ino, for a copy-and-replace), so a changed file
    still invalidates its old row.
    """
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"


def _source_id(path: Path) -> str:
    return hashlib.sha256(str(path).casefold().encode()).hexdigest()[:32]


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "other"


def _type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class MediaService:
    """Blocking service; routes run every public operation in a worker thread."""

    def __init__(self, db: Database, settings: SettingsStore, data_dir: Path) -> None:
        self.db = db
        self.settings = settings
        self.data_dir = data_dir.resolve()
        self.captures = self.data_dir / "captures"
        self.thumbnails = self.data_dir / "thumbnails"
        self.captures.mkdir(parents=True, exist_ok=True)
        self.thumbnails.mkdir(parents=True, exist_ok=True)
        self._ensure_capture_source()

    def _ensure_capture_source(self) -> None:
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO media_sources (id, root_path, root_identity, baseline_complete, updated_ms)"
                " VALUES ('captures', ?, ?, 1, ?) ON CONFLICT(id) DO NOTHING",
                (str(self.captures), _identity(self.captures), now_ms()),
            )

    def baseline_status(self, _db: Database | None = None) -> BaselineStatus:
        """AuthService.baseline_gate seam: may multi-user mode be enabled yet?"""
        try:
            root = self._configured_root()
        except MediaError as exc:
            return BaselineStatus(False, str(exc))
        row = self.db.query_one(
            "SELECT root_identity, baseline_complete FROM media_sources WHERE id = ?",
            (_source_id(root),),
        )
        if (
            row is None
            or not row["baseline_complete"]
            or row["root_identity"] != _identity(root)
        ):
            return BaselineStatus(
                False,
                "Run the local media import for the current ComfyUI output folder before enabling multi-user mode.",
            )
        return BaselineStatus(True, "")

    def import_baseline(self, *, explicit: bool = False) -> dict[str, int]:
        """Index the configured root's pre-existing files under Default.

        The boundary manifest is written before any row is indexed, so an
        interrupted scan resumes against the file set it started with and files
        that appeared meanwhile stay unresolved rather than joining Default.
        """
        root = self._configured_root()
        source_id, identity = _source_id(root), _identity(root)
        row = self.db.query_one(
            "SELECT * FROM media_sources WHERE id = ?", (source_id,)
        )
        established = (
            row is not None
            and row["root_identity"] == identity
            and bool(row["baseline_boundary_json"])
        )
        if not established and not explicit:
            # Either a different folder, or a different volume behind the same
            # path. Both would hand somebody else's files to Default.
            swapped = row is not None and row["root_identity"] != identity
            others = self.db.query_one(
                "SELECT 1 FROM media_sources WHERE id NOT IN ('captures', ?) AND baseline_complete = 1",
                (source_id,),
            )
            if swapped or others:
                raise MediaError(
                    "The ComfyUI output folder changed. Confirm an explicit local import before "
                    "assigning another folder's files to Default."
                )
        if established:
            boundary = json.loads(row["baseline_boundary_json"])
        else:
            boundary = self._manifest(root)
            with self.db.write() as conn:
                conn.execute(
                    "INSERT INTO media_sources (id, root_path, root_identity, baseline_boundary_json,"
                    " baseline_complete, import_required, updated_ms) VALUES (?, ?, ?, ?, 0, 0, ?)"
                    " ON CONFLICT(id) DO UPDATE SET root_path = excluded.root_path,"
                    " root_identity = excluded.root_identity,"
                    " baseline_boundary_json = excluded.baseline_boundary_json,"
                    " baseline_complete = 0, import_required = 0, updated_ms = excluded.updated_ms",
                    (
                        source_id,
                        str(root),
                        identity,
                        json.dumps(boundary, separators=(",", ":")),
                        now_ms(),
                    ),
                )
        return self._scan_boundary(source_id, root, boundary)

    def _configured_root(self) -> Path:
        raw = self.settings.host_value("comfy_output_dir")
        if not raw:
            raise MediaError(
                "Choose a ComfyUI output folder and run the local media import."
            )
        try:
            root = Path(raw).resolve(strict=True)
        except OSError as exc:
            raise MediaError(
                "The configured ComfyUI output folder is unavailable."
            ) from exc
        if not root.is_dir():
            raise MediaError("The configured ComfyUI output folder is not a directory.")
        return root

    def _manifest(self, root: Path) -> dict[str, str]:
        found: dict[str, str] = {}
        for directory, subdirectories, names in os.walk(root):
            here = Path(directory)
            # Prune links before descending. A junction may point at an
            # ancestor, and rglob() walks into junctions happily -- forever.
            subdirectories[:] = [
                name
                for name in subdirectories
                if not (here / name).is_symlink() and not (here / name).is_junction()
            ]
            for name in names:
                path = here / name
                if _kind(path) == "other" or path.is_symlink() or not path.is_file():
                    continue
                found[path.relative_to(root).as_posix()] = _version(path)
        return found

    def _scan_boundary(
        self, source_id: str, root: Path, boundary: dict[str, str]
    ) -> dict[str, int]:
        indexed = 0
        for relative in boundary:
            path = self._safe_path(root, relative)
            if path is None or not path.is_file():
                # Deleted or moved since the boundary was taken. The card stays
                # and says so; nothing here deletes the user's own rows.
                with self.db.write() as conn:
                    conn.execute(
                        "UPDATE media SET state = 'unavailable' WHERE id IN (SELECT media_id"
                        " FROM media_locations WHERE source_id = ? AND relative_path = ?)",
                        (source_id, relative),
                    )
                continue
            self._index(DEFAULT_PROFILE_ID, source_id, relative, path, state="indexed")
            indexed += 1
        outside = [
            (source_id, relative, version, now_ms())
            for relative, version in self._manifest(root).items()
            if relative not in boundary
        ]
        with self.db.write() as conn:
            # Discovered after the boundary: ownership is unprovable, so these
            # get no owner and no gallery row until someone resolves them here.
            unresolved = conn.executemany(
                "INSERT OR IGNORE INTO unresolved_media (source_id, relative_path, file_version,"
                " discovered_ms) VALUES (?, ?, ?, ?)",
                outside,
            ).rowcount
            conn.execute(
                "UPDATE media_sources SET baseline_complete = 1, import_required = 0, updated_ms = ? WHERE id = ?",
                (now_ms(), source_id),
            )
        return {"indexed": indexed, "unresolved": max(unresolved, 0)}

    def _index(
        self,
        owner_id: str,
        source_id: str,
        relative: str,
        path: Path,
        *,
        state: str,
        generation_id: str | None = None,
        output_node: str | None = None,
        ordinal: int | None = None,
    ) -> str:
        version = _version(path)
        existing = self.db.query_one(
            "SELECT media_id FROM media_locations WHERE source_id = ? AND relative_path = ?",
            (source_id, relative),
        )
        if existing is not None:
            row = self.db.query_one(
                "SELECT id, state FROM media WHERE id = ? AND file_version = ?",
                (existing["media_id"], version),
            )
            if row:
                if row["state"] != state:  # e.g. a file that came back
                    with self.db.write() as conn:
                        conn.execute(
                            "UPDATE media SET state = ? WHERE id = ?",
                            (state, row["id"]),
                        )
                return row["id"]
        media_id = new_id()
        with self.db.write() as conn:
            if existing is not None:
                conn.execute("DELETE FROM media WHERE id = ?", (existing["media_id"],))
            conn.execute(
                "INSERT INTO media (id, owner_id, generation_id, output_node, ordinal, storage_path, file_version, media_kind, media_type, state, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    media_id,
                    owner_id,
                    generation_id,
                    output_node,
                    ordinal,
                    relative,
                    version,
                    _kind(path),
                    _type(path),
                    state,
                    now_ms(),
                ),
            )
            conn.execute(
                "INSERT INTO media_locations (media_id, source_id, relative_path) VALUES (?, ?, ?)",
                (media_id, source_id, relative),
            )
        return media_id

    def capture_output(
        self,
        owner_id: str,
        generation_id: str | None,
        source_path: str,
        *,
        output_node: str | None = None,
        ordinal: int | None = None,
        preview: bool = False,
    ) -> str | None:
        """Copy a verified output into private storage; previews intentionally create no card."""
        if preview:
            return None
        source = self._configured_source()
        path = self._safe_path(source[1], source_path)
        if path is None or not path.is_file():
            raise MediaError(
                "Output path is outside the configured output folder or unavailable."
            )
        version = _version(path)
        relative = path.relative_to(source[1]).as_posix()
        # ComfyUI's output folder is shared between profiles, so a result
        # descriptor naming a file is not proof of ownership of that file.
        if self.db.query_one(
            "SELECT 1 FROM media m JOIN media_locations l ON l.media_id = m.id"
            " WHERE l.source_id = ? AND l.relative_path = ? AND m.owner_id != ?"
            " UNION ALL SELECT 1 FROM capture_attempts WHERE source_id = ? AND relative_path = ?"
            " AND owner_id != ? AND state = 'ready'",
            (source[0], relative, owner_id, source[0], relative, owner_id),
        ):
            raise MediaError("That output file already belongs to another profile.")
        attempt = self.db.query_one(
            "SELECT * FROM capture_attempts WHERE owner_id = ? AND IFNULL(generation_id, '') = IFNULL(?, '') AND source_id = ? AND relative_path = ? AND IFNULL(output_node, '') = IFNULL(?, '') AND IFNULL(ordinal, -1) = IFNULL(?, -1) AND file_version = ?",
            (
                owner_id,
                generation_id,
                source[0],
                relative,
                output_node,
                ordinal,
                version,
            ),
        )
        # media_id is NULL once the indexed row was invalidated (ON DELETE SET
        # NULL), so "ready" alone is not proof the card still exists.
        if attempt and attempt["state"] == "ready" and attempt["media_id"]:
            return attempt["media_id"]
        attempt_id = attempt["id"] if attempt else new_id()
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO capture_attempts (id, owner_id, generation_id, source_id, relative_path, file_version, output_node, ordinal, state, updated_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?) ON CONFLICT(id) DO UPDATE SET state = 'pending', error = NULL, updated_ms = excluded.updated_ms",
                (
                    attempt_id,
                    owner_id,
                    generation_id,
                    source[0],
                    relative,
                    version,
                    output_node,
                    ordinal,
                    now_ms(),
                ),
            )
        target_relative = f"{owner_id}/{attempt_id}{path.suffix.lower()}"
        target = self.captures / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            shutil.copyfile(path, temporary)
            os.replace(temporary, target)
            media_id = self._index(
                owner_id,
                "captures",
                target_relative,
                target,
                state="captured",
                generation_id=generation_id,
                output_node=output_node,
                ordinal=ordinal,
            )
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE capture_attempts SET state = 'failed', error = ?, updated_ms = ? WHERE id = ?",
                    (str(exc), now_ms(), attempt_id),
                )
            raise MediaError(
                "Could not capture the output; retry ingestion without rerunning generation."
            ) from exc
        with self.db.write() as conn:
            conn.execute(
                "UPDATE capture_attempts SET state = 'ready', media_id = ?, updated_ms = ? WHERE id = ?",
                (media_id, now_ms(), attempt_id),
            )
        return media_id

    def _configured_source(self) -> tuple[str, Path]:
        root = self._configured_root()
        source_id = _source_id(root)
        if (
            self.db.query_one("SELECT id FROM media_sources WHERE id = ?", (source_id,))
            is None
        ):
            raise MediaError("Run the local media import before capturing outputs.")
        return source_id, root

    def locate(self, owner_id: str, media_id: str) -> LocatedMedia | None:
        row = self.db.query_one(
            "SELECT m.*, l.source_id, l.relative_path, s.root_path FROM media m JOIN media_locations l ON l.media_id = m.id JOIN media_sources s ON s.id = l.source_id WHERE m.id = ? AND m.owner_id = ?",
            (media_id, owner_id),
        )
        if row is None:
            return None
        root = Path(row["root_path"])
        path = self._safe_path(root, row["relative_path"])
        if path is None or not path.is_file() or _version(path) != row["file_version"]:
            return None
        return LocatedMedia(dict(row), path)

    def thumbnail(self, owner_id: str, media_id: str) -> Path | None:
        """None means "no thumbnail": the caller 404s and the UI shows a placeholder.

        # ponytail: images only. A video poster needs a decoder (FFmpeg), which
        # ARCHITECTURE.md keeps optional, so videos take the placeholder path
        # until an FFmpeg probe is wired in here.
        """
        located = self.locate(owner_id, media_id)
        if located is None or located.row["media_kind"] != "image":
            return None
        target = self.thumbnails / f"{media_id}.jpg"
        if target.is_file():
            return target
        temporary = target.with_suffix(".part")
        try:
            with Image.open(located.path) as image:
                image.thumbnail((512, 512))
                image.convert("RGB").save(temporary, "JPEG", quality=82)
            os.replace(temporary, target)
        except (
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            ValueError,
        ):
            temporary.unlink(missing_ok=True)
            return None
        return target

    def list_media(
        self,
        owner_id: str,
        cursor: str | None,
        limit: int,
        *,
        media_kind: str | None = None,
        favorite: bool | None = None,
        workflow_id: str | None = None,
        generation_id: str | None = None,
        created_after: int | None = None,
        created_before: int | None = None,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        page = Repository(self.db).list_media(
            owner_id,
            cursor,
            limit,
            media_kind=media_kind,
            favorite=favorite,
            workflow_id=workflow_id,
            generation_id=generation_id,
            created_after=created_after,
            created_before=created_before,
            prompt=prompt,
        )
        return {
            "items": [self.public_item(row) for row in page.items],
            "next_cursor": page.next_cursor,
        }

    def clear_history(self, owner_id: str) -> dict[str, int]:
        """Purge only snapshots whose terminal state and outputs are proven."""
        purgeable = tuple(sorted(TERMINAL - {"unknown"}))
        placeholders = ",".join("?" for _ in purgeable)
        snapshot = "(graph_json IS NOT NULL OR effective_values_json IS NOT NULL)"
        with self.db.write() as conn:
            purged = conn.execute(
                "UPDATE generations SET graph_json = NULL, effective_values_json = NULL,"
                f" updated_ms = ? WHERE owner_id = ? AND {snapshot}"
                f" AND status IN ({placeholders}) AND output_state != 'pending'",
                (now_ms(), owner_id, *purgeable),
            ).rowcount
            deferred = conn.execute(
                f"SELECT COUNT(*) FROM generations WHERE owner_id = ? AND {snapshot}",
                (owner_id,),
            ).fetchone()[0]
        return {"purged": purged, "deferred": deferred}

    @staticmethod
    def public_item(row: dict[str, Any]) -> dict[str, Any]:
        """Owned fields only. generation_id is None for indexed files: the UI
        shows their prompt/workflow as unknown rather than inventing one."""
        item = {
            key: row[key]
            for key in (
                "id",
                "generation_id",
                "media_kind",
                "media_type",
                "state",
                "favorite",
                "created_ms",
            )
        }
        item["filename"] = PurePosixPath(row["storage_path"]).name
        return item

    def set_flags(
        self,
        owner_id: str,
        media_id: str,
        *,
        hidden: bool | None = None,
        favorite: bool | None = None,
    ) -> bool:
        if hidden is None and favorite is None:
            return False
        assignments: list[str] = []
        values: list[Any] = []
        if hidden is not None:
            assignments.append("hidden = ?")
            values.append(int(hidden))
        if favorite is not None:
            assignments.append("favorite = ?")
            values.append(int(favorite))
        values += [media_id, owner_id]
        with self.db.write() as conn:
            return (
                conn.execute(
                    f"UPDATE media SET {', '.join(assignments)} WHERE id = ? AND owner_id = ?",
                    tuple(values),
                ).rowcount
                == 1
            )

    def _safe_under(self, root: Path, path: Path) -> bool:
        """True when every component from root down to path is a real entry.

        relative_to() alone is not enough: a symlink or junction *inside* the
        root is textually below it and physically anywhere, so each component
        is checked rather than only the final target.
        """
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

    def _safe_path(self, root: Path, relative: str) -> Path | None:
        """Resolve a stored/reported relative path inside root, or None.

        A browser never supplies one of these -- media is addressed by opaque
        id -- but ComfyUI result descriptors and stored rows are still input.
        Rejected: absolute paths, "..", and (via _safe_under) anything a drive-
        relative or rooted spelling such as "/etc/x" pushes outside the root.
        """
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            return None
        path = root / candidate
        return path if self._safe_under(root, path) else None
