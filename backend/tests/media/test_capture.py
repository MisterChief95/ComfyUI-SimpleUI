"""Path/identity validation and immutable capture of app outputs."""

from __future__ import annotations

import errno
import os
import shutil
import unittest

from app.media.service import MediaError
from app.storage import DEFAULT_PROFILE_ID, Repository

from .support import MediaTestCase

OTHER = "other-profile"


class PathValidationTest(MediaTestCase):
    def test_escapes_are_rejected(self) -> None:
        outside = self.png("secret.png", root=self.outside)
        self.assertTrue(outside.is_file())
        for spelling in (
            "../outside/secret.png",
            "..\\outside\\secret.png",
            "sub/../../outside/secret.png",
            "..",
            str(outside),                      # absolute, inside the temp tree
            "C:/Windows/win.ini",
            "/Windows/win.ini",                # drive-relative on Windows
            "//server/share/secret.png",       # UNC
        ):
            with self.subTest(spelling=spelling):
                self.assertIsNone(self.media._safe_path(self.output, spelling))

    def test_ordinary_relative_paths_still_resolve(self) -> None:
        self.png("nested/ok.png")
        resolved = self.media._safe_path(self.output, "nested/ok.png")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())

    def test_a_tampered_row_cannot_stream_a_file_outside_the_root(self) -> None:
        self.png("real.png")
        self.png("secret.png", root=self.outside)
        self.media.import_baseline()
        media_id = self.gallery()[0]["id"]

        with self.db.write() as conn:
            conn.execute(
                "UPDATE media_locations SET relative_path = ? WHERE media_id = ?",
                ("../outside/secret.png", media_id),
            )

        self.assertIsNone(self.media.locate(DEFAULT_PROFILE_ID, media_id))

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows feature")
    def test_files_behind_a_junction_are_not_indexed(self) -> None:
        self.png("real.png")
        self.png("secret.png", root=self.outside)
        self.junction(self.output / "link", self.outside)
        self.assertTrue((self.output / "link" / "secret.png").is_file())

        self.media.import_baseline()

        self.assertEqual(self.filenames(), {"real.png"})
        self.assertIsNone(self.media._safe_path(self.output, "link/secret.png"))
        # The scan must not have walked through it either.
        self.assertEqual(self.db.query("SELECT * FROM unresolved_media"), [])

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows feature")
    def test_a_junction_loop_does_not_hang_the_scan(self) -> None:
        self.png("real.png")
        (self.output / "deep").mkdir()
        self.junction(self.output / "deep" / "loop", self.output)

        self.media.import_baseline()

        self.assertEqual(self.filenames(), {"real.png"})


class CaptureTest(MediaTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.media.import_baseline()
        self.repo = Repository(self.db)
        self.generation = self.repo.create_generation(
            DEFAULT_PROFILE_ID, client_request_key="k1", request_fingerprint="f1"
        )
        self.repo.update_generation_status(
            DEFAULT_PROFILE_ID, self.generation, status="succeeded", output_state="ready"
        )
        self.produced = self.png("SimpleUI_00001_.png")

    def capture(self, **kwargs):
        return self.media.capture_output(
            DEFAULT_PROFILE_ID,
            self.generation,
            "SimpleUI_00001_.png",
            output_node="9",
            ordinal=0,
            **kwargs,
        )

    def test_a_capture_is_private_immutable_and_survives_the_original(self) -> None:
        media_id = self.capture()

        located = self.media.locate(DEFAULT_PROFILE_ID, media_id)
        self.assertIsNotNone(located)
        self.assertEqual(located.row["state"], "captured")
        self.assertEqual(located.row["generation_id"], self.generation)
        self.assertTrue(located.path.is_relative_to(self.media.captures))

        # ComfyUI reuses output filenames; the capture must not follow.
        self.produced.unlink()
        self.png("SimpleUI_00001_.png", color=(1, 2, 3))
        self.assertIsNotNone(self.media.locate(DEFAULT_PROFILE_ID, media_id))

    def test_repeated_delivery_of_the_same_result_makes_one_card(self) -> None:
        first = self.capture()
        second = self.capture()

        self.assertEqual(first, second)
        self.assertEqual(len(self.gallery()), 1)
        rows = self.db.query("SELECT state FROM capture_attempts")
        self.assertEqual([row["state"] for row in rows], ["ready"])

    def test_preview_results_create_no_card(self) -> None:
        self.assertIsNone(self.capture(preview=True))
        self.assertEqual(self.gallery(), [])
        self.assertEqual(self.db.query("SELECT * FROM capture_attempts"), [])

    def test_a_full_disk_keeps_the_execution_record_and_allows_retry(self) -> None:
        def no_space(*args, **kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        real_copy = shutil.copyfile
        shutil.copyfile = no_space
        try:
            with self.assertRaises(MediaError):
                self.capture()
        finally:
            shutil.copyfile = real_copy

        generation = self.repo.get_generation(DEFAULT_PROFILE_ID, self.generation)
        self.assertEqual(generation["status"], "succeeded")
        attempt = self.db.query_one("SELECT * FROM capture_attempts")
        self.assertEqual(attempt["state"], "failed")
        self.assertIn("No space", attempt["error"])
        self.assertEqual(self.gallery(), [])
        self.assertFalse(list(self.media.captures.rglob("*.part")))

        # Ingestion-only retry: no rerun of the workflow.
        media_id = self.capture()
        self.assertIsNotNone(self.media.locate(DEFAULT_PROFILE_ID, media_id))
        self.assertEqual(len(self.gallery()), 1)

    def test_capture_refuses_a_descriptor_outside_the_output_root(self) -> None:
        self.png("secret.png", root=self.outside)
        with self.assertRaises(MediaError):
            self.media.capture_output(
                DEFAULT_PROFILE_ID, self.generation, "../outside/secret.png"
            )
        self.assertEqual(self.gallery(), [])

    def add_other_profile(self) -> None:
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO profiles (id, name, is_default, created_ms) VALUES (?, 'Other', 0, 0)",
                (OTHER,),
            )

    def test_a_descriptor_cannot_claim_a_file_another_profile_owns(self) -> None:
        """The output folder is shared; a reported filename proves nothing."""
        self.add_other_profile()
        self.capture()
        theirs = self.repo.create_generation(
            OTHER, client_request_key="k2", request_fingerprint="f2"
        )

        with self.assertRaises(MediaError):
            self.media.capture_output(OTHER, theirs, "SimpleUI_00001_.png")

        self.assertEqual(self.media.list_media(OTHER, None, 50)["items"], [])
        self.assertEqual(len(self.gallery()), 1)

    def test_another_profile_cannot_reach_the_capture(self) -> None:
        self.add_other_profile()
        media_id = self.capture()

        self.assertIsNone(self.media.locate(OTHER, media_id))
        self.assertIsNone(self.media.thumbnail(OTHER, media_id))
        self.assertFalse(self.media.set_flags(OTHER, media_id, favorite=True))
        self.assertEqual(self.media.list_media(OTHER, None, 50)["items"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
