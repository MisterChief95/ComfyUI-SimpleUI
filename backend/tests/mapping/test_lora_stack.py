"""PACK-002: the node pack's SimpleUILoraStack payload as a dedicated control.

Contract 2 exports omit ``loras`` (no canvas widget); a literal ``loras`` is
still read, and a linked one is left alone.

The pack's nodes are added to the synthetic catalog here (contract 1 shapes);
the COMPAT-001 fixture set itself stays read-only.
Run from backend/:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from app.catalog import normalize
from app.mapping import build_control_schema, build_submission_graph, parse_graph

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
OBJECT_INFO = json.loads(
    (FIXTURES / "catalog" / "object_info.synthetic.json").read_text(encoding="utf-8")
)
OBJECT_INFO.update(
    {
        "LoraLoader": {
            "input": {
                "required": {
                    "model": ["MODEL"],
                    "clip": ["CLIP"],
                    "lora_name": [
                        [
                            "placeholder-style.safetensors",
                            "styles\\placeholder-detail.safetensors",
                        ]
                    ],
                    "strength_model": ["FLOAT", {"default": 1.0}],
                    "strength_clip": ["FLOAT", {"default": 1.0}],
                }
            },
            "output": ["MODEL", "CLIP"],
            "output_node": False,
            "python_module": "nodes",
            "category": "loaders",
        },
        # Contract 2: `loras` is an optional socket with no canvas widget.
        "SimpleUILoraStack": {
            "input": {
                "required": {"model": ["MODEL"], "clip": ["CLIP"]},
                "optional": {"loras": ["STRING", {"forceInput": True}]},
            },
            "output": ["MODEL", "CLIP", "STRING"],
            "output_node": False,
            "python_module": "custom_nodes.comfyui-simpleui-nodes",
            "category": "SimpleUI",
        },
    }
)
NODES = normalize(OBJECT_INFO).nodes
BASE_GRAPH = json.loads(
    (FIXTURES / "graphs" / "image_basic.api.json").read_text(encoding="utf-8")
)


def payload(*entries: dict[str, Any], schema: Any = 1, **extra: Any) -> str:
    return json.dumps({"schema": schema, "loras": list(entries), **extra})


def entry(name: str, enabled: bool = True, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "strength_model": 1.0,
        "strength_clip": 0.5,
        "enabled": enabled,
        "trigger_words": "placeholder style",
        **extra,
    }


ABSENT = object()


def graph_with(loras: Any) -> dict[str, Any]:
    graph = copy.deepcopy(BASE_GRAPH)
    inputs: dict[str, Any] = {"model": ["1", 0], "clip": ["1", 1]}
    if loras is not ABSENT:
        inputs["loras"] = loras
    graph["20"] = {
        "class_type": "SimpleUILoraStack",
        "inputs": inputs,
        "_meta": {"title": "LoRA Stack"},
    }
    graph["5"]["inputs"]["model"] = ["20", 0]
    return parse_graph(json.dumps(graph).encode())


def build(loras: Any):
    graph = graph_with(loras)
    schema = build_control_schema(
        graph, NODES, owner_id="default", workflow_id="wf", revision=1
    )
    control = next(c for c in schema.controls if c.binding_id == "20:loras")
    return graph, schema, control


class LoraStackControlTest(unittest.TestCase):
    def test_a_readable_payload_becomes_a_lora_stack_control(self) -> None:
        _, schema, control = build(payload(entry("placeholder-style.safetensors")))
        self.assertEqual(control.component, "lora_stack")
        self.assertEqual(control.group, "model")
        self.assertEqual(control.logical_type, "string")
        # The catalog's LoRA names, catalog spelling, so the browser can flag misses.
        self.assertEqual(
            [o.value for o in control.options],
            [
                "placeholder-style.safetensors",
                "styles\\placeholder-detail.safetensors",
            ],
        )
        self.assertNotIn("lora_missing", {w.code for w in schema.warnings})

    def test_forward_slash_names_match_os_separator_catalog_names(self) -> None:
        _, schema, _ = build(payload(entry("styles/placeholder-detail.safetensors")))
        self.assertNotIn("lora_missing", {w.code for w in schema.warnings})

    def test_a_missing_lora_is_flagged_and_kept(self) -> None:
        text = payload(
            entry("placeholder-style.safetensors"),
            entry("gone.safetensors", enabled=False),
        )
        graph, schema, control = build(text)
        warning = next(w for w in schema.warnings if w.code == "lora_missing")
        self.assertIn("gone.safetensors", warning.message)
        self.assertEqual(control.value, text)
        self.assertEqual(control.unresolved, [])
        self.assertEqual(
            build_submission_graph(graph, schema)["20"]["inputs"]["loras"], text
        )

    def test_unreadable_payloads_stay_raw_and_editable(self) -> None:
        for text in ("{not json", payload(schema=2), '{"schema":1}', "[]"):
            with self.subTest(text=text):
                _, schema, control = build(text)
                self.assertEqual(control.component, "textarea")
                self.assertEqual(control.value, text)
                self.assertIn(
                    "lora_stack_unreadable", {w.code for w in schema.warnings}
                )

    def test_edits_are_submitted_verbatim_with_unknown_fields(self) -> None:
        graph, schema, _ = build(payload())
        edited = payload(
            entry("placeholder-style.safetensors", note="kept"), future_key=[1, 2]
        )
        submitted = build_submission_graph(graph, schema, {"20:loras": edited})
        self.assertEqual(submitted["20"]["inputs"]["loras"], edited)
        self.assertEqual(graph["20"]["inputs"]["loras"], payload())

    def test_other_string_inputs_are_untouched(self) -> None:
        _, schema, _ = build(payload())
        prompt = next(c for c in schema.controls if c.binding_id == "2:text")
        self.assertEqual(prompt.component, "textarea")

    def test_a_linked_payload_is_not_a_control(self) -> None:
        graph = graph_with(["2", 0])
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        self.assertNotIn("20:loras", {c.binding_id for c in schema.controls})


class AbsentPayloadTest(unittest.TestCase):
    """A contract-2 export: the node has no `loras` key at all."""

    def test_a_control_starts_from_the_empty_payload(self) -> None:
        _, schema, control = build(ABSENT)
        self.assertEqual(control.component, "lora_stack")
        self.assertEqual(control.value, '{"schema":1,"loras":[]}')
        self.assertEqual(control.group, "model")
        self.assertIn("injected", control.inference_reason)
        self.assertEqual(schema.blocking, [])

    def test_an_untouched_stack_is_left_out(self) -> None:
        graph, schema, _ = build(ABSENT)
        self.assertNotIn("loras", build_submission_graph(graph, schema)["20"]["inputs"])

    def test_an_edit_writes_the_payload_into_the_node(self) -> None:
        graph, schema, _ = build(ABSENT)
        edited = payload(entry("styles/placeholder-detail.safetensors"))
        submitted = build_submission_graph(graph, schema, {"20:loras": edited})
        self.assertEqual(submitted["20"]["inputs"]["loras"], edited)
        self.assertEqual(submitted["20"]["inputs"]["model"], ["1", 0])
        self.assertNotIn("loras", graph["20"]["inputs"])

    def test_an_empty_payload_edit_also_passes_through(self) -> None:
        graph, schema, _ = build(ABSENT)
        submitted = build_submission_graph(graph, schema, {"20:loras": payload()})
        self.assertEqual(submitted["20"]["inputs"]["loras"], payload())

    def test_an_unreachable_node_still_gets_its_control_as_inactive(self) -> None:
        graph = graph_with(ABSENT)
        graph["5"]["inputs"]["model"] = ["1", 0]
        schema = build_control_schema(
            graph, NODES, owner_id="default", workflow_id="wf", revision=1
        )
        control = next(c for c in schema.controls if c.binding_id == "20:loras")
        self.assertEqual(control.group, "inactive")


class DisplayOptionsTest(unittest.TestCase):
    """Designer properties: Show thumbnails and Show CLIP."""

    def test_presentation_sets_the_display_options(self) -> None:
        from app.mapping.corrections import Presentation, _apply

        _, _, control = build(ABSENT)
        self.assertTrue(control.show_thumbnails)
        self.assertTrue(control.show_clip)
        shown = _apply(
            control,
            Presentation(show_thumbnails=False, show_clip=False),
            "workflow",
            [],
        )
        self.assertFalse(shown.show_thumbnails)
        self.assertFalse(shown.show_clip)

    def test_presentation_sets_loras_per_row(self) -> None:
        from pydantic import ValidationError

        from app.mapping.corrections import Presentation, _apply

        _, _, control = build(ABSENT)
        self.assertEqual(control.lora_columns, 1)
        shown = _apply(control, Presentation(lora_columns=3), "workflow", [])
        self.assertEqual(shown.lora_columns, 3)
        for bad in (0, 4):
            with self.assertRaises(ValidationError):
                Presentation(lora_columns=bad)

    def test_hidden_clip_follows_model_strength_at_submission(self) -> None:
        graph, schema, control = build(
            payload(entry("placeholder-style.safetensors", note="kept"))
        )
        hidden = schema.model_copy(
            update={
                "controls": [
                    c.model_copy(update={"show_clip": False})
                    if c.binding_id == control.binding_id
                    else c
                    for c in schema.controls
                ]
            }
        )
        # Untouched literal and an edit are both synced.
        for edits in ({}, {"20:loras": payload(entry("x.safetensors"))}):
            with self.subTest(edits=edits):
                sent = json.loads(
                    build_submission_graph(graph, hidden, edits)["20"]["inputs"][
                        "loras"
                    ]
                )
                for item in sent["loras"]:
                    self.assertEqual(item["strength_clip"], item["strength_model"])
        sent = json.loads(
            build_submission_graph(graph, hidden)["20"]["inputs"]["loras"]
        )
        self.assertEqual(sent["loras"][0]["note"], "kept")
        # Shown CLIP: submitted verbatim.
        self.assertEqual(
            build_submission_graph(graph, schema)["20"]["inputs"]["loras"],
            control.value,
        )

    def test_hidden_clip_leaves_an_absent_payload_absent(self) -> None:
        graph, schema, _ = build(ABSENT)
        hidden = schema.model_copy(
            update={
                "controls": [
                    c.model_copy(update={"show_clip": False}) for c in schema.controls
                ]
            }
        )
        self.assertNotIn("loras", build_submission_graph(graph, hidden)["20"]["inputs"])
