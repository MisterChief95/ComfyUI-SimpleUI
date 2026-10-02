import unittest

from tests.generations.test_routes import GenerationRouteTestCase


class RecentPromptTests(GenerationRouteTestCase):
    def test_distinct_pagination_filter_retention_and_ownership(self):
        client = self.local_client()
        workflow = self.import_graph(client)
        store = self.app.state.generations.store
        for i, value in enumerate(("first prompt", "Second prompt", "first prompt", "100% literal", "")):
            accepted = store.accept("default", workflow_id=workflow, request_key=f"p{i}",
                                    fingerprint=f"p{i}", resolve=lambda v=value: ({}, {"2:text": v}))
            store.update("default", accepted.row["id"], status="succeeded", output_state="ready")
            with self.db.write() as conn:
                conn.execute("UPDATE generations SET created_ms=? WHERE id=?", (100 + i, accepted.row["id"]))
        query = {"workflow_id": workflow, "binding_id": "2:text", "limit": 1}
        url = "/api/generations/recent-prompts"
        first = client.get(url, params=query).json()
        self.assertEqual([v["text"] for v in first["items"]], ["100% literal"])
        second = client.get(url, params={**query, "cursor": first["next_cursor"]}).json()
        self.assertEqual([v["text"] for v in second["items"]], ["first prompt"])
        third = client.get(url, params={**query, "cursor": second["next_cursor"]}).json()
        self.assertEqual([v["text"] for v in third["items"]], ["Second prompt"])
        self.assertIsNone(third["next_cursor"])
        filtered = client.get(url, params={**query, "q": "SECOND"}).json()
        self.assertEqual(filtered["items"][0]["text"], "Second prompt")
        self.assertEqual(client.get(url, params={**query, "q": "%"}).json()["items"][0]["text"], "100% literal")
        self.assertEqual(client.get(url, params={**query, "binding_id": "5:seed"}).status_code, 422)
        self.assertEqual(client.get(url, params={**query, "cursor": "bad!"}).status_code, 400)
        self.app.state.settings.set_profile("default", "store_history", False)
        self.assertEqual(client.get(url, params=query).json()["items"], [])
        self.app.state.settings.set_profile("default", "store_history", True)
        rows = self.db.query("SELECT id FROM generations WHERE owner_id='default'")
        for row in rows:
            store.purge_snapshot("default", row["id"])
        self.assertEqual(client.get(url, params=query).json()["items"], [])

        self.enable_multi_user()
        self.auth.create_profile("Other", "another-password")
        other = self.lan_client()
        self.assertEqual(self.login(other, "Other", "another-password").status_code, 200)
        self.assertEqual(other.get(url, params=query).status_code, 404)
        other_workflow = self.import_graph(other)
        self.assertEqual(other.get(url, params={**query, "workflow_id": other_workflow}).json()["items"], [])


if __name__ == "__main__":
    unittest.main()
