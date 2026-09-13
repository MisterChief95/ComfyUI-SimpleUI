"""MEDIA-002 server-side gallery filters and safe history clearing."""

from __future__ import annotations

from app.storage import DEFAULT_PROFILE_ID, Repository

from .test_api import MediaApiTestCase, PASSWORD


class MediaFilterTest(MediaApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = Repository(self.db)
        self.workflow_a = self.repo.create_workflow(DEFAULT_PROFILE_ID, "A", {"1": {}})
        self.workflow_b = self.repo.create_workflow(DEFAULT_PROFILE_ID, "B", {"2": {}})
        self.generation_a = self._generation(
            DEFAULT_PROFILE_ID, "a", self.workflow_a, "quiet forest"
        )
        self.generation_b = self._generation(
            DEFAULT_PROFILE_ID, "b", self.workflow_b, "bright city"
        )
        self.image = self._media(DEFAULT_PROFILE_ID, self.generation_a, "a.png", "image")
        self.video = self._media(DEFAULT_PROFILE_ID, self.generation_b, "b.mp4", "video")
        self.plain = self.repo.record_media(
            DEFAULT_PROFILE_ID,
            storage_path="plain.png",
            file_version="plain",
            media_kind="image",
            media_type="image/png",
        )
        self.media.set_flags(DEFAULT_PROFILE_ID, self.image, favorite=True)
        with self.db.write() as conn:
            conn.execute("UPDATE media SET created_ms = 1000 WHERE id = ?", (self.image,))
            conn.execute("UPDATE media SET created_ms = 2000 WHERE id = ?", (self.video,))

    def _generation(
        self, owner_id: str, key: str, workflow_id: str | None, prompt: str,
        *, status: str = "succeeded", output_state: str = "ready",
    ) -> str:
        generation_id = self.repo.create_generation(
            owner_id,
            client_request_key=key,
            request_fingerprint=key,
            workflow_id=workflow_id,
            graph={"node": key},
            effective_values={"prompt": prompt},
            status=status,
        )
        self.repo.update_generation_status(
            owner_id, generation_id, status=status, output_state=output_state
        )
        return generation_id

    def _media(self, owner_id: str, generation_id: str, filename: str, kind: str) -> str:
        return self.repo.record_media(
            owner_id,
            storage_path=filename,
            file_version=filename,
            media_kind=kind,
            media_type="image/png" if kind == "image" else "video/mp4",
            generation_id=generation_id,
        )

    def _ids(self, query: str) -> set[str]:
        response = self.local_client().get(f"/api/media?{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return {item["id"] for item in response.json()["items"]}

    def test_filters_by_media_kind_and_favorite(self) -> None:
        self.assertEqual(self._ids("media_kind=video"), {self.video})
        self.assertEqual(self._ids("favorite=true"), {self.image})
        self.assertEqual(self._ids("favorite=false"), {self.video, self.plain})

    def test_filters_by_workflow(self) -> None:
        self.assertEqual(self._ids(f"workflow_id={self.workflow_a}"), {self.image})
        self.assertEqual(self._ids(f"workflow_id={self.workflow_b}"), {self.video})

    def test_filters_by_created_range_and_keeps_it_across_pages(self) -> None:
        self.assertEqual(self._ids("created_before=1500"), {self.image})
        self.assertEqual(self._ids("created_after=1500"), {self.video, self.plain})
        self.assertEqual(self._ids("created_after=1500&created_before=2500"), {self.video})
        first = self.local_client().get("/api/media?media_kind=image&limit=1").json()
        second = self.local_client().get(
            f"/api/media?media_kind=image&limit=1&cursor={first['next_cursor']}"
        ).json()
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(len(second["items"]), 1)

    def test_prompt_search_is_literal_and_owner_scoped(self) -> None:
        self.assertEqual(self._ids("prompt=quiet"), {self.image})
        self.assertEqual(self._ids("prompt=%25"), set())

        self.enable_multi_user(PASSWORD)
        bee_id = self.auth.create_profile("Bee", PASSWORD)
        bee_generation = self._generation(bee_id, "bee", None, "private comet")
        bee_media = self._media(bee_id, bee_generation, "bee.png", "image")

        owner = self.local_client()
        self.assertEqual(self.login(owner, "Default", PASSWORD).status_code, 200)
        self.assertEqual(owner.get("/api/media?prompt=private").json()["items"], [])
        bee = self.local_client()
        self.assertEqual(self.login(bee, "Bee", PASSWORD).status_code, 200)
        response = bee.get("/api/media?prompt=private")
        self.assertEqual({item["id"] for item in response.json()["items"]}, {bee_media})


class ClearHistoryTest(MediaApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = Repository(self.db)
        self.generation_a = self._generation(DEFAULT_PROFILE_ID, "a", "first")
        self.generation_b = self._generation(DEFAULT_PROFILE_ID, "b", "second")
        self.repo.record_media(
            DEFAULT_PROFILE_ID,
            storage_path="kept.png",
            file_version="kept",
            media_kind="image",
            media_type="image/png",
            generation_id=self.generation_a,
        )

    def _generation(
        self, owner_id: str, key: str, prompt: str, *,
        status: str = "succeeded", output_state: str = "ready",
    ) -> str:
        generation_id = self.repo.create_generation(
            owner_id,
            client_request_key=key,
            request_fingerprint=key,
            graph={"node": key},
            effective_values={"prompt": prompt},
            status=status,
        )
        self.repo.update_generation_status(
            owner_id, generation_id, status=status, output_state=output_state
        )
        return generation_id

    def test_clear_history_purges_only_owned_proven_terminal_snapshots(self) -> None:
        active = self._generation(DEFAULT_PROFILE_ID, "active", "active", status="running")
        unknown = self._generation(
            DEFAULT_PROFILE_ID, "unknown", "unknown", status="unknown", output_state="unavailable"
        )
        pending = self._generation(
            DEFAULT_PROFILE_ID, "pending", "pending", output_state="pending"
        )
        bee_id = self.repo.create_profile("Bee")
        foreign = self._generation(bee_id, "foreign", "foreign")
        media_count = self.db.query_one("SELECT COUNT(*) AS count FROM media")["count"]
        preserved = {
            generation_id: tuple(
                self.db.query_one(
                    "SELECT owner_id, status, output_state FROM generations WHERE id = ?",
                    (generation_id,),
                )
            )
            for generation_id in (self.generation_a, self.generation_b, active, unknown, pending, foreign)
        }

        response = self.post(self.local_client(), "/api/media/clear-history")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"purged": 2, "deferred": 3})
        for generation_id in (self.generation_a, self.generation_b):
            row = self.db.query_one("SELECT * FROM generations WHERE id = ?", (generation_id,))
            self.assertIsNone(row["graph_json"])
            self.assertIsNone(row["effective_values_json"])
            self.assertEqual(row["status"], "succeeded")
        for generation_id in (active, unknown, pending, foreign):
            row = self.db.query_one("SELECT * FROM generations WHERE id = ?", (generation_id,))
            self.assertIsNotNone(row["graph_json"])
            self.assertIsNotNone(row["effective_values_json"])
        for generation_id, expected in preserved.items():
            row = self.db.query_one(
                "SELECT owner_id, status, output_state FROM generations WHERE id = ?",
                (generation_id,),
            )
            self.assertEqual(tuple(row), expected)
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS count FROM media")["count"], media_count)
