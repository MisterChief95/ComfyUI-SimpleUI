"""Shared private upload fixtures for upload service tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.settings.service import SettingsStore
from app.storage import Database
from app.uploads.service import UploadService


#: A tiny valid PNG, built in-process so the test never depends on a fixture file.
def png_bytes(color: tuple[int, int, int] = (30, 120, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), color).save(buffer, "PNG")
    return buffer.getvalue()


#: Enough of an MP4 container for mimetypes; never decoded (tests/media/support.py).
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4


class StubComfy:
    def __init__(self):
        self.calls = []
        self.response = None

    async def upload_image(self, path, *, filename, subfolder):
        self.calls.append((path, filename, subfolder))
        return self.response or {
            "name": filename,
            "subfolder": subfolder,
            "type": "input",
        }


class UploadTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db = Database(self.root / "app.sqlite3")
        self.settings = SettingsStore(self.db)
        self.comfy = StubComfy()
        self.uploads = UploadService(
            self.db, self.settings, self.root / "data", self.comfy
        )
        # "default" already exists (migration 001); a second profile for
        # ownership-isolation tests needs a real row -- uploads.owner_id is a
        # foreign key.
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO profiles (id, name, is_default, created_ms) VALUES"
                " ('other-profile', 'Other', 0, 0)"
            )
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.db.close)

    def store(self, owner_id: str, kind: str, filename: str, data: bytes) -> dict:
        """Drive the same validate -> temp_path -> write -> finalize sequence
        the streaming route uses, without needing an ASGI request."""
        ext = self.uploads.validate(kind, filename)
        upload_id, temp = self.uploads.temp_path(owner_id, ext)
        temp.write_bytes(data)
        return self.uploads.finalize(owner_id, upload_id, ext, kind, temp, len(data))
