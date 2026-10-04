"""PROMPT-002: prompt styles apply to traced prompt controls and stay per profile."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.styles import apply_styles
from tests.auth.support import AuthTestCase


def control(binding_id: str, side: str | None, value: str = "") -> SimpleNamespace:
    reason = f"conditioning_traced:{side}" if side else "default_group"
    return SimpleNamespace(
        binding_id=binding_id,
        group="prompts",
        logical_type="string",
        inference_reason=reason,
        value=value,
    )


def style(positive: str = "", negative: str = "") -> dict:
    return {"positive": positive, "negative": negative}


class ApplyStylesTest(unittest.TestCase):
    def test_placeholder_wraps_and_plain_snippets_append(self) -> None:
        controls = [control("p", "positive", "cat"), control("n", "negative", "blurry")]
        edits: dict = {}
        apply_styles([style("masterpiece, {prompt}, 4k", "lowres")], controls, edits)
        self.assertEqual(edits, {"p": "masterpiece, cat, 4k", "n": "blurry, lowres"})

    def test_without_placeholder_positive_is_appended_and_empty_text_is_replaced(
        self,
    ) -> None:
        controls = [control("p", "positive", "cat"), control("q", "positive", "")]
        edits = {"p": "dog"}
        apply_styles([style("sharp")], controls, edits)
        self.assertEqual(edits, {"p": "dog, sharp", "q": "sharp"})

    def test_styles_stack_in_order_and_ambiguous_controls_are_untouched(self) -> None:
        controls = [control("p", "positive", "a"), control("x", None, "keep")]
        edits: dict = {}
        apply_styles([style("[{prompt}]"), style("z")], controls, edits)
        self.assertEqual(edits, {"p": "[a], z"})

    def test_no_styles_changes_nothing(self) -> None:
        edits: dict = {}
        apply_styles([], [control("p", "positive", "a")], edits)
        self.assertEqual(edits, {})


class StyleRoutesTest(AuthTestCase):
    def test_crud_revision_conflict_and_profile_privacy(self) -> None:
        client = self.local_client()
        created = self.post(
            client,
            "/api/styles",
            json={"name": "Cinematic", "positive": "{prompt}, film"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        style_id = created.json()["id"]
        self.assertEqual(
            [s["name"] for s in client.get("/api/styles").json()], ["Cinematic"]
        )

        updated = self.put(
            client,
            f"/api/styles/{style_id}",
            json={"negative": "lowres", "expected_revision": 1},
        )
        self.assertEqual(updated.json()["revision"], 2)
        stale = self.put(
            client,
            f"/api/styles/{style_id}",
            json={"name": "x", "expected_revision": 1},
        )
        self.assertEqual(stale.status_code, 409)

        store = self.app.state.styles
        self.assertIsNone(store.get_many("someone-else", [style_id]))
        self.assertEqual(
            self.delete(client, f"/api/styles/{style_id}").status_code, 204
        )
        self.assertEqual(client.get("/api/styles").json(), [])


if __name__ == "__main__":
    unittest.main()
