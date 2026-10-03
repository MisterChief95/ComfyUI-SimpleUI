"""mapping/input_adapters.py: binding a loader-typed control to a private upload.

The happy path is driven through the real controls.py pipeline against the
COMPAT-001 fixture set (an actual captured object_info + graph), matching the
"real or captured supported image/video loader contracts validate" acceptance
criterion. The diagnostic paths use hand-built ControlDescriptors so each
failure mode (wrong owner, wrong kind, unsupported adapter, missing input
folder) is isolated and unambiguous.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.catalog import normalize
from app.contracts import ControlDescriptor
from app.mapping import build_control_schema
from app.mapping.input_adapters import InputAdapterError, bind_upload

from .support import VIDEO_BYTES, UploadTestCase, png_bytes

OWNER = "default"
OTHER = "other-profile"

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
CATALOG = normalize(
    json.loads(
        (FIXTURES / "catalog" / "object_info.synthetic.json").read_text(
            encoding="utf-8"
        )
    )
)
GRAPH = json.loads(
    (FIXTURES / "graphs" / "image_loader_input.api.json").read_text(encoding="utf-8")
)


def _image_control() -> ControlDescriptor:
    schema = build_control_schema(
        GRAPH, CATALOG.nodes, owner_id=OWNER, workflow_id="wf1", revision=1
    )
    controls = {c.binding_id: c for c in schema.controls}
    # Node "2" is LoadImage's `image` input (tests/fixtures/graphs/image_loader_input.api.json).
    return controls["2:image"]


def _hand_built(**overrides) -> ControlDescriptor:
    base = dict(
        binding_id="9:file_input",
        node_id="9",
        class_type="SomeLoader",
        input_name="file_input",
        logical_type="file",
        value=None,
        component="file",
        group="inputs",
        order=0,
        label="Some loader: File input",
        inference_reason="loader_adapter:comfy_input_dir_filename;owned_input",
        raw_metadata={"media_kind": "image"},
    )
    base.update(overrides)
    return ControlDescriptor(**base)


class RealFixtureBindingTest(UploadTestCase):
    def test_supported_image_loader_control_binds_to_an_upload(self) -> None:
        control = _image_control()
        self.assertEqual(control.logical_type, "file")
        self.assertEqual(control.component, "file")

        row = self.store(OWNER, "image", "ref.png", png_bytes())
        staged = bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertTrue(staged.startswith(f"simpleui/{OWNER}/"))
        self.assertTrue((self.comfy_input / staged).is_file())


class DiagnosticsTest(UploadTestCase):
    def test_non_file_control_is_rejected_outright(self) -> None:
        control = _hand_built(logical_type="string", component="text")
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, "whatever")
        self.assertEqual(ctx.exception.detail.code, "not_a_loader_control")

    def test_unavailable_video_adapter_is_an_actionable_diagnostic(self) -> None:
        # This is normalize.py's actual shape for a video/audio OWNED_INPUT_REF:
        # logical_type stays "file" but component falls back to "readonly"
        # because no loader_adapter name was assigned.
        control = _hand_built(
            component="readonly",
            inference_reason="adapter_required;owned_input",
            raw_metadata={"media_kind": "video"},
        )
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, "whatever")
        self.assertEqual(ctx.exception.detail.code, "unsupported_loader_adapter")
        self.assertIn("video", ctx.exception.detail.message)

    def test_unrecognized_adapter_name_is_rejected_even_if_marked_editable(
        self,
    ) -> None:
        control = _hand_built(
            inference_reason="loader_adapter:some_future_adapter;owned_input"
        )
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, "whatever")
        self.assertEqual(ctx.exception.detail.code, "unsupported_loader_adapter")

    def test_foreign_or_missing_upload_id_is_reported_uniformly(self) -> None:
        control = _hand_built()
        row = self.store(OTHER, "image", "ref.png", png_bytes())
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertEqual(ctx.exception.detail.code, "upload_missing")
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, "does-not-exist")
        self.assertEqual(ctx.exception.detail.code, "upload_missing")

    def test_kind_mismatch_is_rejected(self) -> None:
        control = _hand_built()  # expects image
        row = self.store(OWNER, "video", "clip.mp4", VIDEO_BYTES)
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertEqual(ctx.exception.detail.code, "upload_kind_mismatch")

    def test_staging_failure_surfaces_the_upload_error_code(self) -> None:
        control = _hand_built()
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        self.settings.set_host("comfy_input_dir", "")
        with self.assertRaises(InputAdapterError) as ctx:
            bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertEqual(ctx.exception.detail.code, "input_dir_unavailable")


if __name__ == "__main__":
    import unittest

    unittest.main()
