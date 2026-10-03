"""Collection API, storage constraints, and virtual folders."""

from sqlite3 import IntegrityError

from app.storage import Repository

from .test_api import PASSWORD, MediaApiTestCase


class CollectionsTest(MediaApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo = Repository(self.db)
        self.client = self.local_client()
        self.ids = [
            self.repo.record_media(
                "default",
                storage_path=f"{i}.png",
                file_version=f"{i}",
                media_kind="image",
                media_type="image/png",
            )
            for i in range(3)
        ]

    def create(self, name="Trips", client=None):
        response = self.post(
            client or self.client, "/api/media/collections", json={"name": name}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def members(self, collection, ids, remove=False, client=None):
        return self.post(
            client or self.client,
            f"/api/media/collections/{collection}/{'remove' if remove else 'media'}",
            json={"ids": ids},
        )

    def test_crud_duplicates_and_validation(self):
        collection = self.create(" Trips ")
        self.assertEqual(
            self.post(
                self.client, "/api/media/collections", json={"name": "Trips"}
            ).status_code,
            409,
        )
        for name in ("", "   ", "x" * 101):
            self.assertEqual(
                self.post(
                    self.client, "/api/media/collections", json={"name": name}
                ).status_code,
                422,
            )
        other = self.create("Other")
        self.assertEqual(
            self.put(
                self.client, f"/api/media/collections/{other}", json={"name": "Trips"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.put(
                self.client,
                f"/api/media/collections/{collection}",
                json={"name": "Renamed / café"},
            ).json()["name"],
            "Renamed / café",
        )
        self.assertEqual(self.members(collection, []).status_code, 422)
        self.assertEqual(self.members(collection, ["missing"]).status_code, 404)
        self.assertEqual(
            self.delete(self.client, f"/api/media/collections/{other}").status_code, 200
        )
        self.assertEqual(
            self.delete(self.client, f"/api/media/collections/{other}").status_code, 404
        )

    def test_bulk_filter_counts_paging_and_remove(self):
        collection = self.create()
        other = self.create("Also")
        empty = self.create("Empty")
        self.assertEqual(
            self.members(collection, self.ids + self.ids).json(), {"changed": 3}
        )
        self.assertEqual(self.members(collection, self.ids).json(), {"changed": 0})
        self.members(other, self.ids[:1])
        self.media.set_flags("default", self.ids[-1], hidden=True)
        root = self.client.get("/api/media/tree").json()
        self.assertEqual(
            next(c["count"] for c in root["children"] if c["path"] == "Collections"), 2
        )
        tree = self.client.get("/api/media/tree", params={"path": "Collections"}).json()
        self.assertEqual(
            {c["name"]: c["count"] for c in tree["children"]},
            {"Also": 1, "Empty": 0, "Trips": 2},
        )
        path = f"Collections/{collection}"
        self.assertEqual(
            self.client.get("/api/media/tree", params={"path": path}).json()[
                "breadcrumbs"
            ][-1]["name"],
            "Trips",
        )
        for filter_ in ({"path": path}, {"collection_id": collection}):
            for sort in ("newest", "oldest", "random"):
                params = {**filter_, "sort": sort, "limit": 1}
                seen = []
                while True:
                    page = self.client.get("/api/media", params=params).json()
                    seen.extend(item["id"] for item in page["items"])
                    if not page["next_cursor"]:
                        break
                    params["cursor"] = page["next_cursor"]
                self.assertEqual(set(seen), set(self.ids[:2]))
                self.assertEqual(len(seen), 2)
        self.assertEqual(
            self.client.get("/api/media", params={"collection_id": empty}).json()[
                "items"
            ],
            [],
        )
        self.assertEqual(
            self.client.get(
                "/api/media",
                params={"collection_id": collection, "media_kind": "video"},
            ).json()["items"],
            [],
        )
        self.assertEqual(
            self.members(collection, self.ids[:1], remove=True).json(), {"changed": 1}
        )
        self.assertEqual(
            self.members(collection, self.ids[:1], remove=True).json(), {"changed": 0}
        )

    def test_owner_isolation_atomic_bulk_and_database_constraints(self):
        collection = self.create("Shared name")
        self.enable_multi_user(PASSWORD)
        bee_id = self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(self.client, "Default", PASSWORD)
        self.login(bee, "Bee", PASSWORD)
        foreign = self.repo.record_media(
            bee_id,
            storage_path="secret.png",
            file_version="bee",
            media_kind="image",
            media_type="image/png",
        )
        bee_collection = self.create("Shared name", bee)
        self.assertEqual(
            self.members(collection, [self.ids[0], foreign]).status_code, 404
        )
        self.assertEqual(
            self.db.query_one("SELECT COUNT(*) AS n FROM collection_media")["n"], 0
        )
        self.assertEqual(
            self.members(collection, [foreign], client=bee).status_code, 404
        )
        self.assertEqual(
            self.members(
                collection, [self.ids[0]], remove=True, client=bee
            ).status_code,
            404,
        )
        self.assertEqual(
            self.put(
                bee, f"/api/media/collections/{collection}", json={"name": "Stolen"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.delete(bee, f"/api/media/collections/{collection}").status_code, 404
        )
        self.assertEqual(
            [c["id"] for c in bee.get("/api/media/collections").json()["items"]],
            [bee_collection],
        )
        guessed = bee.get(
            "/api/media/tree", params={"path": f"Collections/{collection}"}
        ).json()
        self.assertEqual(guessed["count"], 0)
        self.assertEqual(guessed["breadcrumbs"][-1]["name"], "Unknown collection")
        self.assertEqual(
            bee.get("/api/media", params={"collection_id": collection}).json()["items"],
            [],
        )
        for owner, cid, mid in (
            ("default", collection, foreign),
            ("default", bee_collection, self.ids[0]),
        ):
            with self.assertRaises(IntegrityError), self.db.write() as conn:
                conn.execute(
                    "INSERT INTO collection_media VALUES (?, ?, ?)", (owner, cid, mid)
                )

    def test_cascades_leave_media_intact_and_profile_delete_cleans_membership(self):
        collection = self.create()
        self.members(collection, self.ids)
        with self.db.write() as conn:
            conn.execute("DELETE FROM media WHERE id = ?", (self.ids[0],))
        self.assertEqual(
            self.db.query_one("SELECT COUNT(*) AS n FROM collection_media")["n"], 2
        )
        self.delete(self.client, f"/api/media/collections/{collection}")
        self.assertEqual(
            self.db.query_one("SELECT COUNT(*) AS n FROM collection_media")["n"], 0
        )
        self.assertEqual(len(self.repo.list_media("default").items), 2)
        self.enable_multi_user(PASSWORD)
        owner = self.auth.create_profile("Bee", PASSWORD)
        cid = self.repo.save_collection(owner, "Private")["id"]
        mid = self.repo.record_media(
            owner,
            storage_path="bee.png",
            file_version="bee",
            media_kind="image",
            media_type="image/png",
        )
        self.repo.collection_members(owner, cid, [mid])
        with self.db.write() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (owner,))
        for table in ("collections", "collection_media", "media"):
            self.assertIsNone(
                self.db.query_one(f"SELECT 1 FROM {table} WHERE owner_id = ?", (owner,))
            )
