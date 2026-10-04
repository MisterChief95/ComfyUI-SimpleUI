"""Gallery suggestions use owned generation history with fixed work/result bounds."""

from app.storage import DEFAULT_PROFILE_ID, Repository

from .test_api import PASSWORD, MediaApiTestCase


class SuggestionsTest(MediaApiTestCase):
    def setUp(self):
        super().setUp()
        self.repo = Repository(self.db)

    def generation(
        self, values, *, owner=DEFAULT_PROFILE_ID, workflow=None, key="saved"
    ):
        return self.repo.create_generation(
            owner,
            client_request_key=key,
            request_fingerprint=key,
            workflow_id=workflow,
            effective_values=values,
            status="succeeded",
        )

    def suggestions(self, q, *, client=None, limit=10):
        response = (client or self.local_client()).get(
            "/api/media/suggestions",
            params={"q": q, "limit": limit},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    def test_names_values_and_selected_filter(self):
        workflow = self.repo.create_workflow(DEFAULT_PROFILE_ID, "Café portraits", {})
        generation = self.generation(
            {
                "prompt": "a café portrait",
                "sampler": "euler",
                "seed": "12345678901234567890",
                "quote": 'a "quoted" portrait',
                "literal": "100%_literal",
                "duplicate": "a café portrait",
                "blank": " ",
                "nested": ["secret portrait"],
                "enabled": False,
                "steps": 42,
                "scale": 7.5,
                "file": "C:\\private\\portrait.png",
                "relative": "private/portrait.png",
                "posix": "/private/portrait.png",
                "embedded": "use C:\\private\\portrait.png",
            },
            workflow=workflow,
        )
        media_id = self.repo.record_media(
            DEFAULT_PROFILE_ID,
            storage_path="private/output.png",
            file_version="v1",
            generation_id=generation,
            media_kind="image",
            media_type="image/png",
        )
        self.assertEqual(
            set(self.suggestions("café")), {"Café portraits", "a café portrait"}
        )
        self.assertEqual(self.suggestions("eu"), ["euler"])
        self.assertEqual(self.suggestions("1234"), ["12345678901234567890"])
        self.assertEqual(self.suggestions("%_"), ["100%_literal"])
        self.assertEqual(self.suggestions("fal"), ["false"])
        self.assertEqual(self.suggestions("42"), ["42"])
        self.assertEqual(self.suggestions("7.5"), ["7.5"])
        self.assertFalse(
            any(
                "private" in value or "secret" in value
                for value in self.suggestions("portrait")
            )
        )
        client = self.local_client()
        for selected in (
            "Café portraits",
            "a café portrait",
            'a "quoted" portrait',
            "100%_literal",
            "false",
            "42",
            "7.5",
        ):
            page = client.get("/api/media", params={"prompt": selected}).json()
            self.assertEqual(
                [item["id"] for item in page["items"]], [media_id], selected
            )
        self.assertEqual(
            client.get("/api/media", params={"prompt": "sampler"}).json()["items"], []
        )

    def test_owner_isolation_including_foreign_workflow_association(self):
        self.enable_multi_user(PASSWORD)
        bee = self.auth.create_profile("Bee", PASSWORD)
        workflow = self.repo.create_workflow(bee, "Secret workflow", {})
        self.generation({"prompt": "secret bee"}, owner=bee, workflow=workflow)
        self.generation({"prompt": "secret default"}, workflow=workflow)
        default_client = self.local_client()
        self.assertEqual(
            self.login(default_client, "Default", PASSWORD).status_code, 200
        )
        self.assertEqual(
            self.suggestions("secret", client=default_client), ["secret default"]
        )
        bee_client = self.local_client()
        self.assertEqual(self.login(bee_client, "Bee", PASSWORD).status_code, 200)
        self.assertEqual(
            set(self.suggestions("secret", client=bee_client)),
            {"Secret workflow", "secret bee"},
        )
        self.assertEqual(
            self.local_client().get("/api/media/suggestions?q=secret").status_code, 401
        )

    def test_limit_and_bounded_scan(self):
        self.generation({str(i): f"match {i}" for i in range(30)})
        self.assertEqual(len(self.suggestions("match")), 10)
        self.assertEqual(len(self.suggestions("match", limit=3)), 3)
        self.assertEqual(
            len(self.repo.media_filter_suggestions(DEFAULT_PROFILE_ID, "match", 999)),
            10,
        )
        client = self.local_client()
        for limit in (0, 11):
            self.assertEqual(
                client.get(
                    "/api/media/suggestions", params={"q": "match", "limit": limit}
                ).status_code,
                422,
            )
        oldest = self.generation({"prompt": "old needle"}, key="oldest")
        with self.db.write() as conn:
            conn.execute(
                "UPDATE generations SET created_ms = 0 WHERE id = ?", (oldest,)
            )
        for i in range(100):
            self.generation({}, key=f"recent-{i}")
        self.assertEqual(self.suggestions("needle"), ["old needle"])
        self.generation({"huge": "x" * 65537, "prompt": "large needle"}, key="large")
        self.assertEqual(
            set(self.suggestions("needle")), {"old needle", "large needle"}
        )
        self.generation(
            {**{str(i): "" for i in range(200)}, "prompt": "late needle"}, key="many"
        )
        self.assertEqual(
            set(self.suggestions("needle")),
            {"old needle", "large needle", "late needle"},
        )
        plan = self.db.query(
            "EXPLAIN QUERY PLAN SELECT id FROM generations WHERE owner_id = ?"
            " ORDER BY created_ms DESC, id DESC LIMIT 100",
            (DEFAULT_PROFILE_ID,),
        )
        self.assertIn("generations_by_owner_time", str([tuple(row) for row in plan]))

    def test_empty_short_and_cleared_history(self):
        self.assertEqual(self.suggestions("nothing"), [])
        generation = self.generation(
            {"prompt": "retained value", "empty": "", "null": None}
        )
        for query in ("", "r", " r ", "  "):
            self.assertEqual(self.suggestions(query), [])
        self.assertEqual(self.suggestions("retained"), ["retained value"])
        self.repo.purge_generation_snapshot(DEFAULT_PROFILE_ID, generation)
        self.generation(None, key="no-values")
        self.assertEqual(self.suggestions("retained"), [])
        self.assertEqual(
            self.local_client()
            .get("/api/media/suggestions", params={"q": "x" * 501})
            .status_code,
            422,
        )
