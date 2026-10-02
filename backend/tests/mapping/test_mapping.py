"""Mapping checks, one class per MAPPING-001 acceptance criterion.

Run from backend/:  python -m unittest discover -s tests -v

Every graph and expectation comes from the COMPAT-001 fixture set in
tests/fixtures/, which is read-only here. The catalog is the synthetic
object_info fixture pushed through the real app/catalog normalizer, so these
tests exercise the same sanitized projection a profile would actually receive --
in particular they cannot see the withheld shared input filenames.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.catalog import normalize
from app.catalog.contracts import CatalogFreshness, CatalogSnapshot
from app.mapping import (
    GraphImportError,
    build_control_schema,
    build_submission_graph,
    parse_graph,
    resolve_seed,
)
from app.contracts import EnumOption
from app.mapping.submission import SubmissionError
from app.storage import Database
from app.storage.repository import Repository
from app.workflows import WorkflowService

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
GRAPHS = FIXTURES / "graphs"
CATALOG = normalize(
    json.loads((FIXTURES / "catalog" / "object_info.synthetic.json").read_text(encoding="utf-8"))
)
NODES = CATALOG.nodes
REJECTIONS = {
    case["fixture"]: case
    for case in json.loads(
        (FIXTURES / "expectations" / "rejections.json").read_text(encoding="utf-8")
    )["cases"]
}
TRANSPORT = json.loads(
    (FIXTURES / "expectations" / "seed_and_transport.json").read_text(encoding="utf-8")
)
#: Shared ComfyUI input-directory filenames the client must never receive.
WITHHELD = json.loads(
    (FIXTURES / "catalog" / "sanitized_projection.expected.json").read_text(encoding="utf-8")
)["must_not_appear_in_projection"]


def raw_bytes(name: str) -> bytes:
    return (GRAPHS / name).read_bytes()


def schema_for(name: str, **kwargs: Any):
    graph = parse_graph(raw_bytes(name))
    return graph, build_control_schema(
        graph, NODES, owner_id="default", workflow_id="wf", revision=1, **kwargs
    )


def codes(details) -> set[str]:
    return {detail.code for detail in details}


def by_binding(schema) -> dict[str, Any]:
    return {control.binding_id: control for control in schema.controls}


class ImportValidation(unittest.TestCase):
    """Refusals happen at parse time, with an actionable reason."""

    def test_every_accepted_fixture_imports(self) -> None:
        for path in sorted(GRAPHS.glob("*.api.json")):
            with self.subTest(graph=path.name):
                self.assertTrue(parse_graph(path.read_bytes()))

    def test_non_finite_numbers_are_refused_not_clamped(self) -> None:
        with self.assertRaises(GraphImportError) as caught:
            parse_graph((GRAPHS / "rejected" / "non_finite_values.api.json").read_bytes())
        self.assertEqual(caught.exception.detail.code, "non_finite_number")

    def test_duplicate_object_keys_are_refused(self) -> None:
        with self.assertRaises(GraphImportError) as caught:
            parse_graph((GRAPHS / "rejected" / "duplicate_keys.api.json").read_bytes())
        self.assertEqual(caught.exception.detail.code, "duplicate_key")

    def test_ui_export_gets_an_actionable_message(self) -> None:
        with self.assertRaises(GraphImportError) as caught:
            parse_graph((GRAPHS / "rejected" / "ui_export_not_api.json").read_bytes())
        self.assertEqual(caught.exception.detail.code, "ui_export_not_api")
        self.assertIn("Export (API)", caught.exception.detail.message)

    def test_broken_links_block_submission(self) -> None:
        _, schema = schema_for("rejected/broken_links.api.json")
        self.assertEqual(codes(schema.blocking), {"broken_link"})
        # Both the missing target and the out-of-range output index are reported.
        self.assertEqual(len(schema.blocking), 2)
        fields = {detail.field for detail in schema.blocking}
        self.assertEqual(fields, {"2.inputs.clip", "3.inputs.clip"})

    def test_graph_without_output_node_is_blocked(self) -> None:
        _, schema = schema_for("rejected/no_output_node.api.json")
        self.assertIn("no_output_node", codes(schema.blocking))

    def test_envelope_with_prompt_is_unwrapped(self) -> None:
        inner = json.loads(raw_bytes("image_basic.api.json"))
        graph = parse_graph(json.dumps({"prompt": inner, "client_id": "abc"}))
        self.assertEqual(graph, inner)

    def test_oversized_payload_is_refused(self) -> None:
        with self.assertRaises(GraphImportError) as caught:
            parse_graph(b"{}" + b" " * (9 * 1024 * 1024))
        self.assertEqual(caught.exception.detail.code, "graph_too_large")


class RoundTrip(unittest.TestCase):
    """Untouched graphs survive import, storage and submission unchanged."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "test.sqlite3")
        self.db.migrate()
        self.addCleanup(self.db.close)
        self.service = WorkflowService(Repository(self.db))
        self.snapshot = CatalogSnapshot(
            freshness=CatalogFreshness(state="fresh"), nodes=NODES, capabilities={}
        )

    def test_stored_graph_is_byte_identical_to_the_import(self) -> None:
        for name in ("image_basic.api.json", "video_basic.api.json", "force_input_and_flexible.api.json"):
            with self.subTest(graph=name):
                original = json.loads(raw_bytes(name))
                workflow_id, graph = self.service.import_workflow("default", name, raw_bytes(name))
                self.assertEqual(graph, original)
                stored = self.service._repository.get_workflow_graph("default", workflow_id, 1)
                self.assertEqual(stored, original)

    def test_submission_copy_matches_the_graph_when_nothing_is_edited(self) -> None:
        graph, schema = schema_for("image_basic.api.json")
        self.assertEqual(build_submission_graph(graph, schema), graph)

    def test_omitted_optional_input_stays_omitted(self) -> None:
        graph, schema = schema_for("video_basic.api.json")
        self.assertNotIn("audio", graph["7"]["inputs"])
        submission = build_submission_graph(graph, schema)
        self.assertNotIn("audio", submission["7"]["inputs"])
        self.assertNotIn("7:audio", by_binding(schema))

    def test_editing_one_value_leaves_every_other_node_untouched(self) -> None:
        graph, schema = schema_for("image_basic.api.json")
        submission = build_submission_graph(graph, schema, {"2:text": "edited placeholder"})
        self.assertEqual(submission["2"]["inputs"]["text"], "edited placeholder")
        self.assertEqual(graph["2"]["inputs"]["text"], "placeholder positive prompt, sample subject")
        for node_id in graph:
            if node_id != "2":
                self.assertEqual(submission[node_id], graph[node_id])

    def test_workflow_is_owner_scoped(self) -> None:
        workflow_id, _ = self.service.import_workflow(
            "default", "basic", raw_bytes("image_basic.api.json")
        )
        self.assertIsNotNone(self.service.control_schema("default", workflow_id, self.snapshot))
        self.assertIsNone(self.service.control_schema("someone-else", workflow_id, self.snapshot))

    def test_catalog_unavailable_stores_but_refuses_submission(self) -> None:
        workflow_id, _ = self.service.import_workflow(
            "default", "basic", raw_bytes("image_basic.api.json")
        )
        offline = CatalogSnapshot(
            freshness=CatalogFreshness(state="unavailable", submission_allowed=False), nodes={}
        )
        schema = self.service.control_schema("default", workflow_id, offline)
        self.assertIn("catalog_unavailable", codes(schema.blocking))


