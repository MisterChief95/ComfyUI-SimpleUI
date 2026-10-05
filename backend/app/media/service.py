"""Filesystem media work kept behind owned database rows and validated roots."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import BoundedSemaphore
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..auth.service import BaselineStatus
from ..generations.store import TERMINAL
from ..settings.service import SettingsStore
from ..storage.db import Database
from ..storage.repository import (
    DEFAULT_PROFILE_ID,
    Repository,
    new_id,
    now_ms,
    search_values,
)
from .metadata import read_metadata

IMAGE_EXTENSIONS = frozenset(
    {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
VIDEO_EXTENSIONS = frozenset({".avi", ".mkv", ".mov", ".mp4", ".webm"})
AUDIO_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"})
ZIP_MAX_ITEMS = 200
ZIP_MAX_BYTES = 2 * 1024**3
ZIP_CHUNK_BYTES = 64 * 1024


class _ZipWriter:
    """Unseekable zipfile sink; drained after each bounded input chunk."""

    def __init__(self):
        self.pending = bytearray()
        self.position = 0

    def write(self, data):
        self.pending.extend(data)
        self.position += len(data)
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        pass

    def drain(self):
        data = bytes(self.pending)
        self.pending.clear()
        return data


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
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return "other"


def _type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _video_poster(source: Path, target: Path) -> bool:
    """Write one JPEG frame of ``source`` to ``target``; False if FFmpeg is absent or fails."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    # A frame at 1s skips fade-ins; clips shorter than that yield nothing, so retry at 0.
    for seek in ("1", "0"):
        command = [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            seek,
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(512,iw)':-2",
            "-f",
            "image2",
            "-y",
            str(target),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if target.is_file() and target.stat().st_size:
            return True
    target.unlink(missing_ok=True)
    return False


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
        # Keep thumbnail work off indexing/capture paths while bounding queued
        # jobs. Producers apply backpressure when the small queue is full.
        self._thumbnail_slots = BoundedSemaphore(8)
        self._thumbnail_jobs = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="media-thumbnail"
        )
        self._ensure_capture_source()
        self._recover_provenance()
        self._recover_thumbnails()

    def _recover_provenance(self) -> None:
        """Backfill only external imports. Captures never rehydrate history."""
        cursor = ""
        while True:
            rows = self.db.query(
                "SELECT m.id, m.owner_id FROM media m JOIN media_locations l ON l.media_id = m.id"
                " LEFT JOIN media_provenance p ON p.media_id = m.id"
                " WHERE m.id > ? AND m.generation_id IS NULL AND l.source_id != 'captures'"
                " AND m.hidden = 0 AND p.media_id IS NULL ORDER BY m.id LIMIT 100",
                (cursor,),
            )
            if not rows:
                return
            for row in rows:
                self.provenance(row["owner_id"], row["id"])
            cursor = rows[-1]["id"]

    def provenance(self, owner_id: str, media_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT m.*, l.source_id FROM media m JOIN media_locations l ON l.media_id = m.id"
            " WHERE m.id = ? AND m.owner_id = ? AND m.hidden = 0",
            (media_id, owner_id),
        )
        if row is None:
            return None
        if row["generation_id"] is not None or row["source_id"] == "captures":
            return {
                "source": "generation",
                "raw": {},
                "values": {},
                "diagnostics": ["Use retained generation history for this file."],
            }
        saved = self.db.query_one(
            "SELECT provenance_json FROM media_provenance WHERE media_id = ? AND file_version = ?",
            (media_id, row["file_version"]),
        )
        if saved:
            return json.loads(saved["provenance_json"])
        located = self.locate(owner_id, media_id)
        if located is None:
            return {
                "source": "unknown",
                "raw": {},
                "values": {},
                "diagnostics": [
                    "The imported file is unavailable. Rescan its output folder."
                ],
            }
        result = read_metadata(located.path)
        # Filesystem work is outside the transaction; do not publish stale metadata.
        current = self.locate(owner_id, media_id)
        if (
            current is None
            or current.row["file_version"] != located.row["file_version"]
        ):
            return None
        with self.db.write() as conn:
            if not conn.execute(
                "SELECT 1 FROM media WHERE id = ? AND owner_id = ? AND file_version = ?",
                (media_id, owner_id, row["file_version"]),
            ).fetchone():
                return None
            conn.execute(
                "INSERT INTO media_provenance VALUES (?, ?, ?) ON CONFLICT(media_id)"
                " DO UPDATE SET file_version = excluded.file_version, provenance_json = excluded.provenance_json",
                (media_id, row["file_version"], json.dumps(result)),
            )
            conn.execute("DELETE FROM media_search WHERE media_id = ?", (media_id,))
            conn.executemany(
                "INSERT INTO media_search VALUES (?, ?, ?, ?, ?)",
                [
                    (media_id, owner_id, *record)
                    for record in search_values(result["values"])
                ],
            )
        return result

    def _recover_thumbnails(self) -> None:
        """Requeue indexed media without a thumbnail after a process restart."""
        rows = self.db.query(
            "SELECT id, owner_id FROM media WHERE media_kind IN ('image', 'video')"
        )
        for row in rows:
            if not (self.thumbnails / f"{row['id']}.jpg").is_file():
                self._schedule_thumbnail(row["owner_id"], row["id"])

    def _schedule_thumbnail(self, owner_id: str, media_id: str) -> None:
        target = self.thumbnails / f"{media_id}.jpg"
        if target.is_file():
            return
        located = self.locate(owner_id, media_id)
        if located is None or located.row["media_kind"] not in ("image", "video"):
            return
        self._thumbnail_slots.acquire()

        def generate() -> None:
            temporary = target.with_suffix(".part")
            try:
                if located.row["media_kind"] == "video":
                    if not _video_poster(located.path, temporary):
                        return
                else:
                    with Image.open(located.path) as image:
                        image.thumbnail((512, 512))
                        image.convert("RGB").save(temporary, "JPEG", quality=82)
                # Do not publish a stale thumbnail if the indexed file changed
                # while this job was queued.
                current = self.locate(owner_id, media_id)
                if (
                    current is not None
                    and current.row["file_version"] == located.row["file_version"]
                ):
                    os.replace(temporary, target)
            except (
                OSError,
                UnidentifiedImageError,
                Image.DecompressionBombError,
                ValueError,
            ):
                temporary.unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)
                self._thumbnail_slots.release()

        self._thumbnail_jobs.submit(generate)

    def _ensure_capture_source(self) -> None:
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO media_sources (id, root_path, root_identity, baseline_complete, updated_ms)"
                " VALUES ('captures', ?, ?, 1, ?) ON CONFLICT(id) DO NOTHING",
                (str(self.captures), _identity(self.captures), now_ms()),
            )

    def close(self) -> None:
        """Finish queued thumbnail work before storage or files are closed."""
        self._thumbnail_jobs.shutdown(wait=True)

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
                self.provenance(owner_id, row["id"])
                self._schedule_thumbnail(owner_id, row["id"])
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
        self.provenance(owner_id, media_id)
        self._schedule_thumbnail(owner_id, media_id)
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
        # Hidden (deleted) rows resolve to nothing, so file, download, thumbnail, ZIP and queued thumbnail jobs all refuse them.
        row = self.db.query_one(
            "SELECT m.*, l.source_id, l.relative_path, s.root_path FROM media m JOIN media_locations l ON l.media_id = m.id JOIN media_sources s ON s.id = l.source_id WHERE m.id = ? AND m.owner_id = ? AND m.hidden = 0",
            (media_id, owner_id),
        )
        if row is None:
            return None
        root = Path(row["root_path"])
        path = self._safe_path(root, row["relative_path"])
        if path is None or not path.is_file() or _version(path) != row["file_version"]:
            return None
        return LocatedMedia(dict(row), path)

    def zip_plan(self, owner_id: str, ids: list[str]) -> dict:
        if not 1 <= len(ids) <= ZIP_MAX_ITEMS:
            raise MediaError(f"Select between 1 and {ZIP_MAX_ITEMS} items.")
        entries, skipped, total = [], [], 0
        used = {"manifest.json"}
        for media_id in dict.fromkeys(ids):
            try:
                located = self.locate(owner_id, media_id)
                size = located.path.stat().st_size if located else None
            except OSError:
                located, size = None, None
            if located is None:
                skipped.append({"id": media_id, "reason": "unavailable"})
                continue
            total += size
            if total > ZIP_MAX_BYTES:
                raise MediaError("Selected media exceeds the 2 GiB ZIP limit.")
            original = PurePosixPath(
                located.row["storage_path"].replace("\\", "/")
            ).name
            # ZIP members are flat, safe filenames, including on Windows extraction.
            original = (
                "".join(
                    "_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in original
                ).rstrip(" .")
                or "media"
            )
            name, number = original, 2
            while name.casefold() in used:
                p = PurePosixPath(original)
                name = f"{p.stem} ({number}){p.suffix}"
                number += 1
            used.add(name.casefold())
            entries.append({"id": media_id, "filename": name, "size": size})
        return {"entries": entries, "skipped": skipped, "total_bytes": total}

    def stream_zip(self, owner_id: str, plan: dict):
        """No temporary archive, producer thread, or whole-media buffer."""
        sink = _ZipWriter()
        skipped = list(plan["skipped"])
        included = []
        with zipfile.ZipFile(
            sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for entry in plan["entries"]:
                written = 0
                started = False
                try:
                    located = self.locate(owner_id, entry["id"])
                    if located is None:
                        raise OSError("Unavailable")
                    with located.path.open("rb") as source:
                        stat = os.fstat(source.fileno())
                        version = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
                        if (
                            version != located.row["file_version"]
                            or stat.st_size != entry["size"]
                        ):
                            raise OSError("Changed")
                        with archive.open(
                            entry["filename"], "w", force_zip64=True
                        ) as member:
                            started = True
                            yield sink.drain()
                            while written < entry["size"]:
                                chunk = source.read(
                                    min(ZIP_CHUNK_BYTES, entry["size"] - written)
                                )
                                if not chunk:
                                    raise OSError("Short read")
                                member.write(chunk)
                                written += len(chunk)
                                yield sink.drain()
                    included.append(entry)
                except OSError:
                    # A read error after the header leaves a valid but incomplete member.
                    skipped.append(
                        {
                            "id": entry["id"],
                            "reason": "unavailable",
                            "filename": entry["filename"] if started else None,
                            "incomplete": started,
                        }
                    )
                yield sink.drain()
            archive.writestr(
                "manifest.json", json.dumps({"included": included, "skipped": skipped})
            )
            yield sink.drain()
        yield sink.drain()

    def thumbnail(self, owner_id: str, media_id: str) -> Path | None:
        """None means "no thumbnail": the caller 404s and the UI shows a placeholder.

        Videos get a poster frame only when FFmpeg is on PATH (ARCHITECTURE.md
        keeps it optional); otherwise they take the placeholder path.
        """
        located = self.locate(owner_id, media_id)
        if located is None or located.row["media_kind"] not in ("image", "video"):
            return None
        target = self.thumbnails / f"{media_id}.jpg"
        if target.is_file():
            return target
        temporary = target.with_suffix(".part")
        try:
            if located.row["media_kind"] == "video":
                if not _video_poster(located.path, temporary):
                    return None
            else:
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

    def media_tree(self, owner_id: str, path: str = "") -> dict[str, Any]:
        return Repository(self.db).media_tree(owner_id, path)

    def list_collections(self, owner_id: str) -> dict[str, Any]:
        return {"items": Repository(self.db).list_collections(owner_id)}

    def save_collection(
        self, owner_id: str, name: str, collection_id: str | None = None
    ):
        return Repository(self.db).save_collection(owner_id, name, collection_id)

    def delete_collection(self, owner_id: str, collection_id: str) -> bool:
        return Repository(self.db).delete_collection(owner_id, collection_id)

    def collection_members(
        self, owner_id: str, collection_id: str, ids: list[str], *, remove: bool = False
    ):
        return Repository(self.db).collection_members(
            owner_id, collection_id, ids, remove=remove
        )

    def list_media(
        self,
        owner_id: str,
        cursor: str | None,
        limit: int,
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
    ) -> dict[str, Any]:
        page = Repository(self.db).list_media(
            owner_id,
            cursor,
            limit,
            sort=sort,
            path=path,
            collection_id=collection_id,
            media_kind=media_kind,
            favorite=favorite,
            workflow_id=workflow_id,
            generation_id=generation_id,
            created_after=created_after,
            created_before=created_before,
            prompt=prompt,
            search_field=search_field,
        )
        return {
            "items": [self.public_item(row) for row in page.items],
            "next_cursor": page.next_cursor,
        }

    def filter_suggestions(
        self, owner_id: str, query: str, limit: int = 10, search_field: str = "any"
    ) -> dict[str, Any]:
        return {
            "items": Repository(self.db).media_filter_suggestions(
                owner_id, query, limit, search_field
            )
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
        item["collections"] = row.get("collections", [])
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

    def delete_media(self, owner_id: str, ids: list[str]) -> int:
        """Delete owned items; ids that are not the caller's are ignored.

        Captured copies are ours, so row, file and thumbnail go. Files indexed
        from the ComfyUI output folder are the user's originals: the row is
        hidden (a delete would come back on the next import) and the file is
        never touched. Rows go first so a failed unlink leaves an orphan file,
        not a card pointing at nothing.
        """
        ids = list(dict.fromkeys(ids))
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        rows = self.db.query(
            "SELECT m.id, l.source_id, l.relative_path FROM media m"
            " LEFT JOIN media_locations l ON l.media_id = m.id"
            f" WHERE m.owner_id = ? AND m.id IN ({marks})",
            (owner_id, *ids),
        )
        captured = [row for row in rows if row["source_id"] == "captures"]
        indexed = [row["id"] for row in rows if row["source_id"] != "captures"]
        with self.db.write() as conn:
            conn.executemany(
                "DELETE FROM media WHERE id = ?", [(row["id"],) for row in captured]
            )
            conn.executemany(
                "UPDATE media SET hidden = 1 WHERE id = ?", [(i,) for i in indexed]
            )
        for row in captured:
            path = self._safe_path(self.captures, row["relative_path"])
            if path is not None:
                path.unlink(missing_ok=True)
        for row in rows:
            (self.thumbnails / f"{row['id']}.jpg").unlink(missing_ok=True)
        return len(rows)

    @staticmethod
    def _content_hash(located: LocatedMedia) -> str | None:
        """Stream a bounded buffer; reject files changed before/during the read."""
        try:
            with located.path.open("rb") as source:
                before = os.fstat(source.fileno())
                version = f"{before.st_dev}:{before.st_ino}:{before.st_size}:{before.st_mtime_ns}"
                if version != located.row["file_version"]:
                    return None
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                after = os.fstat(source.fileno())
                version_after = (
                    f"{after.st_dev}:{after.st_ino}:{after.st_size}:{after.st_mtime_ns}"
                )
                if version != version_after or _version(located.path) != version:
                    return None
                return digest.hexdigest()
        except OSError:
            return None

    def duplicate_groups(
        self, owner_id: str, media_id: str | None = None
    ) -> dict | None:
        """On-demand exact groups. Stat/size first, hash only same-size candidates."""
        target = self.locate(owner_id, media_id) if media_id is not None else None
        if media_id is not None and target is None:
            return None
        try:
            target_size = target.path.stat().st_size if target else None
        except OSError:
            return None
        repo = Repository(self.db)
        sizes: dict[int, list[tuple[dict, LocatedMedia]]] = {}
        unavailable = 0
        for row in repo.duplicate_candidates(owner_id):
            try:
                located = self.locate(owner_id, row["id"])
                size = located.path.stat().st_size if located else None
            except OSError:
                located, size = None, None
            if located is None:
                unavailable += 1
                continue
            if target_size is None or size == target_size:
                sizes.setdefault(size, []).append((row, located))
        groups = []
        for size, candidates in sizes.items():
            if len(candidates) < 2:
                continue
            hashes: dict[str, list[dict]] = {}
            for row, located in candidates:
                digest = row["cached_sha256"] if row["hash_size"] == size else None
                if digest is None:
                    digest = self._content_hash(located)
                    if digest:
                        repo.cache_media_hash(
                            owner_id, row["id"], row["file_version"], size, digest
                        )
                if digest is None:
                    unavailable += 1
                    continue
                hashes.setdefault(digest, []).append(row)
            for matches in hashes.values():
                if len(matches) < 2 or (
                    media_id is not None
                    and not any(row["id"] == media_id for row in matches)
                ):
                    continue
                groups.append(
                    {
                        "byte_size": str(size),
                        "count": len(matches),
                        "items": [self.public_item(row) for row in matches[:200]],
                    }
                )
        return {"groups": groups, "unavailable": unavailable}

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
