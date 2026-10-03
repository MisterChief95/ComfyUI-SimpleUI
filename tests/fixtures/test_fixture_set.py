"""Self-check for the COMPAT-001 fixture set.

Run from the repository root:

    python -m unittest discover -s tests/fixtures -v

This guards the fixture set itself, not application code: the manifest must match
the files on disk, every fixture must parse, the exact-integer cases must survive
a Python round trip, the non-finite case must actually be non-finite, and no
personal path, prompt, or credential may be committed.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))


def fixture_files():
    """Every fixture file on disk, excluding this test and the manifest."""
    skip = {ROOT / "MANIFEST.json", Path(__file__).resolve()}
    return sorted(
        p
        for p in ROOT.rglob("*")
        if p.is_file() and p not in skip and p.suffix in {".json", ".jsonl", ".md"}
    )


def strict_load(path):
    """Parse JSON the way the importer must: no NaN/Infinity, no duplicate keys."""

    def no_duplicates(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                raise ValueError(f"duplicate key {key!r}")
            seen.add(key)
        return dict(pairs)

    def reject(constant):
        raise ValueError(f"non-finite constant {constant}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject,
        object_pairs_hook=no_duplicates,
    )


class ManifestMatchesDisk(unittest.TestCase):
    def test_every_listed_file_exists(self):
        for entry in MANIFEST["files"]:
            self.assertTrue((ROOT / entry["path"]).is_file(), entry["path"])

    def test_every_file_is_listed(self):
        listed = {(ROOT / e["path"]).resolve() for e in MANIFEST["files"]}
        listed.add((ROOT / "README.md").resolve())
        for path in fixture_files():
            self.assertIn(path, listed, f"{path.name} is not in MANIFEST.json")

    def test_required_coverage_is_claimed(self):
        claimed = {c for e in MANIFEST["files"] for c in e["covers"]}
        missing = [c for c in MANIFEST["required_coverage"] if c not in claimed]
        self.assertEqual([], missing)

    def test_provenance_is_declared_and_known(self):
        for entry in MANIFEST["files"]:
            self.assertIn(
                entry["provenance"], MANIFEST["provenance_values"], entry["path"]
            )


class FixturesParse(unittest.TestCase):
    def test_accepted_fixtures_parse_strictly(self):
        for entry in MANIFEST["files"]:
            path = ROOT / entry["path"]
            if entry["kind"] == "api_graph_rejected" or path.suffix == ".md":
                continue
            with self.subTest(path=entry["path"]):
                if path.suffix == ".jsonl":
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            json.loads(line)
                else:
                    strict_load(path)

    def test_rejected_fixtures_fail_a_strict_parse_or_validation(self):
        # non_finite and duplicate_keys must fail at parse time. The other three
        # parse cleanly and are rejected later by graph validation, so they only
        # need to be well-formed JSON.
        must_fail_parsing = {
            "graphs/rejected/non_finite_values.api.json",
            "graphs/rejected/duplicate_keys.api.json",
        }
        for entry in MANIFEST["files"]:
            if entry["kind"] != "api_graph_rejected":
                continue
            path = ROOT / entry["path"]
            with self.subTest(path=entry["path"]):
                if entry["path"] in must_fail_parsing:
                    with self.assertRaises(ValueError):
                        strict_load(path)
                else:
                    json.loads(path.read_text(encoding="utf-8"))


class ExactIntegerTransport(unittest.TestCase):
    def test_large_seeds_survive_python_round_trip(self):
        expectations = strict_load(ROOT / "expectations" / "seed_and_transport.json")
        for case in expectations["exact_integer_transport"]["cases"]:
            with self.subTest(node=case["node"]):
                graph = strict_load(ROOT / case["fixture"])
                seed = graph[case["node"]]["inputs"]["seed"]
                self.assertIsInstance(seed, int)
                self.assertEqual(case["graph_value"], seed)
                self.assertEqual(case["descriptor_value"], str(seed))
                # The corruption this guards against: a JavaScript-number round trip.
                self.assertNotEqual(seed, int(float(seed)))

    def test_seed_exceeds_javascript_safe_range(self):
        graph = strict_load(ROOT / "graphs" / "image_large_seed.api.json")
        for node in ("5", "6"):
            self.assertGreater(graph[node]["inputs"]["seed"], 2**53)


class NoPrivateData(unittest.TestCase):
    def test_no_forbidden_substrings(self):
        for path in fixture_files():
            text = path.read_text(encoding="utf-8").lower()
            for needle in MANIFEST["forbidden_substrings"]:
                with self.subTest(path=path.name, needle=needle):
                    self.assertNotIn(needle.lower(), text)

    def test_sanitized_projection_withholds_shared_input_filenames(self):
        raw = (ROOT / "catalog" / "object_info.synthetic.json").read_text(
            encoding="utf-8"
        )
        projection = strict_load(
            ROOT / "catalog" / "sanitized_projection.expected.json"
        )
        withheld = projection["must_not_appear_in_projection"]
        self.assertTrue(withheld)
        rendered = json.dumps(projection["nodes"])
        for name in withheld:
            self.assertIn(
                name, raw, "fixture drift: name should exist in the raw catalog"
            )
            self.assertNotIn(
                name, rendered, "shared input filename leaked into the projection"
            )


class LiveGate(unittest.TestCase):
    def test_no_fixture_claims_live_provenance_while_the_gate_is_blocked(self):
        if MANIFEST["live_gate"] != "blocked":
            self.skipTest("live gate is open; live fixtures are expected")
        for entry in MANIFEST["files"]:
            self.assertEqual("synthetic", entry["provenance"], entry["path"])

    def test_capabilities_record_the_blocked_gate_and_blockers(self):
        caps = strict_load(ROOT / "catalog" / "capabilities.synthetic.json")
        self.assertEqual("synthetic", caps["provenance"])
        self.assertEqual("blocked", caps["live_gate"])
        self.assertIsNone(caps["installation"]["comfyui_revision"])
        self.assertTrue(caps["release_blockers"])


if __name__ == "__main__":
    unittest.main()
