"""GAL-008 stored metadata scope, bounded work, and gallery paging."""

from app.storage import DEFAULT_PROFILE_ID, Repository

from .test_api import MediaApiTestCase


class SavedSearchTest(MediaApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo = Repository(self.db)

    def saved(self, values, *, owner=DEFAULT_PROFILE_ID, workflow=None, stamp=1000):
        key = str(self.db.query_one("SELECT COUNT(*) AS n FROM generations")["n"])
        generation = self.repo.create_generation(
            owner,
            client_request_key=key,
            request_fingerprint=key,
            workflow_id=workflow,
            effective_values=values,
            status="succeeded",
        )
        media = self.repo.record_media(
            owner,
            storage_path=f"{key}.png",
            file_version=key,
            generation_id=generation,
            media_kind="image",
            media_type="image/png",
        )
        with self.db.write() as conn:
            conn.execute(
                "UPDATE generations SET created_ms = ? WHERE id = ?",
                (stamp, generation),
            )
            conn.execute("UPDATE media SET created_ms = ? WHERE id = ?", (stamp, media))
        return generation, media

    def ids(self, term, field="any", **params):
        response = self.local_client().get(
            "/api/media",
            params={"prompt": term, "search_field": field, **params},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return [item["id"] for item in response.json()["items"]]

    def test_field_scope_scalars_unicode_and_literal_terms(self):
        workflow = self.repo.create_workflow(DEFAULT_PROFILE_ID, "Workflow needle", {})
        _, prompt = self.saved(
            {
                "6:text": "CAFÉ needle",
                "8:steps": 42,
                "9:enabled": False,
                "10:seed": "12345678901234567890",
            }
        )
        _, model = self.saved({"4:ckpt_name": "models/needle%_.safetensors"})
        _, control = self.saved({"3:sampler_name": "needle", "nested": ["hidden"]})
        _, named = self.saved({}, workflow=workflow)
        self.assertEqual(set(self.ids("needle")), {prompt, model, control, named})
        self.assertEqual(self.ids("needle", "prompt"), [prompt])
        self.assertEqual(self.ids("needle", "model"), [model])
        self.assertEqual(self.ids("café", "prompt"), [prompt])
        self.assertEqual(self.ids("%_", "model"), [model])
        for term in ("42", "false", "12345678901234567890"):
            self.assertEqual(self.ids(term), [prompt])
            self.assertEqual(self.ids(term, "model"), [])
        self.assertEqual(self.ids("sampler_name"), [])
        self.assertEqual(self.ids("hidden"), [])
        response = self.local_client().get(
            "/api/media/suggestions",
            params={
                "q": "needle",
                "search_field": "prompt",
            },
        )
        self.assertEqual(response.json()["items"], ["CAFÉ needle"])

    def test_owner_import_and_purged_metadata(self):
        other = self.repo.create_profile("Other")
        foreign_workflow = self.repo.create_workflow(other, "private needle", {})
        foreign_generation, _ = self.saved(
            {"4:ckpt_name": "private needle"}, owner=other
        )
        # Even an inconsistent association cannot lend another owner's snapshot.
        self.repo.record_media(
            DEFAULT_PROFILE_ID,
            storage_path="foreign.png",
            file_version="f",
            generation_id=foreign_generation,
            media_kind="image",
            media_type="image/png",
        )
        self.saved({}, workflow=foreign_workflow)
        self.repo.record_media(
            DEFAULT_PROFILE_ID,
            storage_path="needle.png",
            file_version="i",
            media_kind="image",
            media_type="image/png",
        )
        self.assertEqual(self.ids("needle"), [])
        self.assertEqual(self.ids("needle", "model"), [])
        self.assertEqual(
            len(
                self.repo.list_media(other, prompt="needle", search_field="model").items
            ),
            1,
        )
        workflow = self.repo.create_workflow(DEFAULT_PROFILE_ID, "retained needle", {})
        generation, media = self.saved({"6:prompt": "needle"}, workflow=workflow)
        self.repo.purge_generation_snapshot(DEFAULT_PROFILE_ID, generation)
        self.assertEqual(self.ids("needle", "prompt"), [])
        self.assertEqual(self.ids("needle"), [media])

    def test_paging_and_sort_with_folder_and_filters(self):
        expected = [
            self.saved({"4:ckpt_name": "needle"}, stamp=stamp)[1]
            for stamp in (1000, 2000, 3000)
        ]
        self.saved({"6:text": "needle"}, stamp=4000)
        for sort in ("newest", "oldest", "random"):
            seen, cursor = [], None
            while True:
                params = {
                    "prompt": "needle",
                    "search_field": "model",
                    "limit": 1,
                    "sort": sort,
                    "path": "Date/1970/01/01",
                    "media_kind": "image",
                }
                if cursor:
                    params["cursor"] = cursor
                response = self.local_client().get("/api/media", params=params)
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                seen.extend(item["id"] for item in page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
                self.assertLess(len(seen), 4)
            self.assertEqual(len(seen), 3)
            self.assertEqual(set(seen), set(expected))
            if sort != "random":
                self.assertEqual(seen, expected if sort == "oldest" else expected[::-1])

    def test_bounds_and_index_plan(self):
        self.saved({"6:prompt": "old needle"}, stamp=0)
        for i in range(1000):
            self.repo.create_generation(
                DEFAULT_PROFILE_ID,
                client_request_key=f"recent-{i}",
                request_fingerprint=str(i),
                effective_values={},
            )
        self.assertEqual(self.ids("old needle", "prompt"), [])
        self.saved(
            {"huge": "x" * 65537, "6:prompt": "large needle"}, stamp=2_000_000_000_000
        )
        self.saved(
            {**{str(i): "" for i in range(200)}, "6:prompt": "late needle"},
            stamp=2_000_000_000_001,
        )
        self.assertEqual(self.ids("needle", "prompt"), [])
        for params in (
            {"prompt": "x" * 501},
            {"prompt": ""},
            {"search_field": "graph"},
        ):
            self.assertEqual(
                self.local_client().get("/api/media", params=params).status_code, 422
            )
        plan = self.db.query(
            "EXPLAIN QUERY PLAN SELECT id FROM generations WHERE owner_id = ?"
            " ORDER BY created_ms DESC, id DESC LIMIT 1000",
            (DEFAULT_PROFILE_ID,),
        )
        self.assertIn("generations_by_owner_time", str([tuple(row) for row in plan]))
        plan = self.db.query(
            "EXPLAIN QUERY PLAN SELECT * FROM media WHERE owner_id = ?"
            " AND generation_id IN (SELECT value FROM json_each(?))"
            " ORDER BY created_ms DESC, id DESC LIMIT 2",
            (DEFAULT_PROFILE_ID, "[]"),
        )
        self.assertIn("media_by_owner_time", str([tuple(row) for row in plan]))
