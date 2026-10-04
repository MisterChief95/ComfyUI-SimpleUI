"""MAPPING-002: saved mapping corrections, one class per acceptance criterion.

Run from backend/:  python -m unittest discover -s tests -v

These go through the real ``WorkflowService`` and a real migrated SQLite
database, so profile isolation is exercised where it is actually enforced -- the
owner-scoped repository -- rather than against a stub. The catalog is the same
synthetic object_info fixture MAPPING-001 uses, pushed through the real
normalizer.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot
from app.mapping import parse_graph
from app.mapping.corrections import (
    CorrectionError,
    Presentation,
    SaveCorrection,
    content_hash,
    structural_signature,
)
from app.storage import Database
from app.storage.repository import Repository, RevisionConflict
from app.workflows import WorkflowService
from pydantic import ValidationError

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
GRAPHS = FIXTURES / "graphs"
CATALOG = normalize(
    json.loads(
        (FIXTURES / "catalog" / "object_info.synthetic.json").read_text(
            encoding="utf-8"
        )
    )
)
SNAPSHOT = CatalogSnapshot(
    nodes=CATALOG.nodes,
    freshness=CatalogFreshness(state="fresh"),
)

#: In image_basic.api.json node 2 feeds the sampler's positive input and node 3
#: its negative input, so the two CLIPTextEncode text controls differ only by
#: traced graph context -- exactly the case a node template must not flatten.
POSITIVE = "2:text"
NEGATIVE = "3:text"


def graph_of(name: str) -> dict[str, Any]:
    return parse_graph((GRAPHS / name).read_bytes())


class CorrectionTestCase(unittest.TestCase):
    """One migrated database per test, with a second profile to be isolated from."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Database(Path(self._dir.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.repo = Repository(self.db)
        self.service = WorkflowService(self.repo)
        self.other = self.repo.create_profile("Other")

    def import_graph(
        self, name: str = "image_basic.api.json", owner: str = "default"
    ) -> str:
        workflow_id, _ = self.service.import_workflow(
            owner, name, (GRAPHS / name).read_bytes()
        )
        return workflow_id

    def replace_graph(self, workflow_id: str, graph: dict[str, Any]) -> None:
        """Stand in for a re-export: the stored structure changes under a save."""
        with self.db.write() as conn:
            conn.execute(
                "UPDATE workflow_revisions SET graph_json = ? WHERE workflow_id = ?",
                (json.dumps(graph), workflow_id),
            )

    def controls(self, workflow_id: str, owner: str = "default") -> dict[str, Any]:
        schema = self.service.control_schema(owner, workflow_id, SNAPSHOT)
        return {control.binding_id: control for control in schema.controls}

    def schema(self, workflow_id: str, owner: str = "default"):
        return self.service.control_schema(owner, workflow_id, SNAPSHOT)

    def save(
        self,
        workflow_id: str,
        binding_id: str,
        presentation: dict[str, Any],
        *,
        owner: str = "default",
        scope: str = "workflow",
        expected_revision: int = 0,
    ):
        return self.service.save_correction(
            owner,
            workflow_id,
            SNAPSHOT,
            SaveCorrection(
                scope=scope,
                binding_id=binding_id,
                presentation=Presentation(**presentation),
                expected_revision=expected_revision,
            ),
        )


class StructuralSignature(CorrectionTestCase):
    """Literal values are outside the signature; structure is inside it."""

    def test_editing_a_literal_keeps_the_signature(self) -> None:
        graph = graph_of("image_basic.api.json")
        before = structural_signature(graph)
        graph["2"]["inputs"]["text"] = "a completely different prompt"
        graph["5"]["inputs"]["seed"] = 987654321
        graph["5"]["inputs"]["cfg"] = 8.0  # an int/float swap is not a repair
        self.assertEqual(structural_signature(graph), before)
        # The exact content hash is deliberately separate and does move.
        self.assertNotEqual(
            content_hash(graph), content_hash(graph_of("image_basic.api.json"))
        )

    def test_changing_a_node_class_changes_the_signature(self) -> None:
        graph = graph_of("image_basic.api.json")
        before = structural_signature(graph)
        graph["2"]["class_type"] = "SomeOtherTextEncode"
        self.assertNotEqual(structural_signature(graph), before)

    def test_changing_a_connection_changes_the_signature(self) -> None:
        graph = graph_of("image_basic.api.json")
        before = structural_signature(graph)
        graph["5"]["inputs"]["positive"] = ["3", 0]
        self.assertNotEqual(structural_signature(graph), before)

    def test_replacing_a_literal_with_a_link_changes_the_signature(self) -> None:
        graph = graph_of("image_basic.api.json")
        before = structural_signature(graph)
        graph["5"]["inputs"]["steps"] = ["4", 0]
        self.assertNotEqual(structural_signature(graph), before)