class LinksAndLiterals(unittest.TestCase):
    """A two-element array is not always a link."""

    def test_links_do_not_become_form_controls(self) -> None:
        _, schema = schema_for("image_basic.api.json")
        bindings = by_binding(schema)
        for binding in ("5:model", "5:positive", "5:negative", "5:latent_image", "6:samples"):
            self.assertNotIn(binding, bindings)
        self.assertIn("5:seed", bindings)

    def test_literal_arrays_are_preserved_not_rewired(self) -> None:
        graph, schema = schema_for("force_input_and_flexible.api.json")
        bindings = by_binding(schema)
        # Three strings on a STRING input, and a two-element array whose first
        # element is not a node id: both stay literals.
        self.assertEqual(bindings["11:text"].value, '["not","a","link"]')
        self.assertEqual(bindings["12:literal_pair"].value, '["alpha",0]')
        self.assertEqual(build_submission_graph(graph, schema)["11"]["inputs"]["text"],
                         ["not", "a", "link"])
        self.assertIn("literal_shape_unexpected", codes(schema.warnings))

    def test_missing_class_is_blocking_but_the_rest_still_renders(self) -> None:
        _, schema = schema_for("missing_class.api.json")
        blocking = [d for d in schema.blocking if d.code == "class_missing"]
        self.assertEqual(len(blocking), 1)
        self.assertIn("SimpleUIAbsentCustomNode", blocking[0].message)
        bindings = by_binding(schema)
        # Its own literals are preserved read-only; its link is still a link.
        self.assertEqual(bindings["4:strength"].value, 1.25)
        self.assertEqual(bindings["4:mode"].value, "balanced")
        self.assertNotIn("4:model", bindings)
        # Every other control is unaffected.
        self.assertIn("6:seed", bindings)
        self.assertIn("2:text", bindings)

    def test_repeated_stages_are_not_merged(self) -> None:
        _, schema = schema_for("image_repeated_stages.api.json")
        bindings = by_binding(schema)
        self.assertEqual(bindings["3:text"].value, bindings["5:text"].value)
        self.assertNotEqual(bindings["3:text"].binding_id, bindings["5:text"].binding_id)
        self.assertEqual(bindings["3:text"].label, "Base Positive")
        self.assertEqual(bindings["5:text"].label, "Refiner Positive")
        self.assertEqual(bindings["8:seed"].value, "111")
        self.assertEqual(bindings["9:seed"].value, "222")

    def test_positive_and_negative_are_traced_not_guessed(self) -> None:
        _, schema = schema_for("image_repeated_stages.api.json")
        bindings = by_binding(schema)
        for node_id in ("3", "5"):
            self.assertIn("conditioning_traced:positive", bindings[f"{node_id}:text"].inference_reason)
        for node_id in ("4", "6"):
            self.assertIn("conditioning_traced:negative", bindings[f"{node_id}:text"].inference_reason)

    def test_ambiguous_conditioning_is_labelled_neutrally(self) -> None:
        _, schema = schema_for("force_input_and_flexible.api.json")
        bindings = by_binding(schema)
        # Node 3 feeds a wildcard switch, not a sampler input directly.
        self.assertIn("conditioning_traced:ambiguous", bindings["3:text"].inference_reason)
        self.assertEqual(bindings["3:text"].label, "Prompt")

    def test_every_control_carries_binding_provenance(self) -> None:
        for path in sorted(GRAPHS.glob("*.api.json")):
            _, schema = schema_for(path.name)
            for control in schema.controls:
                with self.subTest(graph=path.name, binding=control.binding_id):
                    self.assertTrue(control.binding_id)
                    self.assertTrue(control.node_id)
                    self.assertTrue(control.class_type)
                    self.assertTrue(control.inference_reason)


