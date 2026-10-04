"""Embedded metadata fixtures, privacy, search, and history-off boundaries."""

import json
from unittest.mock import patch

from app.media.metadata import (
    MAX_METADATA_BYTES,
    embedded_api_prompt,
    parse_metadata,
    read_metadata,
)
from app.media.service import MediaService
from app.storage import DEFAULT_PROFILE_ID, Repository
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .test_api import MediaApiTestCase

BIG = 18446744073709551615
GRAPH = {
    "3": {"class_type": "KSampler", "inputs": {"seed": BIG}},
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "model.safetensors"},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "a provenance portrait", "clip": ["4", 1]},
    },
}


class MetadataTest(MediaApiTestCase):
    def fixture(self, name, tags):
        path = self.output / name
        chunks = PngInfo()
        for key, value in tags.items():
            chunks.add_text(key, value)
        Image.new("RGB", (16, 16)).save(path, pnginfo=chunks)
        return path

    def test_comfy_png_preserves_raw_prompt_and_large_seed(self):
        raw = json.dumps(GRAPH)
        path = self.fixture("comfy.png", {"prompt": raw, "workflow": '{"nodes": []}'})
        provenance = read_metadata(path)
        self.assertEqual(provenance["source"], "comfyui")
        self.assertEqual(provenance["values"]["3:seed"], str(BIG))
        self.assertEqual(provenance["raw"]["prompt"], raw)
        self.assertEqual(json.loads(embedded_api_prompt(provenance)), GRAPH)
        self.assertEqual(provenance["diagnostics"], [])
        self.media.import_baseline()
        media_id = self.own_media()["comfy.png"]["id"]
        client = self.local_client()
        self.assertEqual(
            client.get(f"/api/media/{media_id}/provenance").json(), provenance
        )
        for field, term in (
            ("prompt", "provenance"),
            ("model", "model.safetensors"),
            ("seed", str(BIG)),
        ):
            page = client.get(
                "/api/media", params={"prompt": term, "search_field": field}
            ).json()
            self.assertEqual([item["id"] for item in page["items"]], [media_id])
        self.assertEqual(
            client.get(
                "/api/media/suggestions",
                params={"q": "provenance", "search_field": "prompt"},
            ).json()["items"],
            ["a provenance portrait"],
        )
        other = Repository(self.db).create_profile("Other")
        self.assertEqual(
            Repository(self.db).list_media(other, prompt="provenance").items, []
        )
        self.assertEqual(
            Repository(self.db).media_filter_suggestions(other, "provenance"), []
        )
        self.assertIsNone(self.media.provenance(other, media_id))

    def test_a1111_multiline_parameters(self):
        text = f"a cat, watercolor\nsecond prompt line\nNegative prompt: blur, noise\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: {BIG}, Size: 512x512, Model: portrait-model"
        path = self.fixture("a1111.png", {"parameters": text})
        result = read_metadata(path)
        self.assertEqual(result["source"], "a1111")
        self.assertEqual(result["values"]["model"], "portrait-model")
        self.assertEqual(result["values"]["seed"], str(BIG))
        self.assertEqual(result["values"]["negative_prompt"], "blur, noise")
        self.assertEqual(
            result["values"]["prompt"], "a cat, watercolor\nsecond prompt line"
        )
        self.assertEqual(result["raw"]["parameters"], text)

    def test_missing_malformed_ui_only_and_limits(self):
        for name, tags, diagnostic in (
            ("empty.png", {}, "No embedded"),
            ("broken.png", {"prompt": "{broken"}, "malformed"),
            ("ui.png", {"workflow": '{"nodes": []}'}, "UI-format"),
        ):
            result = read_metadata(self.fixture(name, tags))
            self.assertIn(diagnostic, " ".join(result["diagnostics"]))
        for tags in ({"prompt": "x" * (MAX_METADATA_BYTES + 1)}, {"prompt": 123}):
            result = parse_metadata(tags)
            self.assertEqual(result["raw"], {})
            self.assertTrue(result["diagnostics"])

    def test_backfill_and_never_rehydrate_captures(self):
        self.fixture("import.png", {"prompt": json.dumps(GRAPH)})
        self.media.import_baseline()
        media_id = self.own_media()["import.png"]["id"]
        with self.db.write() as conn:
            conn.execute("DELETE FROM media_provenance")
            conn.execute("DELETE FROM media_search")
        second = MediaService(self.db, self.settings, self.app.state.config.data_dir)
        self.addCleanup(second.close)
        self.assertEqual(
            second.provenance(DEFAULT_PROFILE_ID, media_id)["values"]["3:seed"],
            str(BIG),
        )
        # Treating the same row as a capture models a history-off output without a snapshot.
        with self.db.write() as conn:
            conn.execute("DELETE FROM media_provenance")
            conn.execute("DELETE FROM media_search")
            conn.execute(
                "UPDATE media_locations SET source_id = 'captures' WHERE media_id = ?",
                (media_id,),
            )
        with patch(
            "app.media.service.read_metadata",
            side_effect=AssertionError("must not read captures"),
        ):
            second._recover_provenance()
            result = second.provenance(DEFAULT_PROFILE_ID, media_id)
        self.assertEqual(result["source"], "generation")
        self.assertEqual(result["values"], {})

    def test_webp_exif_and_optional_video_probe(self):
        path = self.output / "comfy.webp"
        exif = Image.Exif()
        exif[37510] = "prompt:" + json.dumps(GRAPH)
        Image.new("RGB", (16, 16)).save(path, exif=exif)
        self.assertEqual(read_metadata(path)["values"]["3:seed"], str(BIG))
        video = self.video("clip.mp4")
        with patch("app.media.metadata.shutil.which", return_value=None):
            self.assertIn("ffprobe", read_metadata(video)["diagnostics"][0])