class LayoutSurvivesLiteralEdits(CorrectionTestCase):
    """A corrected workflow keeps its layout when only literals change."""

    def test_a_saved_correction_is_replayed_over_the_derived_schema(self) -> None:
        workflow_id = self.import_graph()
        base = self.controls(workflow_id)[NEGATIVE]
        self.assertNotEqual(base.label, "Things to avoid")

        self.save(
            workflow_id,
            NEGATIVE,
            {
                "label": "Things to avoid",
                "group": "advanced",
                "help_text": "Kept per profile.",
            },
        )
        corrected = self.controls(workflow_id)[NEGATIVE]
        self.assertEqual(corrected.label, "Things to avoid")
        self.assertEqual(corrected.group, "advanced")
        self.assertEqual(corrected.help_text, "Kept per profile.")
        self.assertIn("override:workflow", corrected.inference_reason)
        # Presentation only: the literal and its type are untouched.
        self.assertEqual(corrected.value, base.value)
        self.assertEqual(corrected.logical_type, base.logical_type)

    def test_a_literal_edit_keeps_the_corrected_layout(self) -> None:
        workflow_id = self.import_graph()
        self.save(
            workflow_id, NEGATIVE, {"label": "Things to avoid", "group": "advanced"}
        )

        # Only literals change: the structure, and so the signature, is identical.
        edited = graph_of("image_basic.api.json")
        edited["3"]["inputs"]["text"] = "edited negative prompt"
        edited["5"]["inputs"]["seed"] = 4242
        self.replace_graph(workflow_id, edited)

        schema = self.schema(workflow_id)
        control = {c.binding_id: c for c in schema.controls}[NEGATIVE]
        self.assertEqual(control.label, "Things to avoid")
        self.assertEqual(control.group, "advanced")
        self.assertEqual(control.value, "edited negative prompt")
        self.assertNotIn(
            "mapping_correction_stale", {warning.code for warning in schema.warnings}
        )

    def test_reset_returns_the_untouched_base_schema(self) -> None:
        workflow_id = self.import_graph()
        before = self.schema(workflow_id).model_dump()
        self.save(
            workflow_id, NEGATIVE, {"label": "Things to avoid", "group": "advanced"}
        )
        self.assertNotEqual(self.schema(workflow_id).model_dump(), before)

        removed = self.service.reset_corrections("default", workflow_id)
        self.assertEqual(removed, 1)
        self.assertEqual(self.schema(workflow_id).model_dump(), before)


