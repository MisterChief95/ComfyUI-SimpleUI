"""FEAT-010: persist typed control values as a new workflow revision."""

from __future__ import annotations

import json

from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot
from tests.generations.test_routes import FakeUpstream
from tests.workflows.test_layout import FIXTURES, GRAPH, LayoutTestCase, doc

BIG = 2**64 + 12345


class ValuesTest(LayoutTestCase):
    def setUp(self) -> None:
        super().setUp()
        raw = json.loads(
            (FIXTURES / "catalog" / "object_info.synthetic.json").read_text(
                encoding="utf-8"
            )
        )
        raw["TypedNode"] = {
            **raw["SaveImage"],
            "name": "TypedNode",
            "input": {
                "required": {
                    "flag": ["BOOLEAN", {"default": False}],
                    "big": ["INT", {"default": 0, "min": 0, "max": 2**65}],
                }
            },
            "input_order": {"required": ["flag", "big"]},
        }
        self.snapshot = CatalogSnapshot(
            freshness=CatalogFreshness(state="fresh"),
            nodes=normalize(raw).nodes,
            capabilities={},
        )
        self.client = self.local_client()
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        graph["8"] = {"class_type": "TypedNode", "inputs": {"flag": False, "big": 0}}
        response = self.post(
            self.client,
            "/api/workflows?name=Values",
            content=json.dumps(graph),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.workflow_id = response.json()["id"]
        self.base = f"/api/workflows/{self.workflow_id}"

    def save(self, edits: dict, expected_revision: int = 1):
        return self.put(
            self.client,
            f"{self.base}/values",
            json={"edits": edits, "expected_revision": expected_revision},
        )

    def stored_graph(self) -> dict:
        row = self.db.query_one(
            "SELECT graph_json FROM workflow_revisions WHERE workflow_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (self.workflow_id,),
        )
        return json.loads(row["graph_json"])

    def test_boolean_persists_and_is_visible_in_controls(self) -> None:
        response = self.save({"8:flag": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            (
                response.json()["revision"],
                response.json()["added"],
                response.json()["removed"],
            ),
            (2, [], []),
        )
        self.assertIs(self.stored_graph()["8"]["inputs"]["flag"], True)
        controls = {
            control["binding_id"]: control
            for control in self.client.get(f"{self.base}/controls").json()["controls"]
        }
        self.assertIs(controls["8:flag"]["value"], True)

    def test_big_integer_round_trips_exactly(self) -> None:
        response = self.save({"8:big": str(BIG)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.stored_graph()["8"]["inputs"]["big"], BIG)
        controls = self.client.get(f"{self.base}/controls").json()["controls"]
        self.assertEqual(
            next(c for c in controls if c["binding_id"] == "8:big")["value"], str(BIG)
        )

    def test_stale_revision_is_409_and_leaves_graph_unchanged(self) -> None:
        self.assertEqual(self.save({"8:flag": True}).status_code, 200)
        response = self.save({"8:flag": False}, expected_revision=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "conflict")
        self.assertIs(self.stored_graph()["8"]["inputs"]["flag"], True)

    def test_invalid_value_and_unknown_binding_are_422_and_write_nothing(self) -> None:
        before = self.stored_graph()
        for edits in ({"8:flag": "yes"}, {"99:missing": True}):
            response = self.save(edits)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["error"]["code"], "unprocessable")
        self.assertEqual(self.stored_graph(), before)
        self.assertEqual(self.client.get(f"{self.base}/controls").json()["revision"], 1)
        self.assertEqual(self.save({}).status_code, 422)

    def test_correction_display_range_is_used_for_decoding(self) -> None:
        correction = self.put(
            self.client,
            f"{self.base}/corrections",
            json={
                "binding_id": "5:cfg",
                "presentation": {"display_max": 8.0},
                "expected_revision": 0,
            },
        )
        self.assertEqual(correction.status_code, 200, correction.text)
        response = self.save({"5:cfg": 9.0})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.stored_graph()["5"]["inputs"]["cfg"], 7.5)

    def test_layout_stays_non_stale_after_saving_values(self) -> None:
        saved = self.put(
            self.client,
            f"{self.base}/layout",
            json={"layout": doc(), "expected_revision": 0},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        before = saved.json()
        self.assertEqual(self.save({"8:flag": True}).status_code, 200)
        after = self.client.get(f"{self.base}/layout").json()
        self.assertEqual(after["layout"], before["layout"])
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["schema_signature"], before["schema_signature"])
        self.assertEqual(after["stale_bindings"], [])

    def test_saved_values_are_used_by_generation(self) -> None:
        self.app.state.generations.upstream = FakeUpstream()
        self.assertEqual(self.save({"8:flag": True}).status_code, 200)
        response = self.post(
            self.client,
            "/api/generations",
            json={
                "workflow_id": self.workflow_id,
                "request_key": "saved-values",
                "edits": {},
                "seed_policy": "fixed",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        graph = json.loads(
            self.db.query_one(
                "SELECT graph_json FROM generations WHERE id = ?",
                (response.json()["id"],),
            )["graph_json"]
        )
        self.assertIs(graph["8"]["inputs"]["flag"], True)


if __name__ == "__main__":
    import unittest

    unittest.main()
