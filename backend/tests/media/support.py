"""Shared fixtures for the MEDIA-001 tests.

A temporary ComfyUI output root plus a migrated database, so the scanner works
on real files with real identities: nothing here stubs the filesystem.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.media import MediaService
from app.settings.service import SettingsStore
from app.storage import Database

#: Enough of an MP4 container for mimetypes and range requests; never decoded.
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4


class MediaTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.db = Database(self.root / "app.sqlite3")
        self.settings = SettingsStore(self.db)
        self.settings.set_host("comfy_output_dir", str(self.output))
        self.media = MediaService(self.db, self.settings, self.root / "data")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.db.close)
        self.addCleanup(self.media.close)

    # --- files ------------------------------------------------------------

    def png(
        self,
        relative: str,
        color: tuple[int, int, int] = (200, 30, 30),
        root: Path | None = None,
    ) -> Path:
        path = (root or self.output) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), color).save(path)
        return path

    def video(self, relative: str, root: Path | None = None) -> Path:
        path = (root or self.output) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(VIDEO_BYTES)
        return path

    def junction(self, link: Path, target: Path) -> None:
        """Create a directory junction, or skip the test where that is refused."""
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:  # pragma: no cover - depends on the host
            self.skipTest(f"mklink /J unavailable: {result.stderr.strip()}")
        # Unlink the reparse point before rmtree walks it: deleting the junction
        # must never delete what it points at.
        self.addCleanup(lambda: link.exists() and link.rmdir())

    # --- assertions -------------------------------------------------------

    def gallery(self, owner_id: str = "default") -> list[dict]:
        return self.media.list_media(owner_id, None, 200)["items"]

    def filenames(self, owner_id: str = "default") -> set[str]:
        return {item["filename"] for item in self.gallery(owner_id)}
