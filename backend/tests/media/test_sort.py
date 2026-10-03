"""Gallery sort contracts and keyset stability under concurrent changes."""

import base64
import hashlib
import json

from app.storage import DEFAULT_PROFILE_ID, Repository

from .test_api import MediaApiTestCase


class GallerySortTest(MediaApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo = Repository(self.db)

    def make_media(self, name, created_ms, owner=DEFAULT_PROFILE_ID):
        media_id = self.repo.record_media(
            owner,
            storage_path=name,
            file_version=name,
            media_kind="image",
            media_type="image/png",
        )
        with self.db.write() as conn:
            conn.execute(
                "UPDATE media SET created_ms = ? WHERE id = ?", (created_ms, media_id)
            )
        return media_id

    def page(self, sort, cursor=None, limit=2):
        response = self.local_client().get(
            "/api/media",
            params={
                "sort": sort,
                "limit": limit,
                **({"cursor": cursor} if cursor else {}),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_all_sorts_page_stably_with_ties_insertions_and_removals(self):
        for sort in ("newest", "oldest", "random"):
            with self.subTest(sort=sort):
                with self.db.write() as conn:
                    conn.execute("DELETE FROM media")
                original = [
                    self.make_media(f"{sort}-{i}.png", 1000 + i // 2) for i in range(9)
                ]
                first = self.page(sort)
                saved = json.loads(
                    base64.urlsafe_b64decode(
                        first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4)
                    )
                )
                self.assertEqual(saved["sort"], sort)
                seed = saved["seed"]
                if sort == "random":
                    key = lambda row_id, seed=seed: (
                        hashlib.sha256(f"{seed}:{row_id}".encode()).hexdigest(),
                        row_id,
                    )
                else:
                    times = {row_id: 1000 + i // 2 for i, row_id in enumerate(original)}
                    key = lambda row_id, times=times: (times[row_id], row_id)
                expected = sorted(original, key=key, reverse=sort == "newest")
                seen = [item["id"] for item in first["items"]]
                self.assertEqual(seen, expected[:2])
                # Remove the cursor anchor and one unread row. Offset paging skips surviving rows here.
                removed = expected[4]
                with self.db.write() as conn:
                    conn.execute(
                        "DELETE FROM media WHERE id IN (?, ?)", (seen[-1], removed)
                    )
                self.make_media(f"{sort}-early.png", 500)
                self.make_media(f"{sort}-late.png", 2000)
                foreign = self.repo.create_profile(f"Foreign-{sort}")
                foreign_media = self.make_media(f"{sort}-foreign.png", 1001, foreign)
                cursor = first["next_cursor"]
                # Replaying a cursor produces the same page and random seed.
                self.assertEqual(self.page(sort, cursor), self.page(sort, cursor))
                while cursor:
                    page = self.page(sort, cursor)
                    seen.extend(item["id"] for item in page["items"])
                    cursor = page["next_cursor"]
                    if cursor:
                        saved = json.loads(
                            base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                        )
                        self.assertEqual(saved["seed"], seed)
                self.assertEqual(len(seen), len(set(seen)))
                self.assertNotIn(foreign_media, seen)
                self.assertEqual(
                    [row_id for row_id in seen if row_id in original],
                    [row_id for row_id in expected if row_id != removed],
                )

    def test_sort_validation_and_cursor_sort_mismatch(self):
        for i in range(3):
            self.make_media(f"{i}.png", 1000 + i)
        client = self.local_client()
        self.assertEqual(client.get("/api/media?sort=invalid").status_code, 422)
        self.assertEqual(client.get("/api/media").json(), self.page("newest", limit=50))
        for sort in ("newest", "oldest", "random"):
            cursor = self.page(sort)["next_cursor"]
            for other in ("newest", "oldest", "random"):
                if other != sort:
                    self.assertEqual(
                        client.get(
                            "/api/media", params={"sort": other, "cursor": cursor}
                        ).status_code,
                        400,
                    )
        for saved in (
            {},
            [],
            {"sort": "random", "seed": None, "key": 1, "id": "x"},
            {"sort": "newest", "seed": None, "key": True, "id": "x"},
        ):
            cursor = base64.urlsafe_b64encode(json.dumps(saved).encode()).decode()
            self.assertEqual(
                client.get(
                    "/api/media",
                    params={
                        "cursor": cursor,
                        "sort": "random"
                        if isinstance(saved, dict) and saved.get("sort") == "random"
                        else "newest",
                    },
                ).status_code,
                400,
            )

    def test_filters_remain_applied_across_sorted_pages(self):
        expected = {self.make_media(f"match-{i}.png", 1000) for i in range(5)}
        for media_id in expected:
            self.media.set_flags(DEFAULT_PROFILE_ID, media_id, favorite=True)
        self.make_media("not-favorite.png", 1000)
        later = self.make_media("too-late.png", 2000)
        self.media.set_flags(DEFAULT_PROFILE_ID, later, favorite=True)
        for sort in ("newest", "oldest", "random"):
            params = {
                "sort": sort,
                "media_kind": "image",
                "favorite": "true",
                "created_before": 1500,
                "limit": 2,
            }
            seen = []
            while True:
                response = self.local_client().get("/api/media", params=params)
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                seen.extend(item["id"] for item in page["items"])
                if not page["next_cursor"]:
                    break
                params["cursor"] = page["next_cursor"]
            self.assertEqual(set(seen), expected)
            self.assertEqual(len(seen), len(expected))
