"""Baseline import: what lands in Default, what deliberately does not.

Run from backend/:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from app.auth.service import AuthService, Conflict
from app.media.service import MediaError, MediaService
from app.storage import DEFAULT_PROFILE_ID

from .support import MediaTestCase


class BaselineImportTest(MediaTestCase):
    def test_import_precomputes_image_thumbnails_without_ffmpeg(self) -> None:
        self.png("one.png")
        self.video("clip.mp4")

        with patch("app.media.service.shutil.which", return_value=None):
            self.media.import_baseline()

        image = next(item for item in self.gallery() if item["filename"] == "one.png")
        poster = self.media.thumbnails / f"{image['id']}.jpg"
        deadline = time.monotonic() + 3
        while not poster.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(
            poster.is_file(),
            "image thumbnail should exist before its first-view request",
        )
        poster.unlink()
        restarted = MediaService(self.db, self.settings, self.media.data_dir)
        self.addCleanup(restarted.close)
        deadline = time.monotonic() + 3
        while not poster.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(poster.is_file(), "startup should recover a missing thumbnail")
        video = next(item for item in self.gallery() if item["filename"] == "clip.mp4")
        self.assertFalse((self.media.thumbnails / f"{video['id']}.jpg").exists())
        self.assertFalse(list(self.media.thumbnails.glob("*.part")))

    def test_existing_files_are_indexed_under_default_with_unknown_metadata(
        self,
    ) -> None:
        self.png("one.png")
        self.png("nested/two.png")
        self.video("clip.mp4")
        (self.output / "notes.txt").write_text("not media", encoding="utf-8")

        counts = self.media.import_baseline()

        self.assertEqual(counts["indexed"], 3)
        items = self.gallery()
        self.assertEqual(
            {item["filename"] for item in items}, {"one.png", "two.png", "clip.mp4"}
        )
        self.assertEqual({item["media_kind"] for item in items}, {"image", "video"})
        # Old files have no execution record; the card says unknown rather than
        # inventing a workflow or prompt for them.
        self.assertEqual([item["generation_id"] for item in items], [None, None, None])
        self.assertEqual({item["state"] for item in items}, {"indexed"})

    def test_two_files_sharing_a_size_and_mtime_both_appear(self) -> None:
        """Regression: file_version is the whole ingestion key for indexed files.

        With size+mtime alone these two collide on media_ingestion_key and the
        scan dies with an IntegrityError partway through the library.
        """
        first = self.png("a.png")
        second = self.png("b.png")
        second.write_bytes(first.read_bytes())
        stamp = first.stat().st_mtime_ns
        os.utime(second, ns=(stamp, stamp))
        self.assertEqual(first.stat().st_size, second.stat().st_size)

        self.media.import_baseline()

        self.assertEqual(self.filenames(), {"a.png", "b.png"})

    def test_files_discovered_after_the_baseline_stay_out_of_default(self) -> None:
        self.png("before.png")
        self.media.import_baseline()

        self.png("after.png")
        counts = self.media.import_baseline()

        self.assertEqual(counts["unresolved"], 1)
        self.assertEqual(self.filenames(), {"before.png"})
        unresolved = self.db.query("SELECT relative_path FROM unresolved_media")
        self.assertEqual([row["relative_path"] for row in unresolved], ["after.png"])
        # Rescanning again must not promote it, nor record it twice.
        self.assertEqual(self.media.import_baseline()["unresolved"], 0)
        self.assertEqual(self.filenames(), {"before.png"})

    def test_an_interrupted_scan_keeps_its_import_boundary(self) -> None:
        self.png("first.png")
        self.png("second.png")
        real_index = self.media._index
        calls: list[str] = []

        def fail_after_one(owner_id, source_id, relative, path, **kwargs):
            calls.append(relative)
            if len(calls) > 1:
                raise OSError("scan interrupted")
            return real_index(owner_id, source_id, relative, path, **kwargs)

        self.media._index = fail_after_one
        with self.assertRaises(OSError):
            self.media.import_baseline()
        self.media._index = real_index

        # A file that appeared while the scan was down is outside the boundary.
        self.png("late.png")
        self.media.import_baseline()

        self.assertEqual(self.filenames(), {"first.png", "second.png"})
        self.assertEqual(
            [
                row["relative_path"]
                for row in self.db.query("SELECT relative_path FROM unresolved_media")
            ],
            ["late.png"],
        )

    def test_changing_the_root_requires_an_explicit_local_import(self) -> None:
        self.png("old.png")
        self.media.import_baseline()

        moved = self.root / "elsewhere"
        moved.mkdir()
        self.png("new.png", root=moved)
        self.settings.set_host("comfy_output_dir", str(moved))

        with self.assertRaises(MediaError):
            self.media.import_baseline()
        self.assertEqual(self.filenames(), {"old.png"})

        self.media.import_baseline(explicit=True)
        self.assertEqual(self.filenames(), {"old.png", "new.png"})
        # The new root is established now; an ordinary rescan no longer asks.
        self.png("later.png", root=moved)
        self.assertEqual(self.media.import_baseline()["unresolved"], 1)

    def test_a_different_folder_behind_the_same_path_also_needs_confirming(
        self,
    ) -> None:
        """Same configured path, different volume/folder identity."""
        self.png("old.png")
        self.media.import_baseline()

        os.rename(self.output, self.root / "archived")
        self.output.mkdir()
        self.png("someone-elses.png")

        with self.assertRaises(MediaError):
            self.media.import_baseline()
        self.media.import_baseline(explicit=True)
        self.assertIn("someone-elses.png", self.filenames())

    def test_a_deleted_file_becomes_unavailable_instead_of_failing_the_scan(
        self,
    ) -> None:
        self.png("keep.png")
        gone = self.png("gone.png")
        self.media.import_baseline()
        gone.unlink()

        self.media.import_baseline()

        states = {item["filename"]: item["state"] for item in self.gallery()}
        self.assertEqual(states, {"keep.png": "indexed", "gone.png": "unavailable"})
        missing = next(i["id"] for i in self.gallery() if i["filename"] == "gone.png")
        self.assertIsNone(self.media.locate(DEFAULT_PROFILE_ID, missing))

        # A file at that path again: indexed once more (as a new item, since
        # the bytes on disk are a new file).
        self.png("gone.png")
        self.media.import_baseline()
        states = {item["filename"]: item["state"] for item in self.gallery()}
        self.assertEqual(states["gone.png"], "indexed")

    def test_overwriting_an_indexed_file_invalidates_the_old_card(self) -> None:
        path = self.png("shared.png")
        self.media.import_baseline()
        original = self.gallery()[0]["id"]

        self.png("shared.png", color=(10, 200, 10))
        path.write_bytes(path.read_bytes() + b"\x00")  # same name, new content
        self.media.import_baseline()

        items = self.gallery()
        self.assertEqual(len(items), 1, "a rewritten file must not leave two cards")
        self.assertNotEqual(items[0]["id"], original)
        self.assertIsNone(self.media.locate(DEFAULT_PROFILE_ID, original))
        self.assertIsNotNone(self.media.locate(DEFAULT_PROFILE_ID, items[0]["id"]))


class BaselineGateTest(MediaTestCase):
    def test_multi_user_activation_waits_for_a_completed_import(self) -> None:
        auth = AuthService(self.db, self.settings)
        auth.baseline_gate = self.media.baseline_status
        self.png("existing.png")

        self.assertFalse(self.media.baseline_status().ready)
        with self.assertRaises(Conflict):
            auth.activate_multi_user("default-password")

        self.media.import_baseline()

        self.assertTrue(self.media.baseline_status().ready)
        auth.activate_multi_user("default-password")
        self.assertTrue(auth.multi_user_enabled())

    def test_the_gate_closes_again_when_the_root_changes(self) -> None:
        self.media.import_baseline()
        self.assertTrue(self.media.baseline_status().ready)

        moved = self.root / "elsewhere"
        moved.mkdir()
        self.settings.set_host("comfy_output_dir", str(moved))

        self.assertFalse(self.media.baseline_status().ready)

    def test_the_gate_reports_an_unconfigured_or_missing_folder(self) -> None:
        self.settings.set_host("comfy_output_dir", "")
        self.assertFalse(self.media.baseline_status().ready)

        self.settings.set_host("comfy_output_dir", str(self.root / "nowhere"))
        status = self.media.baseline_status()
        self.assertFalse(status.ready)
        self.assertTrue(status.reason)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
