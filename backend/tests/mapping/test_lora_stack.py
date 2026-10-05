"""PACK-002: the node pack's SimpleUILoraStack payload as a dedicated control.

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
        "SimpleUILoraStack": {
            "input": {
                "required": {
                    "model": ["MODEL"],
                    "clip": ["CLIP"],
                    "loras": [
                        "STRING",
                        {"multiline": True, "default": '{"schema":1,"loras":[]}'},
                    ],
                }
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


def graph_with(loras: Any) -> dict[str, Any]:
    graph = copy.deepcopy(BASE_GRAPH)
    graph["20"] = {
        "class_type": "SimpleUILoraStack",
        "inputs": {"model": ["1", 0], "clip": ["1", 1], "loras": loras},
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