class StaleCorrectionsRequireRepair(CorrectionTestCase):
    """An incompatible structural change is reported, never silently applied."""

    def test_an_incompatible_schema_change_makes_the_correction_stale(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Things to avoid"})

        # The stored graph's structure changes under the saved correction.
        changed = graph_of("image_basic.api.json")
        changed["3"]["class_type"] = "CLIPTextEncodeSDXL"
        self.replace_graph(workflow_id, changed)

        schema = self.schema(workflow_id)
        control = {c.binding_id: c for c in schema.controls}[NEGATIVE]
        self.assertNotEqual(
            control.label, "Things to avoid"
        )  # base label, not the stale one
        self.assertIn(
            "mapping_correction_stale", {warning.code for warning in schema.warnings}
        )

        exported = self.service.export_corrections("default", workflow_id, SNAPSHOT)
        self.assertEqual([c.stale for c in exported], [True])
        self.assertEqual(
            exported[0].presentation.label, "Things to avoid"
        )  # kept for repair

    def test_repairing_a_stale_correction_re_applies_it(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Things to avoid"})
        changed = graph_of("image_basic.api.json")
        changed["3"]["inputs"]["clip"] = ["1", 0]  # a different connection
        self.replace_graph(workflow_id, changed)
        self.assertTrue(
            self.service.export_corrections("default", workflow_id, SNAPSHOT)[0].stale
        )

        # Saving again against the current structure is the repair.
        self.save(
            workflow_id, NEGATIVE, {"label": "Things to avoid"}, expected_revision=1
        )
        self.assertFalse(
            self.service.export_corrections("default", workflow_id, SNAPSHOT)[0].stale
        )
        self.assertEqual(self.controls(workflow_id)[NEGATIVE].label, "Things to avoid")

    def test_a_correction_that_binds_to_nothing_is_reported_not_dropped(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Things to avoid"})
        renumbered = {
            ("30" if node_id == "3" else node_id): node
            for node_id, node in graph_of("image_basic.api.json").items()
        }
        renumbered["5"]["inputs"]["negative"] = ["30", 0]
        self.replace_graph(workflow_id, renumbered)
        schema = self.schema(workflow_id)
        self.assertIn("mapping_correction_stale", {w.code for w in schema.warnings})
        self.assertEqual(
            len(self.service.export_corrections("default", workflow_id, SNAPSHOT)), 1
        )


class TemplatesCarryPresentationOnly(CorrectionTestCase):
    """A compatible node template stores presentation and nothing else."""

    def test_a_value_cannot_be_saved_as_a_correction(self) -> None:
        for field in ("value", "text", "seed", "filename"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                Presentation(**{field: "smuggled"})

    def test_a_node_template_stores_no_workflow_and_no_value(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Avoid"}, scope="node_class")
        rows = self.repo.list_mapping_overrides("default", workflow_id)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["workflow_id"])
        self.assertEqual(json.loads(rows[0]["presentation_json"]), {"label": "Avoid"})

    def test_a_widget_choice_the_type_cannot_support_is_refused(self) -> None:
        workflow_id = self.import_graph()
        with self.assertRaises(CorrectionError) as caught:
            self.save(workflow_id, "1:ckpt_name", {"component": "slider"})
        self.assertEqual(caught.exception.status, 422)

    def test_a_display_range_narrows_but_never_widens(self) -> None:
        workflow_id = self.import_graph()
        declared = self.controls(workflow_id)["5:steps"].constraints
        self.save(
            workflow_id, "5:steps", {"display_min": 10.0, "display_max": 10_000.0}
        )
        narrowed = self.controls(workflow_id)["5:steps"].constraints
        self.assertEqual(narrowed.min, 10.0)
        self.assertEqual(narrowed.max, declared.max)  # the widened bound is ignored

    def test_a_display_default_and_step_are_saved_and_replayed(self) -> None:
        workflow_id = self.import_graph()
        self.save(
            workflow_id,
            "5:steps",
            {
                "display_min": 10.0,
                "display_max": 100.0,
                "display_default": 25.0,
                "display_step": 5.0,
            },
        )
        control = self.controls(workflow_id)["5:steps"]
        self.assertEqual(control.value, "25")
        self.assertEqual(control.constraints.step, 5.0)

    def test_a_display_default_outside_the_resolved_range_is_rejected(self) -> None:
        workflow_id = self.import_graph()
        with self.assertRaises(CorrectionError) as caught:
            self.save(
                workflow_id,
                "5:steps",
                {"display_min": 10.0, "display_max": 100.0, "display_default": 500.0},
            )
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(self.controls(workflow_id)["5:steps"].value, "20")  # unchanged

    def test_a_display_default_is_rejected_for_an_exact_integer(self) -> None:
        workflow_id = self.import_graph()
        with self.assertRaises(CorrectionError) as caught:
            self.save(workflow_id, "5:seed", {"display_default": 42.0})
        self.assertEqual(caught.exception.status, 422)

    def test_a_non_positive_display_step_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Presentation(display_step=0)


class TemplatesDoNotSpread(CorrectionTestCase):
    """Graph context is part of the template key."""

    def test_a_negative_label_does_not_reach_the_positive_encoder(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Avoid this"}, scope="node_class")
        controls = self.controls(workflow_id)
        self.assertEqual(controls[NEGATIVE].label, "Avoid this")
        self.assertNotEqual(controls[POSITIVE].label, "Avoid this")
        self.assertIn("override:node_class", controls[NEGATIVE].inference_reason)
        self.assertNotIn("override:", controls[POSITIVE].inference_reason)

    def test_a_template_reaches_the_same_context_in_another_workflow(self) -> None:
        first = self.import_graph()
        self.save(first, NEGATIVE, {"label": "Avoid this"}, scope="node_class")
        second = self.import_graph()  # the same graph imported again
        self.assertEqual(self.controls(second)[NEGATIVE].label, "Avoid this")
        self.assertNotEqual(self.controls(second)[POSITIVE].label, "Avoid this")

    def test_an_exact_workflow_override_wins_over_a_template(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "From template"}, scope="node_class")
        self.save(workflow_id, NEGATIVE, {"label": "From this workflow"})
        self.assertEqual(
            self.controls(workflow_id)[NEGATIVE].label, "From this workflow"
        )


class ProfileIsolation(CorrectionTestCase):
    """One profile's corrections are invisible to another."""

    def test_another_profile_never_sees_or_reads_a_correction(self) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Private label"}, scope="node_class")

        # Same graph, imported by the other profile: the template is theirs alone.
        theirs = self.import_graph(owner=self.other)
        self.assertNotEqual(
            self.controls(theirs, owner=self.other)[NEGATIVE].label, "Private label"
        )
        self.assertEqual(self.repo.list_mapping_overrides(self.other, theirs), [])
        self.assertEqual(
            self.service.export_corrections(self.other, theirs, SNAPSHOT), []
        )

    def test_another_profile_cannot_read_or_reset_a_workflow_it_does_not_own(
        self,
    ) -> None:
        workflow_id = self.import_graph()
        self.save(workflow_id, NEGATIVE, {"label": "Private label"})
        self.assertIsNone(
            self.service.control_schema(self.other, workflow_id, SNAPSHOT)
        )
        self.assertIsNone(
            self.service.export_corrections(self.other, workflow_id, SNAPSHOT)
        )
        self.assertEqual(self.service.reset_corrections(self.other, workflow_id), 0)
        # Untouched for the owner.
        self.assertEqual(self.controls(workflow_id)[NEGATIVE].label, "Private label")

    def test_another_profile_cannot_save_into_a_workflow_it_does_not_own(self) -> None:
        workflow_id = self.import_graph()
        with self.assertRaises(CorrectionError) as caught:
            self.save(workflow_id, NEGATIVE, {"label": "Injected"}, owner=self.other)
        self.assertEqual(caught.exception.status, 404)


class RevisionConflicts(CorrectionTestCase):
    """Concurrent saves conflict instead of overwriting one another."""

    def test_a_second_tab_saving_from_a_stale_revision_conflicts(self) -> None:
        workflow_id = self.import_graph()
        saved = self.save(workflow_id, NEGATIVE, {"label": "First"})
        self.assertEqual(saved.revision, 1)

        with self.assertRaises(CorrectionError) as caught:
            self.save(workflow_id, NEGATIVE, {"label": "Second"}, expected_revision=0)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.controls(workflow_id)[NEGATIVE].label, "First")

        # Reloading and saving from the current revision succeeds.
        self.assertEqual(
            self.save(
                workflow_id, NEGATIVE, {"label": "Second"}, expected_revision=1
            ).revision,
            2,
        )
        self.assertEqual(self.controls(workflow_id)[NEGATIVE].label, "Second")

    def test_the_repository_rejects_a_revision_from_the_future(self) -> None:
        with self.assertRaises(RevisionConflict):
            self.repo.save_mapping_override(
                "default",
                scope="node_class",
                workflow_id=None,
                selector="CLIPTextEncode:text:ctx",
                schema_signature="input:whatever",
                presentation={"label": "x"},
                expected_revision=7,
            )

    def test_saving_is_refused_while_the_catalog_is_unavailable(self) -> None:
        """Unvalidated controls must not become the key of a saved correction."""
        workflow_id = self.import_graph()
        offline = CatalogSnapshot(
            nodes={}, freshness=CatalogFreshness(state="unavailable")
        )
        with self.assertRaises(CorrectionError) as caught:
            self.service.save_correction(
                "default",
                workflow_id,
                offline,
                SaveCorrection(
                    binding_id=NEGATIVE, presentation=Presentation(label="x")
                ),
            )
        self.assertEqual(caught.exception.status, 409)

    def test_a_missing_binding_is_refused(self) -> None:
        workflow_id = self.import_graph()
        with self.assertRaises(CorrectionError) as caught:
            self.save(workflow_id, "999:nope", {"label": "x"})
        self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