class BranchesAndDiagnostics(unittest.TestCase):
    """Nothing is pruned, and the three diagnostic channels stay distinct."""

    def test_every_output_branch_is_shown_with_its_role(self) -> None:
        _, schema = schema_for("multi_output_branches.api.json")
        branches = {
            c.node_id: c.raw_metadata["output_role"]
            for c in schema.controls
            if c.inference_reason.startswith("output_branch")
        }
        self.assertEqual(
            branches, {"7": "image", "8": "image_ephemeral", "10": "video", "11": "video"}
        )

    def test_inactive_controls_are_grouped_not_pruned(self) -> None:
        graph, schema = schema_for("inactive_controls.api.json")
        bindings = by_binding(schema)
        for binding in ("90:text", "91:width", "91:height", "91:batch_size"):
            self.assertEqual(bindings[binding].group, "inactive")
        self.assertEqual(bindings["2:text"].group, "prompts")
        self.assertIn("inactive_controls", codes(schema.warnings))
        # Still submittable, and the nodes remain in the submitted graph.
        self.assertEqual(schema.blocking, [])
        self.assertIn("90", build_submission_graph(graph, schema))

    def test_presentation_warnings_do_not_block(self) -> None:
        graph, schema = schema_for("force_input_and_flexible.api.json")
        self.assertEqual(schema.blocking, [])
        self.assertIn("adapter_required", codes(schema.warnings))
        self.assertIn("flexible_input", codes(schema.warnings))
        self.assertEqual(build_submission_graph(graph, schema), graph)

    def test_missing_choice_is_an_editable_mapping_issue(self) -> None:
        graph = parse_graph(raw_bytes("image_basic.api.json"))
        graph["1"]["inputs"]["ckpt_name"] = "placeholder-uninstalled.safetensors"
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        control = by_binding(schema)["1:ckpt_name"]
        self.assertEqual(codes(control.unresolved), {"choice_missing"})
        # The imported value is shown, marked unavailable, and not replaced.
        self.assertEqual(control.value, "placeholder-uninstalled.safetensors")
        missing = [o for o in control.options if not o.available]
        self.assertEqual([o.value for o in missing], ["placeholder-uninstalled.safetensors"])
        self.assertEqual(schema.blocking, [])
        with self.assertRaises(SubmissionError) as caught:
            build_submission_graph(graph, schema)
        self.assertEqual(caught.exception.detail.code, "mapping_unresolved")

    def test_required_input_absent_blocks(self) -> None:
        graph = parse_graph(raw_bytes("image_basic.api.json"))
        del graph["4"]["inputs"]["width"]
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        self.assertIn("required_input_missing", codes(schema.blocking))

    def test_rejection_expectations_agree_with_the_fixture_file(self) -> None:
        """The fixture file is the contract; confirm the outcomes line up."""
        for fixture, case in REJECTIONS.items():
            name = fixture.split("graphs/", 1)[1]
            with self.subTest(fixture=fixture):
                if case["outcome"] == "warning":
                    _, schema = schema_for(name)
                    self.assertEqual(schema.blocking, [])
                    continue
                try:
                    _, schema = schema_for(name)
                except GraphImportError:
                    continue  # refused at parse time, which is blocking enough
                self.assertTrue(schema.blocking, f"{fixture} must block")


