"""Batch ZIP privacy, limits, partial failures, and bounded streaming."""
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.auth.security import CSRF_COOKIE
from app.media.service import LocatedMedia, ZIP_CHUNK_BYTES, ZIP_MAX_ITEMS
from .test_api import MediaApiTestCase, PASSWORD


class ZipTest(MediaApiTestCase):
    def seed(self):
        self.video("clip.mp4")
        self.media.import_baseline()
        return self.own_media()["clip.mp4"]["id"]

    def archive(self, ids, client=None):
        response = self.post(client or self.local_client(), "/api/media/download-zip", json={"ids": ids})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        return response, zipfile.ZipFile(io.BytesIO(response.content))

    def test_original_bytes_duplicate_names_and_manifest_collision(self):
        self.video("clip.mp4")
        (self.output / "nested").mkdir()
        (self.output / "nested" / "clip.mp4").write_bytes(b"second")
        self.media.import_baseline()
        first = self.own_media()["clip.mp4"]["id"]
        ids = [item["id"] for item in self.media.list_media("default", None, 200)["items"]]
        response, archive = self.archive([first, *ids])
        with archive:
            self.assertEqual(set(archive.namelist()), {"clip.mp4", "clip (2).mp4", "manifest.json"})
            self.assertEqual(archive.read("clip.mp4"), (self.output / "clip.mp4").read_bytes())
            self.assertEqual(archive.read("clip (2).mp4"), b"second")
            self.assertEqual(len(json.loads(archive.read("manifest.json"))["included"]), 2)
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertNotIn(str(self.output).encode(), response.content)

    def test_manifest_name_is_reserved(self):
        media_id = self.seed()
        located = self.media.locate("default", media_id)
        row = {**located.row, "storage_path": "manifest.json"}
        with patch.object(self.media, "locate", return_value=LocatedMedia(row, located.path)):
            plan = self.media.zip_plan("default", [media_id])
        self.assertEqual(plan["entries"][0]["filename"], "manifest (2).json")

    def test_missing_and_foreign_ids_are_indistinguishable(self):
        media_id = self.seed()
        self.auth.activate_multi_user(PASSWORD)
        self.auth.create_profile("Bee", PASSWORD)
        client = self.local_client()
        self.login(client, "Bee", PASSWORD)
        response, archive = self.archive([media_id, "unknown"], client)
        with archive:
            self.assertEqual(archive.namelist(), ["manifest.json"])
            report = json.loads(archive.read("manifest.json"))
            self.assertEqual(report["included"], [])
            self.assertEqual(report["skipped"], [{"id": media_id, "reason": "unavailable"}, {"id": "unknown", "reason": "unavailable"}])
        self.assertEqual(response.headers["x-media-skipped"], "2")
        self.assertNotIn(b"clip.mp4", response.content)
        self.assertEqual(self.local_client().post("/api/media/download-zip", json={"ids": [media_id]}).status_code, 401)

    def test_missing_file_is_skipped_and_preview_matches(self):
        self.video("clip.mp4")
        self.png("keep.png")
        self.media.import_baseline()
        media_id = self.own_media()["clip.mp4"]["id"]
        keep = self.own_media()["keep.png"]["id"]
        (self.output / "clip.mp4").unlink()
        client = self.local_client()
        preview = self.post(client, "/api/media/download-zip/preview", json={"ids": [media_id, keep]}).json()
        self.assertEqual(preview["skipped"], [{"id": media_id, "reason": "unavailable"}])
        _, archive = self.archive([media_id, keep])
        with archive:
            self.assertEqual(set(archive.namelist()), {"keep.png", "manifest.json"})
            self.assertEqual(json.loads(archive.read("manifest.json"))["skipped"], preview["skipped"])

    def test_count_size_and_body_caps(self):
        media_id = self.seed()
        client = self.local_client()
        for endpoint in ("/api/media/download-zip", "/api/media/download-zip/preview"):
            for ids in ([], ["a"] * (ZIP_MAX_ITEMS + 1), ["x" * 101]):
                self.assertEqual(self.post(client, endpoint, json={"ids": ids}).status_code, 422)
            with patch("app.media.service.ZIP_MAX_BYTES", 1):
                self.assertEqual(self.post(client, endpoint, json={"ids": [media_id]}).status_code, 413)
            with patch("app.media.service.ZIP_MAX_BYTES", (self.output / "clip.mp4").stat().st_size):
                self.assertEqual(self.post(client, endpoint, json={"ids": [media_id]}).status_code, 200)
        self.assertEqual(client.post("/api/media/download-zip", content=b"x" * 65537,
                                     headers={"content-type": "application/json"}).status_code, 413)

    def test_native_form_csrf_and_origin(self):
        media_id = self.seed()
        self.auth.activate_multi_user(PASSWORD)
        client = self.local_client()
        self.login(client, "Default", PASSWORD)
        data = {"selection": json.dumps({"ids": [media_id]}), "csrf_token": client.cookies.get(CSRF_COOKIE)}
        response = client.post("/api/media/download-zip", data=data)
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn("clip.mp4", archive.namelist())
        self.assertEqual(client.post("/api/media/download-zip", data={**data, "csrf_token": "bad"}).status_code, 403)
        self.assertEqual(client.post("/api/media/download-zip", data=data, headers={"origin": "https://foreign.example"}).status_code, 403)
        self.assertEqual(client.post("/api/media/download-zip", json={"ids": [media_id]}).status_code, 403)

    def test_bounded_lazy_reads_no_temp_files_and_disconnect_cleanup(self):
        path = self.output / "big.mp4"
        path.write_bytes(b"x" * (ZIP_CHUNK_BYTES * 4 + 13))
        self.media.import_baseline()
        self.media.close()  # Drain thumbnail work before comparing filesystem snapshots.
        media_id = self.own_media()["big.mp4"]["id"]
        plan = self.media.zip_plan("default", [media_id])
        before = set(self.output.parent.rglob("*"))
        reads, handles = [], []
        original_open = Path.open

        class Reader:
            def __init__(self, handle):
                self.handle = handle
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def read(self, size=-1):
                reads.append(size)
                self_test.assertTrue(0 < size <= ZIP_CHUNK_BYTES)
                return self.handle.read(size)

        self_test = self
        def bounded_open(target, *args, **kwargs):
            handle = original_open(target, *args, **kwargs)
            handles.append(handle)
            return Reader(handle)

        with patch.object(Path, "open", bounded_open), patch.object(Path, "read_bytes", side_effect=AssertionError("whole read")):
            stream = self.media.stream_zip("default", plan)
            next(stream)  # Only the local header, no source bytes yet.
            self.assertEqual(reads, [])
            next(stream)
            self.assertEqual(reads, [ZIP_CHUNK_BYTES])
            stream.close()
            self.assertTrue(all(handle.closed for handle in handles))
            chunks = list(self.media.stream_zip("default", plan))
        self.assertLessEqual(max(map(len, chunks)), ZIP_CHUNK_BYTES)
        with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
            self.assertEqual(len(archive.read("big.mp4")), path.stat().st_size)
            self.assertIsNone(archive.testzip())
        self.assertEqual(before, set(self.output.parent.rglob("*")))

    def test_disappearing_file_after_plan_is_reported(self):
        media_id = self.seed()
        plan = self.media.zip_plan("default", [media_id])
        (self.output / "clip.mp4").unlink()
        with zipfile.ZipFile(io.BytesIO(b"".join(self.media.stream_zip("default", plan)))) as archive:
            self.assertEqual(archive.namelist(), ["manifest.json"])
            self.assertEqual(len(json.loads(archive.read("manifest.json"))["skipped"]), 1)

    def test_read_error_leaves_valid_zip_and_reports_incomplete_member(self):
        path = self.output / "broken.mp4"
        path.write_bytes(b"x" * (ZIP_CHUNK_BYTES * 2))
        self.video("keep.mp4")
        self.media.import_baseline()
        broken = self.own_media()["broken.mp4"]["id"]
        keep = self.own_media()["keep.mp4"]["id"]
        plan = self.media.zip_plan("default", [broken, keep])
        original_open = Path.open
        handles = []

        class FailingReader:
            def __init__(self, handle):
                self.handle = handle
                self.reads = 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.handle.close()
            def fileno(self):
                return self.handle.fileno()
            def read(self, size):
                self.reads += 1
                if self.reads > 1:
                    raise OSError("secret filesystem path must not escape")
                return self.handle.read(size)

        def failing_open(target, *args, **kwargs):
            handle = original_open(target, *args, **kwargs)
            handles.append(handle)
            return FailingReader(handle) if target == path else handle

        with patch.object(Path, "open", failing_open):
            content = b"".join(self.media.stream_zip("default", plan))
        self.assertTrue(all(handle.closed for handle in handles))
        self.assertNotIn(b"secret filesystem", content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIsNone(archive.testzip())
            report = json.loads(archive.read("manifest.json"))
            self.assertEqual([entry["id"] for entry in report["included"]], [keep])
            self.assertEqual(report["skipped"], [{"id": broken, "reason": "unavailable", "filename": "broken.mp4", "incomplete": True}])
            self.assertEqual(len(archive.read("broken.mp4")), ZIP_CHUNK_BYTES)
