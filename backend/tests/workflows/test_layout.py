"""LAYOUT-001: saved run-page layouts over real HTTP and a real migrated database.

Covers the document invariants (422), optimistic revisions (409), stale
binding reporting, owner isolation, cascade deletion, availability without a
catalog, and migration 003 on an existing database.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot
from app.storage import Database
from app.storage.db import MIGRATIONS_DIR
from tests.auth.support import AuthTestCase

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
GRAPH = FIXTURES / "graphs" / "image_basic.api.json"
CATALOG = normalize(
    json.loads((FIXTURES / "catalog" / "object_info.synthetic.json").read_text(encoding="utf-8"))
)
FRESH = CatalogSnapshot(freshness=CatalogFreshness(state="fresh"), nodes=CATALOG.nodes, capabilities={})
UNAVAILABLE = CatalogSnapshot(freshness=CatalogFreshness(state="unavailable"), nodes={}, capabilities={})
PASSWORD = "default-password"


def doc(**overrides) -> dict:
    base = {
        "version": 1,
        "sections": [
            {
                "id": "prompt",
                "title": "Prompt",
                "columns": 1,
                "collapsed": False,
                "items": [{"kind": "control", "binding_id": "2:text", "span": "full"}],
            },
            {
                "id": "sampling",
                "title": "Sampling",
                "columns": 2,
                "collapsed": True,
                "items": [
                    {"kind": "control", "binding_id": "5:steps"},
                    {"kind": "control", "binding_id": "5:cfg", "span": "auto"},
                ],
            },
        ],
        "hidden": ["3:text"],
    }
    base.update(overrides)
    return base


def section(**overrides) -> dict:
    base = {"id": "s1", "title": "T", "columns": 1, "collapsed": False, "items": []}
    base.update(overrides)
    return base


class LayoutTestCase(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.snapshot = FRESH

        async def current_snapshot():
            return self.snapshot

        self.app.state.catalog.snapshot = current_snapshot

    def import_graph(self, client) -> str:
        response = self.post(
            client, "/api/workflows?name=Portrait",
            content=GRAPH.read_bytes(), headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def save(self, client, workflow_id: str, layout: dict, expected: int = 0):
        return self.put(
            client, f"/api/workflows/{workflow_id}/layout",
            json={"layout": layout, "expected_revision": expected},
        )


class RoundTripTest(LayoutTestCase):
    def test_aspect_ratio_validation_uniqueness_and_stale_bindings(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        pair = {"kind": "aspect_ratio", "width": "4:width", "height": "4:height", "presets": [[1024, 1024], [1152, 896]]}
        layout = doc(sections=[section(items=[pair])], hidden=[])
        saved = self.save(client, workflow_id, layout)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["layout"]["sections"][0]["items"][0]["presets"], pair["presets"])
        for invalid in (
            {**pair, "height": "4:width"},
            {**pair, "height": "2:text"},
            {**pair, "height": "5:cfg"},
            {**pair, "height": "gone:height"},
            {**pair, "presets": [[0, 1024]]},
            {**pair, "presets": [[True, 1024]]},
            {**pair, "presets": [[1024, 1024, 1024]]},
        ):
            response = self.save(client, workflow_id, doc(sections=[section(items=[invalid])], hidden=[]), 1)
            self.assertEqual(response.status_code, 422, response.text)
        duplicate = doc(sections=[section(items=[pair, {"kind": "control", "binding_id": "4:height"}])], hidden=[])
        self.assertEqual(self.save(client, workflow_id, duplicate, 1).status_code, 422)
        self.assertEqual(self.save(client, workflow_id, doc(sections=[section(items=[pair])], hidden=["4:width"]), 1).status_code, 422)
        # Missing bindings survive replacement of a workflow, without silently rewriting the layout.
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        del graph["4"]["inputs"]["height"]
        replaced = self.put(client, f"/api/workflows/{workflow_id}/graph", content=json.dumps(graph), headers={"content-type": "application/json"})
        self.assertEqual(replaced.status_code, 200, replaced.text)
        stale = self.save(client, workflow_id, layout, 1)
        self.assertEqual(stale.status_code, 200, stale.text)
        self.assertEqual(stale.json()["stale_bindings"], ["4:height"])

    def test_unsaved_layout_is_revision_zero_and_null(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        body = client.get(f"/api/workflows/{workflow_id}/layout").json()
        self.assertEqual(
            body,
            {
                "workflow_id": workflow_id,
                "revision": 0,
                "schema_signature": body["schema_signature"],
                "layout": None,
                "stale_bindings": [],
            },
        )
        self.assertTrue(body["schema_signature"].startswith("structure:"))

    def test_save_then_get_round_trips_with_defaults_filled(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        saved = self.save(client, workflow_id, doc())
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], 1)

        expected = doc()
        expected["version"] = 2
        for entry in expected["sections"]:
            entry["mode"] = "auto"
        expected["sections"][1]["items"][0]["span"] = "auto"  # default made explicit
        fetched = client.get(f"/api/workflows/{workflow_id}/layout").json()
        self.assertEqual(fetched["layout"], expected)
        self.assertEqual(fetched, saved.json())
        self.assertEqual(fetched["stale_bindings"], [])

    def test_v1_read_normalizes_without_writing_until_explicit_save(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        self.save(client, workflow_id, doc())
        legacy = json.dumps(doc())
        with self.db.write() as conn:
            conn.execute("UPDATE workflow_layouts SET layout_json = ? WHERE workflow_id = ?", (legacy, workflow_id))
        fetched = client.get(f"/api/workflows/{workflow_id}/layout").json()
        self.assertEqual(fetched["layout"]["version"], 2)
        self.assertEqual([s["mode"] for s in fetched["layout"]["sections"]], ["auto", "auto"])
        self.assertEqual([i["binding_id"] for i in fetched["layout"]["sections"][1]["items"]], ["5:steps", "5:cfg"])
        self.assertEqual(self.db.query_one("SELECT layout_json FROM workflow_layouts WHERE workflow_id = ?", (workflow_id,))["layout_json"], legacy)
        self.assertEqual(self.save(client, workflow_id, fetched["layout"], 1).status_code, 200)
        stored = json.loads(self.db.query_one("SELECT layout_json FROM workflow_layouts WHERE workflow_id = ?", (workflow_id,))["layout_json"])
        self.assertEqual(stored["version"], 2)

    def test_panel_roundtrip_and_invalid_structures_are_422(self) -> None:
        from copy import deepcopy
        from app.workflows.layout import MAX_ROWS, MAX_ENTRIES

        client = self.local_client()
        workflow_id = self.import_graph(client)
        pair = {"kind": "aspect_ratio", "width": "4:width", "height": "4:height"}
        panel = {"id": "p", "title": "Panels", "mode": "panels", "collapsed": False,
                 "rows": [{"id": "r", "columns": [{"id": "c", "items": [pair]},
                                                     {"id": "d", "items": [{"kind": "control", "binding_id": "gone:x"}]}]}]}
        layout = doc(version=2, sections=[panel], hidden=[])
        saved = self.save(client, workflow_id, layout)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["stale_bindings"], ["gone:x"])
        self.assertEqual(saved.json()["layout"]["sections"][0]["rows"][0]["columns"][0]["items"][0]["width"], "4:width")

        def invalid(change):
            value = deepcopy(layout)
            change(value, value["sections"][0], value["sections"][0]["rows"][0])
            response = self.save(client, workflow_id, value, 1)
            self.assertEqual(response.status_code, 422, response.text)

        invalid(lambda v, p, r: v["hidden"].append("4:height"))
        invalid(lambda v, p, r: p.update(toggle="4:width"))
        invalid(lambda v, p, r: r["columns"][1].update(id="c"))
        invalid(lambda v, p, r: r.update(id="c"))
        invalid(lambda v, p, r: p["rows"].append(deepcopy(r)))
        invalid(lambda v, p, r: r.update(columns=[]))
        invalid(lambda v, p, r: r.update(columns=[{"id": f"c{i}", "items": []} for i in range(4)]))
        invalid(lambda v, p, r: p.update(rows=[{"id": f"r{i}", "columns": [{"id": f"c{i}", "items": []}]} for i in range(MAX_ROWS + 1)]))
        invalid(lambda v, p, r: r["columns"][0].update(rows=[]))
        invalid(lambda v, p, r: r["columns"][0].update(items=[{"kind": "row", "columns": []}]))
        invalid(lambda v, p, r: p.update(items=[]))
        invalid(lambda v, p, r: p.update(columns=2))
        invalid(lambda v, p, r: r["columns"][0].update(items=[{**pair, "height": "2:text"}]))
        invalid(lambda v, p, r: v.update(hidden=[f"h{i}" for i in range(MAX_ENTRIES - 2)]))
        invalid(lambda v, p, r: (v.update(hidden=[f"h{i}" for i in range(MAX_ENTRIES - 3)]), p.update(toggle="on")))
        invalid(lambda v, p, r: v.update(version=1))
        # Empty structural containers cannot bypass the row cap; max rows and columns are valid.
        boundary = doc(version=2, sections=[{**panel, "rows": [
            {"id": f"r{i}", "columns": [{"id": f"c{i}_{j}", "items": []} for j in range(3)]}
            for i in range(MAX_ROWS)]}], hidden=[])
        self.assertEqual(self.save(client, workflow_id, boundary, 1).status_code, 200)

    def test_delete_reverts_to_the_automatic_layout_and_is_idempotent(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        self.save(client, workflow_id, doc())
        url = f"/api/workflows/{workflow_id}/layout"
        self.assertEqual(self.delete(client, url).status_code, 204)
        self.assertEqual(self.delete(client, url).status_code, 204)
        body = client.get(url).json()
        self.assertEqual((body["revision"], body["layout"]), (0, None))
        # A save after a reset starts again from revision 0.
        self.assertEqual(self.save(client, workflow_id, doc()).json()["revision"], 1)

    def test_mutations_need_csrf(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        url = f"/api/workflows/{workflow_id}/layout"
        body = {"layout": doc(), "expected_revision": 0}
        foreign = {"origin": "http://evil.example"}  # CSRF: a mutation must come from our origin
        self.assertEqual(client.put(url, json=body, headers=foreign).status_code, 403)
        self.assertEqual(client.delete(url, headers=foreign).status_code, 403)


class ConflictTest(LayoutTestCase):
    def test_a_stale_revision_is_409_and_leaves_the_stored_document(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        self.assertEqual(self.save(client, workflow_id, doc()).status_code, 200)

        mine = doc(hidden=[])
        for expected in (0, 5):
            response = self.save(client, workflow_id, mine, expected=expected)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "conflict")
        stored = client.get(f"/api/workflows/{workflow_id}/layout").json()
        self.assertEqual((stored["revision"], stored["layout"]["hidden"]), (1, ["3:text"]))

        # The documented recovery: re-GET, then overwrite with the fresh revision.
        again = self.save(client, workflow_id, mine, expected=stored["revision"])
        self.assertEqual((again.status_code, again.json()["revision"]), (200, 2))


class ValidationTest(LayoutTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.local_client()
        self.workflow_id = self.import_graph(self.client)

    def reject(self, layout: dict, *, body: dict | None = None) -> None:
        payload = body if body is not None else {"layout": layout, "expected_revision": 0}
        response = self.put(self.client, f"/api/workflows/{self.workflow_id}/layout", json=payload)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "unprocessable")
        stored = self.client.get(f"/api/workflows/{self.workflow_id}/layout").json()
        self.assertEqual(stored["revision"], 0, "a rejected layout must not be stored")

    def test_version_must_be_one_or_two(self) -> None:
        self.reject(doc(version=3))

    def test_unknown_fields_are_refused(self) -> None:
        self.reject(doc(extra=1))
        self.reject({}, body={"layout": doc(), "expected_revision": 0, "owner_id": "x"})

    def test_section_id_pattern_and_uniqueness(self) -> None:
        for bad in ("", "has space", "a" * 41, "dot.dot"):
            self.reject(doc(sections=[section(id=bad)], hidden=[]))
        self.reject(doc(sections=[section(), section(title="Other")], hidden=[]))

    def test_title_is_trimmed_and_length_bounded(self) -> None:
        for bad in ("", "   ", "x" * 81):
            self.reject(doc(sections=[section(title=bad)], hidden=[]))
        saved = self.save(
            self.client, self.workflow_id, doc(sections=[section(title="  Padded  ")], hidden=[])
        )
        self.assertEqual(saved.json()["layout"]["sections"][0]["title"], "Padded")

    def test_columns_span_kind_and_collapsed_are_closed_sets(self) -> None:
        for columns in (0, 4, "2"):
            self.reject(doc(sections=[section(columns=columns)], hidden=[]))
        self.reject(doc(sections=[section(collapsed="true")], hidden=[]))
        item = {"kind": "control", "binding_id": "5:steps"}
        self.reject(doc(sections=[section(items=[{**item, "span": "half"}])], hidden=[]))
        self.reject(doc(sections=[section(items=[{**item, "kind": "aspect_ratio"}])], hidden=[]))

    def test_binding_id_length(self) -> None:
        for bad in ("", "b" * 201):
            self.reject(doc(sections=[], hidden=[bad]))
            self.reject(
                doc(sections=[section(items=[{"kind": "control", "binding_id": bad}])], hidden=[])
            )
        longest = self.save(self.client, self.workflow_id, doc(sections=[], hidden=["b" * 200]))
        self.assertEqual(longest.status_code, 200)

    def test_a_binding_appears_at_most_once(self) -> None:
        item = {"kind": "control", "binding_id": "5:steps"}
        self.reject(doc(sections=[section(items=[item, item])], hidden=[]))
        self.reject(
            doc(sections=[section(items=[item]), section(id="s2", items=[item])], hidden=[])
        )
        self.reject(doc(sections=[section(items=[item])], hidden=["5:steps"]))
        self.reject(doc(sections=[], hidden=["5:steps", "5:steps"]))

    def test_conditions_and_toggles(self) -> None:
        item = {"kind": "control", "binding_id": "5:steps"}
        for bad in (1, True, "", "b" * 201):
            self.reject(doc(sections=[section(items=[{**item, "when": bad}])], hidden=[]))
            self.reject(doc(sections=[section(toggle=bad)], hidden=[]))
        # A toggle places its control: it may not also be an item or hidden.
        self.reject(doc(sections=[section(items=[item], toggle="5:steps")], hidden=[]))
        self.reject(doc(sections=[section(toggle="5:cfg")], hidden=["5:cfg"]))
        # A condition is only a reference: many items may share one, even a placed one.
        both = [{**item, "when": "9:on"}, {"kind": "control", "binding_id": "5:cfg", "when": "9:on"}]
        saved = self.save(
            self.client, self.workflow_id, doc(sections=[section(items=both, toggle="9:on")], hidden=[])
        ).json()
        self.assertEqual(saved["layout"]["sections"][0]["toggle"], "9:on")
        self.assertEqual(saved["layout"]["sections"][0]["items"][1]["when"], "9:on")
        self.assertIn("9:on", saved["stale_bindings"])

    def test_section_cap(self) -> None:
        sections = [section(id=f"s{i}") for i in range(41)]
        self.reject(doc(sections=sections, hidden=[]))
        ok = self.save(self.client, self.workflow_id, doc(sections=sections[:40], hidden=[]))
        self.assertEqual(ok.status_code, 200)

    def test_total_items_plus_hidden_cap(self) -> None:
        items = [{"kind": "control", "binding_id": f"n{i}:x"} for i in range(500)]
        hidden = [f"h{i}:x" for i in range(501)]
        self.reject(doc(sections=[section(items=items)], hidden=hidden))
        ok = self.save(
            self.client, self.workflow_id, doc(sections=[section(items=items)], hidden=hidden[:500])
        )
        self.assertEqual(ok.status_code, 200)

    def test_expected_revision_must_be_a_non_negative_integer(self) -> None:
        self.reject({}, body={"layout": doc(), "expected_revision": -1})
        self.reject({}, body={"layout": doc()})


class StaleBindingTest(LayoutTestCase):
    def test_unknown_bindings_are_kept_and_reported_even_without_a_catalog(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        layout = doc(hidden=["99:gone"])
        layout["sections"][0]["items"].append({"kind": "control", "binding_id": "7:vanished"})

        self.snapshot = UNAVAILABLE  # saving is pure arrangement: no catalog needed
        saved = self.save(client, workflow_id, layout)
        self.assertEqual(saved.status_code, 200, saved.text)

        self.snapshot = FRESH
        body = client.get(f"/api/workflows/{workflow_id}/layout").json()
        self.assertEqual(body["stale_bindings"], ["7:vanished", "99:gone"])
        self.assertIn(
            {"kind": "control", "binding_id": "7:vanished", "span": "auto"},
            body["layout"]["sections"][0]["items"],
        )
        self.assertEqual(body["layout"]["hidden"], ["99:gone"])

    def test_schema_signature_tracks_the_current_graph(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        before = self.save(client, workflow_id, doc()).json()["schema_signature"]
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        graph["99"] = {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}}
        with self.db.write() as conn:
            conn.execute(
                "UPDATE workflow_revisions SET graph_json = ? WHERE workflow_id = ?",
                (json.dumps(graph), workflow_id),
            )
        after = client.get(f"/api/workflows/{workflow_id}/layout").json()
        self.assertNotEqual(after["schema_signature"], before)
        self.assertEqual(after["revision"], 1)


class OwnershipTest(LayoutTestCase):
    def test_another_profile_gets_404_on_every_verb_and_cannot_touch_the_layout(self) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        self.save(owner, workflow_id, doc())

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        url = f"/api/workflows/{workflow_id}/layout"
        self.assertEqual(bee.get(url).status_code, 404)
        self.assertEqual(self.save(bee, workflow_id, doc()).status_code, 404)
        self.assertEqual(self.delete(bee, url).status_code, 404)
        self.assertEqual(owner.get(url).json()["revision"], 1)
        self.assertEqual(self.delete(owner, "/api/workflows/nope/layout").status_code, 404)

    def test_unauthenticated_requests_are_refused(self) -> None:
        self.enable_multi_user(PASSWORD)
        self.assertEqual(self.local_client().get("/api/workflows/x/layout").status_code, 401)


class CascadeAndMigrationTest(LayoutTestCase):
    def rows(self) -> int:
        return self.db.query_one("SELECT COUNT(*) AS n FROM workflow_layouts")["n"]

    def test_deleting_the_workflow_deletes_its_layout(self) -> None:
        client = self.local_client()
        workflow_id = self.import_graph(client)
        self.save(client, workflow_id, doc())
        self.assertEqual(self.rows(), 1)
        self.assertEqual(self.delete(client, f"/api/workflows/{workflow_id}").status_code, 204)
        self.assertEqual(self.rows(), 0)

    def test_deleting_the_profile_deletes_its_layout(self) -> None:
        self.enable_multi_user(PASSWORD)
        bee_id = self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        workflow_id = self.import_graph(bee)
        self.save(bee, workflow_id, doc())
        self.assertEqual(self.rows(), 1)
        with self.db.write() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (bee_id,))
        self.assertEqual(self.rows(), 0)

    def test_the_table_is_strict_and_keyed_by_owner_and_workflow(self) -> None:
        sql = self.db.query_one("SELECT sql FROM sqlite_master WHERE name = 'workflow_layouts'")["sql"]
        self.assertIn("STRICT", sql)
        self.assertIn("PRIMARY KEY (owner_id, workflow_id)", sql)

    def test_migration_003_applies_to_a_database_that_stopped_at_002(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = Path(tmp.name) / "old"
        old.mkdir()
        for script in MIGRATIONS_DIR.glob("00[12]_*.sql"):
            (old / script.name).write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
        path = Path(tmp.name) / "app.sqlite3"
        first = Database(path, migrations_dir=old)
        self.assertEqual(first.schema_version(), 2)
        first.close()

        upgraded = Database(path)
        self.addCleanup(upgraded.close)
        self.assertGreaterEqual(upgraded.schema_version(), 3)
        self.assertIsNotNone(
            upgraded.query_one("SELECT 1 FROM sqlite_master WHERE name = 'workflow_layouts'")
        )


if __name__ == "__main__":
    unittest.main()
