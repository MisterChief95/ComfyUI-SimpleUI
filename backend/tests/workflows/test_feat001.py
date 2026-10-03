"""FEAT-001: value presets, graph replacement, rename, conditional layout reset."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.storage import Database
from app.storage.db import MIGRATIONS_DIR

from tests.generations.test_routes import FakeUpstream
from tests.workflows.test_layout import (
    GRAPH,
    PASSWORD,
    UNAVAILABLE,
    LayoutTestCase,
    doc,
)

BIG = 2**64 + 12345  # not representable as a JS number


def graph_with(**mutate) -> dict:
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    for key, value in mutate.items():
        node, _, name = key.partition("__")
        graph[node]["inputs"][name] = value
    return graph


class FeatTestCase(LayoutTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client = self.local_client()
        self.workflow_id = self.import_graph(self.client)
        self.base = f"/api/workflows/{self.workflow_id}"

    def replace(self, graph: dict | bytes, client=None, workflow_id: str | None = None):
        body = graph if isinstance(graph, bytes) else json.dumps(graph).encode()
        return self.put(
            client or self.client,
            f"/api/workflows/{workflow_id or self.workflow_id}/graph",
            content=body,
            headers={"content-type": "application/json"},
        )

    def create_preset(self, name="P", values=None, client=None, workflow_id=None):
        return self.post(
            client or self.client,
            f"/api/workflows/{workflow_id or self.workflow_id}/presets",
            json={
                "name": name,
                "values": {"5:steps": 20} if values is None else values,
            },
        )


class PresetTest(FeatTestCase):
    def test_crud_round_trip_and_ordering(self) -> None:
        self.assertEqual(self.client.get(f"{self.base}/presets").json(), [])
        first = self.create_preset(
            "  First  ", {"5:steps": 20, "2:text": "hi", "x:flag": True, "5:cfg": 7.5}
        )
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertEqual(body["name"], "First")
        self.assertEqual(body["revision"], 1)
        self.assertEqual(
            body["values"],
            {"5:steps": 20, "2:text": "hi", "x:flag": True, "5:cfg": 7.5},
        )
        self.assertIs(body["values"]["x:flag"], True)
        self.assertTrue(body["created_ms"].isdigit() and body["updated_ms"].isdigit())

        second = self.create_preset("Second").json()
        listed = self.client.get(f"{self.base}/presets").json()
        self.assertEqual({p["id"] for p in listed}, {body["id"], second["id"]})

        url = f"{self.base}/presets/{body['id']}"
        updated = self.put(
            self.client, url, json={"name": "Renamed", "expected_revision": 1}
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            (updated.json()["revision"], updated.json()["name"]), (2, "Renamed")
        )
        self.assertEqual(
            updated.json()["values"], body["values"], "omitted values are kept"
        )
        # The edited preset is now the most recently updated.
        self.assertEqual(
            self.client.get(f"{self.base}/presets").json()[0]["id"], body["id"]
        )

        again = self.put(
            self.client, url, json={"values": {"a": "b"}, "expected_revision": 2}
        )
        self.assertEqual(again.json()["values"], {"a": "b"})
        self.assertEqual(again.json()["name"], "Renamed")

        self.assertEqual(self.delete(self.client, url).status_code, 204)
        self.assertEqual(self.delete(self.client, url).status_code, 404)
        self.assertEqual(len(self.client.get(f"{self.base}/presets").json()), 1)

    def test_stale_revision_is_409_and_changes_nothing(self) -> None:
        preset = self.create_preset().json()
        url = f"{self.base}/presets/{preset['id']}"
        self.assertEqual(
            self.put(
                self.client, url, json={"name": "A", "expected_revision": 1}
            ).status_code,
            200,
        )
        stale = self.put(self.client, url, json={"name": "B", "expected_revision": 1})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "conflict")
        stored = self.client.get(f"{self.base}/presets").json()[0]
        self.assertEqual((stored["name"], stored["revision"]), ("A", 2))

    def test_put_needs_a_change_and_a_revision(self) -> None:
        url = f"{self.base}/presets/{self.create_preset().json()['id']}"
        self.assertEqual(
            self.put(self.client, url, json={"expected_revision": 1}).status_code, 422
        )
        self.assertEqual(
            self.put(self.client, url, json={"name": "x"}).status_code, 422
        )
        self.assertEqual(
            self.put(
                self.client, url, json={"name": "", "expected_revision": 1}
            ).status_code,
            422,
        )

    def test_name_and_value_limits(self) -> None:
        for bad in ("", "   ", "n" * 81):
            self.assertEqual(self.create_preset(bad).status_code, 422, bad)
        self.assertEqual(self.create_preset("n" * 80).status_code, 201)
        self.assertEqual(self.create_preset("v", {"k": "v"}).status_code, 201)

        self.assertEqual(
            self.create_preset("v", {str(i): i for i in range(501)}).status_code, 422
        )
        self.assertEqual(
            self.create_preset("v", {str(i): i for i in range(500)}).status_code, 201
        )
        for key in ("", "k" * 201):
            self.assertEqual(self.create_preset("v", {key: 1}).status_code, 422)
        self.assertEqual(self.create_preset("v", {"k" * 200: 1}).status_code, 201)

        for bad in ({"k": None}, {"k": [1]}, {"k": {"a": 1}}):
            self.assertEqual(self.create_preset("v", bad).status_code, 422, bad)
        self.assertEqual(
            self.post(
                self.client,
                f"{self.base}/presets",
                json={"name": "v", "values": {}, "owner_id": "x"},
            ).status_code,
            422,
        )

    def test_serialized_values_are_capped(self) -> None:
        big = {str(i): "x" * 1000 for i in range(300)}  # ~300 KB across 300 keys
        response = self.create_preset("big", big)
        self.assertEqual(response.status_code, 422, response.text[:200])
        self.assertEqual(
            self.create_preset(
                "ok", {str(i): "x" * 1000 for i in range(200)}
            ).status_code,
            201,
        )

    def test_preset_count_cap(self) -> None:
        for i in range(100):
            self.assertEqual(self.create_preset(f"p{i}", {}).status_code, 201)
        over = self.create_preset("one too many", {})
        self.assertEqual(over.status_code, 409, over.text)
        self.assertIn("100", over.json()["error"]["message"])
        # The cap is per workflow.
        other = self.import_graph(self.client)
        self.assertEqual(
            self.create_preset("fresh", {}, workflow_id=other).status_code, 201
        )

    def test_exact_integers_are_never_lossy(self) -> None:
        raw = (
            '{"name": "seeds", "values": {"3:seed": %d, "3:neg": -%d, "3:str": "%d",'
            ' "3:small": 42, "3:f": 1.5}}' % (BIG, BIG, BIG)
        ).encode()
        created = self.post(
            self.client,
            f"{self.base}/presets",
            content=raw,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        values = created.json()["values"]
        self.assertEqual(values["3:seed"], str(BIG))
        self.assertEqual(values["3:neg"], f"-{BIG}")
        self.assertEqual(values["3:str"], str(BIG))
        self.assertEqual((values["3:small"], values["3:f"]), (42, 1.5))
        self.assertEqual(
            self.client.get(f"{self.base}/presets").json()[0]["values"], values
        )
        # And through update.
        url = f"{self.base}/presets/{created.json()['id']}"
        raw = ('{"values": {"3:seed": %d}, "expected_revision": 1}' % BIG).encode()
        updated = self.put(
            self.client, url, content=raw, headers={"content-type": "application/json"}
        )
        self.assertEqual(updated.json()["values"], {"3:seed": str(BIG)})

    def test_non_finite_floats_are_refused(self) -> None:
        raw = b'{"name": "x", "values": {"k": NaN}}'
        response = self.post(
            self.client,
            f"{self.base}/presets",
            content=raw,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unknown_bindings_are_kept(self) -> None:
        self.assertEqual(
            self.create_preset("v", {"99:gone": "x"}).json()["values"], {"99:gone": "x"}
        )

    def test_mutations_need_csrf_and_missing_things_404(self) -> None:
        foreign = {"origin": "http://evil.example"}
        body = {"name": "x", "values": {}}
        self.assertEqual(
            self.client.post(
                f"{self.base}/presets", json=body, headers=foreign
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.delete(f"{self.base}/presets/x", headers=foreign).status_code,
            403,
        )
        self.assertEqual(self.create_preset(workflow_id="nope").status_code, 404)
        self.assertEqual(
            self.client.get("/api/workflows/nope/presets").status_code, 404
        )
        self.assertEqual(
            self.delete(self.client, f"{self.base}/presets/nope").status_code, 404
        )
        self.assertEqual(
            self.put(
                self.client,
                f"{self.base}/presets/nope",
                json={"name": "x", "expected_revision": 1},
            ).status_code,
            404,
        )

    def test_cascades_with_the_workflow(self) -> None:
        self.create_preset()
        self.assertEqual(self.delete(self.client, self.base).status_code, 204)
        self.assertEqual(
            self.db.query_one("SELECT COUNT(*) AS n FROM workflow_presets")["n"], 0
        )


class PresetOwnershipTest(FeatTestCase):
    def test_other_profiles_cannot_see_or_touch_presets(self) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        preset = self.create_preset(
            "mine", client=owner, workflow_id=workflow_id
        ).json()

        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        url = f"/api/workflows/{workflow_id}/presets"
        self.assertEqual(bee.get(url).status_code, 404)
        self.assertEqual(
            self.create_preset(client=bee, workflow_id=workflow_id).status_code, 404
        )
        self.assertEqual(
            self.put(
                bee, f"{url}/{preset['id']}", json={"name": "x", "expected_revision": 1}
            ).status_code,
            404,
        )
        self.assertEqual(self.delete(bee, f"{url}/{preset['id']}").status_code, 404)
        # Bee's own workflow does not expose the owner's preset id either.
        own = self.import_graph(bee)
        self.assertEqual(
            self.delete(
                bee, f"/api/workflows/{own}/presets/{preset['id']}"
            ).status_code,
            404,
        )
        self.assertEqual(owner.get(url).json()[0]["name"], "mine")


class RenameTest(FeatTestCase):
    def test_rename_returns_info_and_bumps_updated_ms(self) -> None:
        before = self.client.get("/api/workflows").json()["items"][0]
        self.assertEqual(before["current_revision"], 1)
        self.assertTrue(before["updated_ms"].isdigit())
        response = self.client.patch(
            self.base, json={"name": "New name"}, headers=self.csrf_headers(self.client)
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            (body["id"], body["name"], body["current_revision"]),
            (self.workflow_id, "New name", 1),
        )
        self.assertGreaterEqual(int(body["updated_ms"]), int(before["updated_ms"]))
        self.assertEqual(
            self.client.get("/api/workflows").json()["items"][0]["name"], "New name"
        )

    def test_validation_csrf_and_ownership(self) -> None:
        headers = self.csrf_headers(self.client)
        for bad in ("", "n" * 121):
            self.assertEqual(
                self.client.patch(
                    self.base, json={"name": bad}, headers=headers
                ).status_code,
                422,
            )
        self.assertEqual(
            self.client.patch(
                self.base, json={"name": "x" * 120}, headers=headers
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.patch(self.base, json={}, headers=headers).status_code, 422
        )
        self.assertEqual(
            self.client.patch(
                self.base, json={"name": "a"}, headers={"origin": "http://evil.example"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.patch(
                "/api/workflows/nope", json={"name": "a"}, headers=headers
            ).status_code,
            404,
        )

    def test_other_profile_gets_404(self) -> None:
        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        response = bee.patch(
            f"/api/workflows/{workflow_id}",
            json={"name": "mine now"},
            headers=self.csrf_headers(bee),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            owner.get("/api/workflows").json()["items"][0]["name"], "Portrait"
        )


class ReplaceGraphTest(FeatTestCase):
    def revisions(self) -> list[int]:
        rows = self.db.query(
            "SELECT revision FROM workflow_revisions WHERE workflow_id = ? ORDER BY revision",
            (self.workflow_id,),
        )
        return [r["revision"] for r in rows]

    def controls(self) -> dict:
        schema = self.client.get(f"{self.base}/controls").json()
        return {c["binding_id"]: c for c in schema["controls"]}, schema["revision"]

    def test_same_graph_makes_revision_two_with_no_diff(self) -> None:
        response = self.replace(GRAPH.read_bytes())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            (body["revision"], body["added"], body["removed"]), (2, [], [])
        )
        self.assertEqual(body["workflow"]["id"], self.workflow_id)
        self.assertEqual(body["workflow"]["current_revision"], 2)
        self.assertEqual(self.revisions(), [1, 2])
        self.assertEqual(
            self.client.get("/api/workflows").json()["items"][0]["current_revision"], 2
        )
        self.assertEqual(self.client.get(f"{self.base}/controls").json()["revision"], 2)

    def test_changed_graph_reports_added_and_removed_and_reads_use_it(self) -> None:
        controls, _ = self.controls()
        self.assertEqual(
            controls["5:steps"]["value"],
            str(json.loads(GRAPH.read_text())["5"]["inputs"]["steps"]),
        )
        graph = graph_with(**{"5__steps": 33})
        graph["99"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "extra", "clip": ["1", 1]},
        }
        del graph["3"]
        response = self.replace(graph)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn("99:text", body["added"])
        self.assertIn("3:text", body["removed"])
        controls, revision = self.controls()
        self.assertEqual(revision, 2)
        self.assertEqual(controls["5:steps"]["value"], "33")
        self.assertIn("99:text", controls)
        self.assertNotIn("3:text", controls)

    def test_layout_and_corrections_persist_and_staleness_is_reported(self) -> None:
        layout = doc()
        self.assertEqual(
            self.put(
                self.client,
                f"{self.base}/layout",
                json={"layout": layout, "expected_revision": 0},
            ).status_code,
            200,
        )
        graph = json.loads(GRAPH.read_text(encoding="utf-8"))
        del graph["3"]
        before = self.client.get(f"{self.base}/layout").json()
        self.assertEqual(self.replace(graph).status_code, 200)
        after = self.client.get(f"{self.base}/layout").json()
        self.assertEqual(after["revision"], 1, "layout untouched")
        self.assertEqual(after["layout"], before["layout"])
        self.assertEqual(after["stale_bindings"], ["3:text"])
        self.assertNotEqual(after["schema_signature"], before["schema_signature"])

    def test_invalid_graph_is_422_and_changes_nothing(self) -> None:
        before = self.client.get("/api/workflows").json()["items"][0]
        for bad in (b"not json", b"[]", b"{}", b'{"a": NaN}'):
            response = self.replace(bad)
            self.assertEqual(response.status_code, 422, (bad, response.text))
        self.assertEqual(self.revisions(), [1])
        self.assertEqual(self.client.get("/api/workflows").json()["items"][0], before)

    def test_exact_large_integers_survive_replacement(self) -> None:
        raw = GRAPH.read_text(encoding="utf-8").replace(
            '"steps": 20', '"steps": 18446744073709551617', 1
        )
        if raw == GRAPH.read_text(encoding="utf-8"):
            self.skipTest("fixture has no literal steps: 20")
        self.assertEqual(self.replace(raw.encode()).status_code, 200)
        stored = json.loads(
            self.db.query_one(
                "SELECT graph_json FROM workflow_revisions WHERE workflow_id = ? AND revision = 2",
                (self.workflow_id,),
            )["graph_json"]
        )
        self.assertEqual(stored["5"]["inputs"]["steps"], 18446744073709551617)

    def test_works_without_a_catalog(self) -> None:
        self.snapshot = UNAVAILABLE
        response = self.replace(GRAPH.read_bytes())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)

    def test_csrf_and_ownership(self) -> None:
        url = f"{self.base}/graph"
        body = GRAPH.read_bytes()
        self.assertEqual(
            self.client.put(
                url, content=body, headers={"origin": "http://evil.example"}
            ).status_code,
            403,
        )
        self.assertEqual(self.replace(body, workflow_id="nope").status_code, 404)

        self.enable_multi_user(PASSWORD)
        owner = self.local_client()
        self.login(owner, "Default", PASSWORD)
        workflow_id = self.import_graph(owner)
        self.auth.create_profile("Bee", PASSWORD)
        bee = self.local_client()
        self.login(bee, "Bee", PASSWORD)
        self.assertEqual(
            self.replace(body, client=bee, workflow_id=workflow_id).status_code, 404
        )
        self.assertEqual(
            owner.get(f"/api/workflows/{workflow_id}/controls").json()["revision"], 1
        )

    def test_a_generation_uses_the_new_graph_and_keeps_its_own_revision(self) -> None:
        self.app.state.generations.upstream = FakeUpstream()

        def submit(key: str) -> str:
            response = self.post(
                self.client,
                "/api/generations",
                json={
                    "workflow_id": self.workflow_id,
                    "request_key": key,
                    "seed_policy": "fixed",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()["id"]

        old = submit("before")
        self.assertEqual(self.replace(graph_with(**{"5__steps": 33})).status_code, 200)
        new = submit("after")

        def row(generation_id: str):
            return self.db.query_one(
                "SELECT workflow_revision, graph_json FROM generations WHERE id = ?",
                (generation_id,),
            )

        self.assertEqual(row(old)["workflow_revision"], 1)
        self.assertEqual(row(new)["workflow_revision"], 2)
        self.assertEqual(json.loads(row(new)["graph_json"])["5"]["inputs"]["steps"], 33)
        self.assertNotEqual(
            json.loads(row(old)["graph_json"])["5"]["inputs"]["steps"], 33
        )


class ConditionalLayoutDeleteTest(FeatTestCase):
    def save(self, expected: int):
        return self.put(
            self.client,
            f"{self.base}/layout",
            json={"layout": doc(), "expected_revision": expected},
        )

    def test_matching_revision_deletes_and_mismatch_is_409_untouched(self) -> None:
        self.save(0)
        url = f"{self.base}/layout"
        for wrong in (0, 2):
            response = self.delete(self.client, f"{url}?expected_revision={wrong}")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "conflict")
        self.assertEqual(self.client.get(url).json()["revision"], 1)
        self.assertEqual(
            self.delete(self.client, f"{url}?expected_revision=1").status_code, 204
        )
        self.assertEqual(self.client.get(url).json()["revision"], 0)

    def test_expected_zero_matches_nothing_saved_and_omitted_stays_unconditional(
        self,
    ) -> None:
        url = f"{self.base}/layout"
        self.assertEqual(
            self.delete(self.client, f"{url}?expected_revision=0").status_code, 204
        )
        self.save(0)
        self.assertEqual(self.delete(self.client, url).status_code, 204)
        self.assertEqual(
            self.delete(self.client, f"{url}?expected_revision=-1").status_code, 422
        )
        self.assertEqual(
            self.delete(self.client, f"{url}?expected_revision=x").status_code, 422
        )
        self.assertEqual(
            self.delete(
                self.client, "/api/workflows/nope/layout?expected_revision=0"
            ).status_code,
            404,
        )


class MigrationTest(unittest.TestCase):
    def test_004_applies_to_a_database_that_stopped_at_003(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "migrations"
            old.mkdir()
            for sql in MIGRATIONS_DIR.glob("00[1-3]_*.sql"):
                (old / sql.name).write_text(
                    sql.read_text(encoding="utf-8"), encoding="utf-8"
                )
            path = Path(tmp) / "app.sqlite3"
            first = Database(path, migrations_dir=old)
            self.assertEqual(first.schema_version(), 3)
            first.close()
<<<<<<< Updated upstream
            migration = MIGRATIONS_DIR / "004_workflow_presets.sql"
            (old / migration.name).write_text(migration.read_text(encoding="utf-8"), encoding="utf-8")
            second = Database(path, migrations_dir=old)
            try:
                self.assertEqual(second.schema_version(), 4)
                sql = second.query_one("SELECT sql FROM sqlite_master WHERE name = 'workflow_presets'")["sql"]
                self.assertIn("STRICT", sql)
            finally:
                second.close()
=======
            second = Database(path)
            self.assertEqual(second.schema_version(), 4)
            sql = second.query_one(
                "SELECT sql FROM sqlite_master WHERE name = 'workflow_presets'"
            )["sql"]
            self.assertIn("STRICT", sql)
            second.close()
>>>>>>> Stashed changes


if __name__ == "__main__":
    unittest.main()