class LiteralsAndFlexibleInputs(unittest.TestCase):
    """forceInput literals, flexible inputs and opaque values are preserved."""

    def test_force_input_literal_stays_editable(self) -> None:
        _, schema = schema_for("force_input_and_flexible.api.json")
        control = by_binding(schema)["2:steps"]
        self.assertEqual(control.value, "28")
        self.assertIn(control.component, ("number", "slider"))
        self.assertEqual(control.unresolved, [])
        self.assertTrue(control.raw_metadata["force_input_hint"])

    def test_dynamically_named_inputs_are_not_rejected_by_name(self) -> None:
        graph, schema = schema_for("force_input_and_flexible.api.json")
        self.assertEqual(schema.blocking, [])
        # input_2 and input_3 resolve to nodes, so they stay links, not controls.
        bindings = by_binding(schema)
        self.assertNotIn("5:input_2", bindings)
        self.assertNotIn("5:input_3", bindings)
        self.assertEqual(build_submission_graph(graph, schema)["5"]["inputs"]["input_2"], ["4", 0])

    def test_opaque_custom_object_is_preserved_and_not_editable(self) -> None:
        graph, schema = schema_for("force_input_and_flexible.api.json")
        control = by_binding(schema)["6:config"]
        self.assertEqual(control.component, "readonly")
        self.assertEqual(
            build_submission_graph(graph, schema)["6"]["inputs"]["config"],
            graph["6"]["inputs"]["config"],
        )
        with self.assertRaises(SubmissionError) as caught:
            build_submission_graph(graph, schema, {"6:config": "anything"})
        self.assertEqual(caught.exception.detail.code, "control_not_editable")

    def test_finite_values_are_not_clamped_or_rounded(self) -> None:
        graph, schema = schema_for("image_basic.api.json")
        submission = build_submission_graph(graph, schema, {"5:cfg": 7.25})
        self.assertEqual(submission["5"]["inputs"]["cfg"], 7.25)
        # Out of range is reported, never silently corrected.
        with self.assertRaises(SubmissionError) as caught:
            build_submission_graph(graph, schema, {"5:cfg": 1000.0})
        self.assertEqual(caught.exception.detail.code, "value_out_of_range")

    def test_enum_without_options_rejects_edits(self) -> None:
        graph, schema = schema_for("image_basic.api.json")
        enum = next(c for c in schema.controls if c.logical_type == "enum" and c.options)
        with self.assertRaises(SubmissionError):
            build_submission_graph(graph, schema, {enum.binding_id: "not-a-choice"})
        enum.options = []
        with self.assertRaises(SubmissionError):
            build_submission_graph(graph, schema, {enum.binding_id: "typed.model"})
        build_submission_graph(graph, schema)  # unedited value still passes through

    def test_typed_combo_options_are_submitted_as_listed(self) -> None:
        graph, schema = schema_for("image_basic.api.json")
        enum = next(c for c in schema.controls if c.logical_type == "enum" and c.options)
        enum.options = [EnumOption(value=v, label=str(v), available=True) for v in (4, 8.5, "x", True)]
        node, name = enum.binding_id.split(":", 1)
        for sent, expected in ((8.5, 8.5), (4.0, 4), ("x", "x"), (True, True)):
            got = build_submission_graph(graph, schema, {enum.binding_id: sent})[node]["inputs"][name]
            self.assertEqual((got, type(got)), (expected, type(expected)))
        for bad in ("4", 1, False, "8.5"):  # text is not the number; True is not 1
            with self.assertRaises(SubmissionError):
                build_submission_graph(graph, schema, {enum.binding_id: bad})

    def test_non_finite_submitted_value_is_refused(self) -> None:
        graph, schema = schema_for("image_basic.api.json")
        with self.assertRaises(SubmissionError) as caught:
            build_submission_graph(graph, schema, {"5:cfg": float("nan")})
        self.assertEqual(caught.exception.detail.code, "non_finite_number")

    def test_an_ordinary_prompt_does_not_warn(self) -> None:
        """Declaring dynamicPrompts is metadata, not a macro in the literal.

        Every core CLIPTextEncode declares it, so warning on the declaration
        would put a warning on every prompt in every graph.
        """
        _, schema = schema_for("image_basic.api.json")
        self.assertNotIn("macro_not_evaluated", codes(schema.warnings))
        self.assertTrue(by_binding(schema)["2:text"].raw_metadata["dynamic_prompts_hint"])

    def test_dynamic_prompt_wildcards_are_flagged_and_kept(self) -> None:
        graph = parse_graph(raw_bytes("image_basic.api.json"))
        graph["2"]["inputs"]["text"] = "a {red|blue} placeholder"
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        self.assertIn("macro_not_evaluated", codes(schema.warnings))
        self.assertEqual(schema.blocking, [])
        self.assertEqual(
            build_submission_graph(graph, schema)["2"]["inputs"]["text"], "a {red|blue} placeholder"
        )

    def test_prompt_macros_are_preserved_literally(self) -> None:
        graph = parse_graph(raw_bytes("image_basic.api.json"))
        graph["7"]["inputs"]["filename_prefix"] = "SimpleUI/%date:yyyy-MM-dd%"
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        self.assertIn("macro_not_evaluated", codes(schema.warnings))
        self.assertEqual(
            build_submission_graph(graph, schema)["7"]["inputs"]["filename_prefix"],
            "SimpleUI/%date:yyyy-MM-dd%",
        )


