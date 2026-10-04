"""Exact duplicate checks are owner-scoped, cached and never remove files."""

import hashlib
import shutil
from unittest.mock import patch

from app.media import MediaService
from app.storage import DEFAULT_PROFILE_ID, Repository

from .test_api import MediaApiTestCase


class DuplicateTest(MediaApiTestCase):
    def test_size_first_exact_content_and_cache(self):
        self.video("a.mp4").write_bytes(b"a" * (2 * 1024 * 1024 + 3))
        shutil.copyfile(self.output / "a.mp4", self.output / "b.mp4")
        self.video("different.mp4").write_bytes(b"b" * (2 * 1024 * 1024 + 3))
        self.video("singleton.mp4").write_bytes(b"unique size")
        self.media.import_baseline()
        rows = self.own_media()
        client = self.local_client()
        with patch.object(
            MediaService, "_content_hash", wraps=MediaService._content_hash
        ) as hashing:
            response = client.get(
                "/api/media/duplicates", params={"media_id": rows["a.mp4"]["id"]}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(hashing.call_count, 3)
        group = response.json()["groups"][0]
        self.assertEqual(group["count"], 2)
        self.assertEqual(
            {entry["filename"] for entry in group["items"]}, {"a.mp4", "b.mp4"}
        )
        cached = self.db.query_one(
            "SELECT sha256 FROM media_hashes WHERE media_id = ?", (rows["a.mp4"]["id"],)
        )
        self.assertEqual(
            cached["sha256"],
            hashlib.sha256((self.output / "a.mp4").read_bytes()).hexdigest(),
        )
        with patch.object(
            MediaService, "_content_hash", side_effect=AssertionError("cached")
        ):
            self.assertEqual(
                client.get(
                    "/api/media/duplicates", params={"media_id": rows["a.mp4"]["id"]}
                ).json(),
                response.json(),
            )
        self.assertEqual(len(list(self.output.iterdir())), 4)

    def test_owner_isolation_and_explicit_delete_preserves_indexed_original(self):
        self.png("a.png")
        shutil.copyfile(self.output / "a.png", self.output / "b.png")
        self.media.import_baseline()
        rows = self.own_media()
        other = Repository(self.db).create_profile("Other")
        # Another profile's private capture has identical bytes, but must not be in the group.
        shutil.copyfile(self.output / "a.png", self.output / "other.png")
        foreign = self.media.capture_output(other, None, "other.png")
        group = self.media.duplicate_groups(DEFAULT_PROFILE_ID, rows["a.png"]["id"])
        self.assertEqual(group["groups"][0]["count"], 2)
        self.assertIsNone(self.media.duplicate_groups(DEFAULT_PROFILE_ID, foreign))
        self.assertIsNone(self.media.duplicate_groups(other, rows["a.png"]["id"]))
        self.assertEqual(self.media.duplicate_groups(other, foreign)["groups"], [])
        client = self.local_client()
        self.assertEqual(
            client.get(
                "/api/media/duplicates", params={"media_id": foreign}
            ).status_code,
            404,
        )
        self.assertEqual(
            client.get(
                "/api/media/duplicates", params={"media_id": "missing"}
            ).status_code,
            404,
        )
        self.assertEqual(
            client.post(
                "/api/media/delete", json={"ids": [rows["b.png"]["id"]]}
            ).status_code,
            200,
        )
        self.assertTrue((self.output / "b.png").is_file())
        self.assertEqual(
            self.media.duplicate_groups(DEFAULT_PROFILE_ID, rows["a.png"]["id"])[
                "groups"
            ],
            [],
        )

    def test_changed_missing_and_reindexed_files_do_not_reuse_old_hashes(self):
        self.video("a.mp4").write_bytes(b"aaaa")
        self.video("b.mp4").write_bytes(b"aaaa")
        self.media.import_baseline()
        rows = self.own_media()
        self.assertEqual(
            self.media.duplicate_groups(DEFAULT_PROFILE_ID, rows["a.mp4"]["id"])[
                "groups"
            ][0]["count"],
            2,
        )
        old = self.media.locate(DEFAULT_PROFILE_ID, rows["b.mp4"]["id"])
        (self.output / "b.mp4").write_bytes(b"bbbb")
        self.assertIsNone(MediaService._content_hash(old))
        self.assertEqual(
            self.media.duplicate_groups(DEFAULT_PROFILE_ID, rows["a.mp4"]["id"])[
                "groups"
            ],
            [],
        )
        self.media.import_baseline()
        with patch.object(
            MediaService, "_content_hash", wraps=MediaService._content_hash
        ) as hashing:
            self.assertEqual(
                self.media.duplicate_groups(DEFAULT_PROFILE_ID, rows["a.mp4"]["id"])[
                    "groups"
                ],
                [],
            )
        self.assertEqual(hashing.call_count, 1)
        (self.output / "a.mp4").unlink()
        self.assertIsNone(
            self.media.duplicate_groups(DEFAULT_PROFILE_ID, rows["a.mp4"]["id"])
        )
