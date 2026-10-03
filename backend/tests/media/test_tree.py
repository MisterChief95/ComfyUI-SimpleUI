"""Computed gallery folders share owner isolation and the gallery keyset query."""

from datetime import datetime, timezone

from app.storage import DEFAULT_PROFILE_ID, Repository

from .test_api import MediaApiTestCase, PASSWORD


class MediaTreeTest(MediaApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo = Repository(self.db)
        self.workflow = self.repo.create_workflow(DEFAULT_PROFILE_ID, "Portrait / café", {})
        self.generation = self.generation_for(DEFAULT_PROFILE_ID, self.workflow, "owned")
        self.start = int(datetime(2026, 10, 3, tzinfo=timezone.utc).timestamp() * 1000)
        self.images = [self.add_media(f"private/path/{i}.png", self.start + i,
                                      generation=self.generation) for i in range(5)]
        self.video_id = self.add_media("clip.mp4", self.start - 1, kind="video")
        no_workflow = self.generation_for(DEFAULT_PROFILE_ID, None, "no-workflow")
        self.orphan = self.add_media("orphan.png", self.start + 100, generation=no_workflow)
        self.media.set_flags(DEFAULT_PROFILE_ID, self.images[0], favorite=True)
        hidden = self.add_media("hidden.png", self.start, generation=self.generation)
        self.media.set_flags(DEFAULT_PROFILE_ID, hidden, hidden=True, favorite=True)

    def generation_for(self, owner, workflow, key):
        generation_id = self.repo.create_generation(
            owner, client_request_key=key, request_fingerprint=key,
            workflow_id=workflow, graph={}, effective_values={"prompt": "portrait"},
            status="succeeded",
        )
        self.repo.update_generation_status(owner, generation_id, status="succeeded", output_state="ready")
        return generation_id

    def add_media(self, name, stamp, *, owner=DEFAULT_PROFILE_ID, generation=None, kind="image"):
        media_id = self.repo.record_media(
            owner, storage_path=name, file_version=name, generation_id=generation,
            media_kind=kind, media_type="video/mp4" if kind == "video" else "image/png",
        )
        with self.db.write() as conn:
            conn.execute("UPDATE media SET created_ms = ? WHERE id = ?", (stamp, media_id))
        return media_id

    def tree(self, path="", client=None):
        response = (client or self.local_client()).get("/api/media/tree", params={"path": path})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_counts_dates_names_and_unsorted(self):
        root = self.tree()
        self.assertEqual(root["timezone"], "UTC")
        self.assertEqual(root["count"], 7)
        self.assertEqual({c["name"]: c["count"] for c in root["children"]},
                         {"Date": 7, "Workflow": 5, "Favorites": 1, "Videos": 1, "Unsorted": 2, "Collections": 0})
        self.assertEqual(self.tree("Date")["children"],
                         [{"path": "Date/2026", "name": "2026", "count": 7}])
        self.assertEqual(self.tree("Date/2026")["children"],
                         [{"path": "Date/2026/10", "name": "10", "count": 7}])
        self.assertEqual({c["name"]: c["count"] for c in self.tree("Date/2026/10")["children"]},
                         {"03": 6, "02": 1})
        workflows = self.tree("Workflow")
        self.assertEqual(workflows["children"],
                         [{"path": f"Workflow/{self.workflow}", "name": "Portrait / café", "count": 5}])
        leaf = self.tree(f"Workflow/{self.workflow}")
        self.assertEqual(leaf["children"], [])
        self.assertEqual(leaf["breadcrumbs"][-1]["name"], "Portrait / café")
        self.assertNotIn("private/path", str(root) + str(workflows) + str(leaf))
        self.repo.delete_workflow(DEFAULT_PROFILE_ID, self.workflow)
        self.assertEqual(self.tree("Unsorted")["count"], 7)
        self.assertEqual(self.tree("Workflow")["children"], [])

    def test_leaf_paging_intersects_filters_for_all_sorts(self):
        client = self.local_client()
        folders = {f"Workflow/{self.workflow}": set(self.images),
                   "Date/2026/10/03": {*self.images, self.orphan},
                   "Favorites": {self.images[0]}, "Videos": {self.video_id},
                   "Unsorted": {self.video_id, self.orphan}}
        for path, expected in folders.items():
            for sort in ("newest", "oldest", "random"):
                params = {"path": path, "sort": sort, "limit": 2}
                seen = []
                while True:
                    response = client.get("/api/media", params=params)
                    self.assertEqual(response.status_code, 200, response.text)
                    page = response.json()
                    seen.extend(item["id"] for item in page["items"])
                    if not page["next_cursor"]:
                        break
                    params["cursor"] = page["next_cursor"]
                self.assertEqual(set(seen), expected)
                self.assertEqual(len(seen), len(expected))
                self.assertEqual(self.tree(path)["count"], len(expected))
        self.assertEqual(client.get("/api/media", params={
            "path": f"Workflow/{self.workflow}", "favorite": "true", "prompt": "portrait",
            "created_after": self.start, "created_before": self.start,
        }).json()["items"][0]["id"], self.images[0])
        self.assertEqual(client.get("/api/media", params={
            "path": "Videos", "media_kind": "image",
        }).json()["items"], [])
        self.assertEqual(client.get("/api/media", params={
            "path": "Unsorted", "workflow_id": self.workflow,
        }).json()["items"], [])

    def test_owner_isolation_including_guessed_workflow_paths(self):
        self.enable_multi_user(PASSWORD)
        bee_id = self.auth.create_profile("Bee", PASSWORD)
        workflow = self.repo.create_workflow(bee_id, "Secret workflow", {})
        generation = self.generation_for(bee_id, workflow, "bee")
        foreign = self.add_media("bee.png", self.start, owner=bee_id, generation=generation)
        owner = self.local_client()
        self.assertEqual(self.login(owner, "Default", PASSWORD).status_code, 200)
        self.assertEqual(self.tree(client=owner)["count"], 7)
        guessed = self.tree(f"Workflow/{workflow}", owner)
        self.assertEqual(guessed["count"], 0)
        self.assertNotIn("Secret workflow", str(guessed))
        self.assertEqual(owner.get("/api/media", params={"path": f"Workflow/{workflow}"}).json()["items"], [])
        bee = self.local_client()
        self.assertEqual(self.login(bee, "Bee", PASSWORD).status_code, 200)
        self.assertEqual(self.tree(client=bee)["count"], 1)
        self.assertEqual(self.tree("Workflow", bee)["children"][0]["name"], "Secret workflow")
        self.assertEqual(bee.get("/api/media", params={"path": "Date/2026/10/03"}).json()["items"][0]["id"], foreign)

    def test_invalid_paths_and_query_plan(self):
        client = self.local_client()
        for path in ("../", "/Date", "Date/2026/02/30", "Date/0000", "Date/9999",
                     "Date/2026/13", "Favorites/nested", "Workflow/../../secret"):
            for endpoint in ("/api/media/tree", "/api/media"):
                self.assertEqual(client.get(endpoint, params={"path": path}).status_code, 400, path)
        # Existing owner/time index bounds the counts; no new migration is needed.
        for path in ("", "Date/2026/10", "Workflow", "Unsorted", "Favorites", "Videos"):
            sql, params = self.repo._media_folder(path)
            plan = self.db.query(
                f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM media WHERE owner_id = ? AND hidden = 0 {sql}",
                (DEFAULT_PROFILE_ID, *params),
            )
            details = " ".join(row["detail"] for row in plan)
            self.assertTrue("media_by_owner_time" in details or "media_owner_id" in details, details)
            self.assertNotIn("SCAN media", details)