class SeedsAndTransport(unittest.TestCase):
    """Exact seeds, fixed-by-default policy, and batch size versus requests."""

    def test_large_seeds_survive_exactly(self) -> None:
        graph, schema = schema_for("image_large_seed.api.json")
        bindings = by_binding(schema)
        for case in TRANSPORT["exact_integer_transport"]["cases"]:
            with self.subTest(node=case["node"]):
                control = bindings[f"{case['node']}:seed"]
                self.assertEqual(control.value, case["descriptor_value"])
                self.assertEqual(graph[case["node"]]["inputs"]["seed"], case["graph_value"])
                submission = build_submission_graph(
                    graph, schema, {control.binding_id: case["descriptor_value"]}
                )
                self.assertEqual(
                    submission[case["node"]]["inputs"]["seed"], case["submitted_value"]
                )

    def test_unsafe_integers_never_get_a_slider(self) -> None:
        _, schema = schema_for("image_large_seed.api.json")
        for control in schema.controls:
            if control.constraints and control.constraints.exact_max:
                with self.subTest(binding=control.binding_id):
                    self.assertNotEqual(control.component, "slider")

    def test_seed_controls_use_the_seed_component(self) -> None:
        _, schema = schema_for("image_basic.api.json")
        self.assertEqual(by_binding(schema)["5:seed"].component, "seed")

    def test_fixed_is_the_default_and_never_moves(self) -> None:
        for _ in range(3):
            self.assertEqual(resolve_seed(123456789, "fixed"), 123456789)

    def test_increment_advances_once_per_call_and_reports_overflow(self) -> None:
        self.assertEqual(resolve_seed(41, "increment"), 42)
        with self.assertRaises(SubmissionError) as caught:
            resolve_seed(2**64 - 1, "increment")
        self.assertEqual(caught.exception.detail.code, "seed_overflow")

    def test_random_stays_within_declared_bounds(self) -> None:
        rng = random.Random(7)
        for _ in range(50):
            value = resolve_seed(0, "random", minimum=10, maximum=20, rng=rng)
            self.assertGreaterEqual(value, 10)
            self.assertLessEqual(value, 20)

    def test_a_retry_reuses_the_resolved_seed(self) -> None:
        """A retry re-reads the persisted seed; it never calls resolve again."""
        graph, schema = schema_for("image_basic.api.json")
        resolved = resolve_seed(123456789, "random", maximum=2**64 - 1, rng=random.Random(1))
        first = build_submission_graph(graph, schema, {"5:seed": str(resolved)})
        retry = build_submission_graph(graph, schema, {"5:seed": str(resolved)})
        self.assertEqual(first["5"]["inputs"]["seed"], retry["5"]["inputs"]["seed"])
        self.assertEqual(first["5"]["inputs"]["seed"], resolved)

    def test_latent_batch_size_is_a_node_input_not_a_request_count(self) -> None:
        expected = TRANSPORT["batch_versus_request_count"]
        graph, schema = schema_for("latent_batch_size.api.json")
        control = by_binding(schema)[f"{expected['node']}:{expected['input']}"]
        self.assertEqual(control.value, str(expected["value"]))
        self.assertEqual(control.group, "generation")
        submission = build_submission_graph(graph, schema)
        self.assertEqual(submission[expected["node"]]["inputs"][expected["input"]], expected["value"])
        self.assertEqual(submission, graph)  # one graph, submitted once


