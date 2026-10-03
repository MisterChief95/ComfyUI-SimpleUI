"""UploadService: validation, ownership isolation, staging, and cleanup."""

from __future__ import annotations

from app.storage.repository import now_ms
from app.uploads.service import UploadError

from .support import VIDEO_BYTES, UploadTestCase, png_bytes

OWNER = "default"
OTHER = "other-profile"


class ValidationTest(UploadTestCase):
    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(UploadError) as ctx:
            self.uploads.validate("audio", "x.wav")
        self.assertEqual(ctx.exception.code, "unsupported_kind")

    def test_extension_must_match_kind(self) -> None:
        with self.assertRaises(UploadError) as ctx:
            self.uploads.validate("mask", "reference.jpg")  # masks: PNG only
        self.assertEqual(ctx.exception.code, "unsupported_type")

    def test_video_extension_accepted_for_video_kind(self) -> None:
        self.assertEqual(self.uploads.validate("video", "clip.MP4"), ".mp4")

    def test_corrupt_image_is_rejected_and_leaves_no_file(self) -> None:
        with self.assertRaises(UploadError) as ctx:
            self.store(OWNER, "image", "bad.png", b"not a real png")
        self.assertEqual(ctx.exception.code, "invalid_image")
        self.assertEqual(list((self.uploads.private_root / OWNER).glob("*")), [])


class StorageAndOwnershipTest(UploadTestCase):
    def test_round_trip_records_owner_and_size(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        self.assertEqual(row["owner_id"], OWNER)
        self.assertEqual(row["byte_size"], str(len(png_bytes())))
        self.assertEqual(self.uploads.classify(row), "image")

    def test_video_is_stored_without_pil_validation(self) -> None:
        row = self.store(OWNER, "video", "clip.mp4", VIDEO_BYTES)
        self.assertEqual(self.uploads.classify(row), "video")

    def test_another_owner_cannot_fetch_by_id(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        self.assertIsNone(self.uploads.get(OTHER, row["id"]))
        self.assertIsNotNone(self.uploads.get(OWNER, row["id"]))

    def test_another_owner_cannot_delete_by_id(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        self.assertFalse(self.uploads.delete(OTHER, row["id"]))
        self.assertTrue(self.uploads.delete(OWNER, row["id"]))
        self.assertIsNone(self.uploads.get(OWNER, row["id"]))

    def test_listing_is_owner_scoped_and_kind_filterable(self) -> None:
        self.store(OWNER, "image", "ref.png", png_bytes())
        self.store(OWNER, "video", "clip.mp4", VIDEO_BYTES)
        self.store(OTHER, "image", "ref.png", png_bytes())

        mine = self.uploads.list_uploads(OWNER, None, 50)
        self.assertEqual(len(mine.items), 2)

        images = self.uploads.list_uploads(OWNER, None, 50, media_kind="image")
        self.assertEqual(len(images.items), 1)
        self.assertEqual(self.uploads.classify(images.items[0]), "image")


class StagingTest(UploadTestCase):
    def test_image_is_staged_under_a_per_owner_subfolder(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        staged_name = self.uploads.ensure_staged(OWNER, row["id"])
        self.assertTrue(staged_name.startswith(f"simpleui/{OWNER}/"))
        staged_path = self.comfy_input / staged_name
        self.assertTrue(staged_path.is_file())

    def test_staging_is_idempotent(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        first = self.uploads.ensure_staged(OWNER, row["id"])
        second = self.uploads.ensure_staged(OWNER, row["id"])
        self.assertEqual(first, second)

    def test_restages_if_the_staged_copy_goes_missing(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        staged_name = self.uploads.ensure_staged(OWNER, row["id"])
        (self.comfy_input / staged_name).unlink()
        again = self.uploads.ensure_staged(OWNER, row["id"])
        self.assertEqual(again, staged_name)
        self.assertTrue((self.comfy_input / staged_name).is_file())

    def test_video_has_no_established_staging_route(self) -> None:
        row = self.store(OWNER, "video", "clip.mp4", VIDEO_BYTES)
        with self.assertRaises(UploadError) as ctx:
            self.uploads.ensure_staged(OWNER, row["id"])
        self.assertEqual(ctx.exception.code, "unsupported_loader_adapter")

    def test_foreign_upload_id_is_reported_as_missing_not_forbidden(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        with self.assertRaises(UploadError) as ctx:
            self.uploads.ensure_staged(OTHER, row["id"])
        self.assertEqual(ctx.exception.code, "upload_missing")

    def test_unconfigured_input_dir_is_an_actionable_diagnostic(self) -> None:
        self.settings.set_host("comfy_input_dir", "")
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        with self.assertRaises(UploadError) as ctx:
            self.uploads.ensure_staged(OWNER, row["id"])
        self.assertEqual(ctx.exception.code, "input_dir_unavailable")

    def test_staged_path_cannot_escape_the_input_root(self) -> None:
        self.assertIsNone(self.uploads._safe_join(self.comfy_input, "../outside/x.png"))
        self.assertIsNone(self.uploads._safe_join(self.comfy_input, "/etc/passwd"))


class SweepTest(UploadTestCase):
    def test_unreferenced_upload_past_the_grace_period_is_removed(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        removed = self.uploads.sweep_abandoned(grace_ms=0, now=now_ms() + 1)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.uploads.get(OWNER, row["id"]))
        self.assertFalse((self.uploads.private_root / row["storage_path"]).exists())

    def test_upload_within_the_grace_period_is_kept(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        removed = self.uploads.sweep_abandoned(
            grace_ms=24 * 60 * 60 * 1000, now=now_ms() + 1
        )
        self.assertEqual(removed, 0)
        self.assertIsNotNone(self.uploads.get(OWNER, row["id"]))

    def test_upload_referenced_by_a_retained_generation_is_kept(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        staged_name = self.uploads.ensure_staged(OWNER, row["id"])
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO generations (id, owner_id, client_request_key, request_fingerprint,"
                " graph_json, status, created_ms, updated_ms) VALUES"
                " ('g1', ?, 'k1', 'f1', ?, 'queued', ?, ?)",
                (
                    OWNER,
                    f'{{"1": {{"inputs": {{"image": "{staged_name}"}}}}}}',
                    now_ms(),
                    now_ms(),
                ),
            )
        removed = self.uploads.sweep_abandoned(grace_ms=0, now=now_ms() + 1)
        self.assertEqual(removed, 0)
        self.assertIsNotNone(self.uploads.get(OWNER, row["id"]))

    def test_upload_no_longer_referenced_once_the_snapshot_is_purged(self) -> None:
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        staged_name = self.uploads.ensure_staged(OWNER, row["id"])
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO generations (id, owner_id, client_request_key, request_fingerprint,"
                " graph_json, status, created_ms, updated_ms) VALUES"
                " ('g2', ?, 'k2', 'f2', NULL, 'succeeded', ?, ?)",
                (OWNER, now_ms(), now_ms()),
            )
        removed = self.uploads.sweep_abandoned(grace_ms=0, now=now_ms() + 1)
        self.assertEqual(removed, 1)
        self.assertIsNone(self.uploads.get(OWNER, row["id"]))


if __name__ == "__main__":
    import unittest

    unittest.main()
