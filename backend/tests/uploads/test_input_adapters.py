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
from app.mapping.input_adapters import InputAdapterError, bind_media, bind_upload
from app.media.service import LocatedMedia

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
    base = {
        "binding_id": "9:file_input",
        "node_id": "9",
        "class_type": "SomeLoader",
        "input_name": "file_input",
        "logical_type": "file",
        "value": None,
        "component": "file",
        "group": "inputs",
        "order": 0,
        "label": "Some loader: File input",
        "inference_reason": "loader_adapter:comfy_input_filename_image;owned_input",
        "raw_metadata": {"media_kind": "image"},
    }
    base.update(overrides)
    return ControlDescriptor(**base)


class RealFixtureBindingTest(UploadTestCase):
    async def test_supported_image_loader_control_binds_to_an_upload(self) -> None:
        control = _image_control()
        self.assertEqual(control.logical_type, "file")
        self.assertEqual(control.component, "file")

        row = self.store(OWNER, "image", "ref.png", png_bytes())
        staged = await bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertTrue(staged.startswith(f"simpleui/{OWNER}/"))


class DiagnosticsTest(UploadTestCase):
    async def test_non_file_control_is_rejected_outright(self) -> None:
        control = _hand_built(logical_type="string", component="text")
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_upload(control, self.uploads, OWNER, "whatever")
        self.assertEqual(ctx.exception.detail.code, "not_a_loader_control")

    async def test_video_loader_control_binds_video_upload(self) -> None:
        # This is normalize.py's actual shape for a video/audio OWNED_INPUT_REF:
        control = _hand_built(
            inference_reason="loader_adapter:comfy_input_filename_video;owned_input",
            raw_metadata={"media_kind": "video"},
        )
        row = self.store(OWNER, "video", "clip.mp4", VIDEO_BYTES)
        result = await bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertTrue(result.endswith(".mp4"))

    async def test_audio_loader_accepts_video_container_upload(self) -> None:
        control = _hand_built(
            inference_reason="loader_adapter:comfy_input_filename_audio;owned_input",
            raw_metadata={"media_kind": "audio"},
        )
        row = self.store(OWNER, "audio", "clip.mp4", VIDEO_BYTES)
        self.assertTrue(
            (await bind_upload(control, self.uploads, OWNER, row["id"])).endswith(
                ".mp4"
            )
        )

    async def test_unsupported_loader_metadata_is_actionable(self) -> None:
        control = _hand_built(
            component="readonly",
            inference_reason="adapter_required;owned_input",
            raw_metadata={"media_kind": "video"},
        )
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_upload(control, self.uploads, OWNER, "whatever")
        self.assertEqual(ctx.exception.detail.code, "unsupported_loader_adapter")
        self.assertIn("video", ctx.exception.detail.message)

    async def test_unrecognized_adapter_name_is_rejected_even_if_marked_editable(
        self,
    ) -> None:
        control = _hand_built(
            inference_reason="loader_adapter:some_future_adapter;owned_input"
        )
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_upload(control, self.uploads, OWNER, "whatever")
        self.assertEqual(ctx.exception.detail.code, "unsupported_loader_adapter")

    async def test_foreign_or_missing_upload_id_is_reported_uniformly(self) -> None:
        control = _hand_built()
        row = self.store(OTHER, "image", "ref.png", png_bytes())
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertEqual(ctx.exception.detail.code, "upload_missing")
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_upload(control, self.uploads, OWNER, "does-not-exist")
        self.assertEqual(ctx.exception.detail.code, "upload_missing")

    async def test_kind_mismatch_is_rejected(self) -> None:
        control = _hand_built()  # expects image
        row = self.store(OWNER, "video", "clip.mp4", VIDEO_BYTES)
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertEqual(ctx.exception.detail.code, "upload_kind_mismatch")


class MediaBindingTest(UploadTestCase):
    class Media:
        def __init__(self, item):
            self.item = item

        def locate(self, owner_id, media_id):
            return self.item if owner_id == OWNER and media_id == "media-1" else None

    def media(self, kind="image", version=None):
        path = self.root / "captured.png"
        path.write_bytes(png_bytes())
        stat = path.stat()
        version = (
            version or f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"
        )
        return self.Media(
            LocatedMedia(
                {"id": "media-1", "media_kind": kind, "file_version": version}, path
            )
        )

    async def test_media_is_copied_into_managed_upload_storage_and_staged(self):
        control = _image_control()
        staged, located = await bind_media(
            control, self.uploads, self.media(), OWNER, "media-1"
        )
        row = self.db.query_one(
            "SELECT staged_name, storage_path FROM uploads WHERE owner_id = ?", (OWNER,)
        )
        self.assertEqual(row["staged_name"], staged)
        self.assertEqual(
            (self.uploads.private_root / row["storage_path"]).read_bytes(), png_bytes()
        )
        self.assertTrue(located.row["file_version"])

    async def test_foreign_and_missing_media_have_the_same_diagnostic(self):
        control = _image_control()
        messages = []
        for owner, media_id in ((OWNER, "missing"), ("other-profile", "media-1")):
            with self.assertRaises(InputAdapterError) as ctx:
                await bind_media(control, self.uploads, self.media(), owner, media_id)
            messages.append((ctx.exception.detail.code, ctx.exception.detail.message))
        self.assertEqual(messages[0], messages[1])

    async def test_media_kind_and_version_are_checked(self):
        control = _image_control()
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_media(
                control, self.uploads, self.media(kind="video"), OWNER, "media-1"
            )
        self.assertEqual(ctx.exception.detail.code, "media_kind_mismatch")
        with self.assertRaises(InputAdapterError) as ctx:
            await bind_media(
                control,
                self.uploads,
                self.media(version="stale"),
                OWNER,
                "media-1",
            )
        self.assertEqual(ctx.exception.detail.code, "media_missing")

    async def test_staging_failure_surfaces_the_upload_error_code(self) -> None:
        control = _hand_built()
        row = self.store(OWNER, "image", "ref.png", png_bytes())
        with self.assertRaises(InputAdapterError) as ctx:
            self.comfy.response = {
                "name": "wrong.png",
                "subfolder": "foreign",
                "type": "input",
            }
            await bind_upload(control, self.uploads, OWNER, row["id"])
        self.assertEqual(ctx.exception.detail.code, "invalid_upload_response")


if __name__ == "__main__":
    import unittest

    unittest.main()