class CatalogSanitization(unittest.TestCase):
    """Private filenames never reach a control."""

    def test_owned_input_never_becomes_a_generic_dropdown(self) -> None:
        _, schema = schema_for("image_loader_input.api.json")
        bindings = by_binding(schema)
        for binding in ("2:image", "3:image"):
            control = bindings[binding]
            self.assertEqual(control.logical_type, "file")
            self.assertEqual(control.component, "file")
            # The shared input-directory listing is withheld by the catalog and
            # is never re-offered here as a dropdown.
            self.assertIsNone(control.options)
            # Both loaders are disconnected in this fixture, so they are shown
            # as Inactive rather than pruned.
            self.assertEqual(control.group, "inactive")
            # The imported filename is preserved for the upload adapter to resolve.
            self.assertIsInstance(control.value, str)

    def test_a_connected_owned_input_is_grouped_under_inputs(self) -> None:
        graph = parse_graph(raw_bytes("image_loader_input.api.json"))
        graph["9"]["inputs"]["images"] = ["2", 0]  # reach the loader from the output
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        control = by_binding(schema)["2:image"]
        self.assertEqual(control.group, "inputs")
        self.assertIsNone(control.options)

    def test_no_withheld_filename_appears_in_any_schema(self) -> None:
        for path in sorted(GRAPHS.glob("*.api.json")):
            _, schema = schema_for(path.name)
            serialized = schema.model_dump_json()
            for name in WITHHELD:
                with self.subTest(graph=path.name, filename=name):
                    if name in json.dumps(json.loads(raw_bytes(path.name))):
                        continue  # the user's own imported literal, not the catalog
                    self.assertNotIn(name, serialized)

    def test_model_choices_come_from_the_sanitized_catalog(self) -> None:
        _, schema = schema_for("image_basic.api.json")
        control = by_binding(schema)["1:ckpt_name"]
        self.assertEqual(control.group, "model")
        self.assertEqual(
            [option.value for option in control.options],
            NODES["CheckpointLoaderSimple"]["inputs"]["ckpt_name"]["choices"],
        )


if __name__ == "__main__":
    unittest.main()
