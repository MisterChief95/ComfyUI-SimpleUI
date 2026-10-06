"""PACK-003: Chain Input/Output node matching (pure functions, no ComfyUI)."""

from __future__ import annotations

import unittest

from app.chains.nodes import ChainNodeError, Reference, named_outputs, plan_stage

CATALOG = {"SaveImage": {"output_node": True}, "PreviewAny": {"output_node": True}}


def producer(name="a", save=True):
    graph = {
        "1": {
            "class_type": "SimpleUIChainOutput",
            "inputs": {"image": ["0", 0], "name": name},
        },
    }
    if save:
        graph["9"] = {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}}
    return graph


def consumer(name="a", extra=None):
    node = {
        "class_type": "SimpleUIChainInputImage",
        "inputs": {"image": "x.png", "name": name},
    }
    return {"5": node, **(extra or {})}


class ChainNodesTest(unittest.TestCase):
    def test_name_match_resolves_to_the_downstream_save_node(self) -> None:
        refs = plan_stage(1, [producer(), consumer()], CATALOG, set())
        self.assertEqual(refs, [Reference("5:image", "image", 0, "9")])

    def test_nearest_earlier_stage_wins(self) -> None:
        refs = plan_stage(2, [producer(), producer(), consumer()], CATALOG, set())
        self.assertEqual(refs[0].stage, 1)

    def test_explicit_binding_wins_and_unnamed_inputs_are_left_alone(self) -> None:
        self.assertEqual(
            plan_stage(1, [producer(), consumer()], CATALOG, {"5:image"}), []
        )
        self.assertEqual(plan_stage(1, [producer(), consumer("")], CATALOG, set()), [])

    def test_missing_name_is_an_error_not_a_guess(self) -> None:
        with self.assertRaisesRegex(ChainNodeError, "no earlier Chain Output"):
            plan_stage(1, [producer("a"), consumer("b")], CATALOG, set())

    def test_names_are_exact_and_case_sensitive(self) -> None:
        with self.assertRaises(ChainNodeError):
            plan_stage(1, [producer("Out"), consumer("out")], CATALOG, set())
        self.assertEqual(
            len(plan_stage(1, [producer("out"), consumer(" out ")], CATALOG, set())), 1
        )

    def test_duplicate_output_names_fail(self) -> None:
        graph = producer()
        graph["2"] = dict(graph["1"])
        with self.assertRaisesRegex(ChainNodeError, "more than once"):
            named_outputs(graph)

    def test_chain_output_without_a_save_node_is_a_validation_error(self) -> None:
        with self.assertRaisesRegex(ChainNodeError, "no downstream save"):
            plan_stage(1, [producer(save=False), consumer()], CATALOG, set())

    def test_kind_mismatch_is_an_error(self) -> None:
        text_in = {
            "5": {
                "class_type": "SimpleUIChainInputText",
                "inputs": {"text": "", "name": "a"},
            }
        }
        with self.assertRaisesRegex(ChainNodeError, "is text but"):
            plan_stage(1, [producer(), text_in], CATALOG, set())
