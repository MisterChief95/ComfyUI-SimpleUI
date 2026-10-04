"""Media uploads use exactly the existing API-JSON graph importer."""

import io
import json
import subprocess
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .test_layout import GRAPH, PASSWORD, LayoutTestCase


class MediaImportTest(LayoutTestCase):
    def png(self, tags):
        data = io.BytesIO()
        chunks = PngInfo()
        for key, value in tags.items():
            chunks.add_text(key, value)
        Image.new("RGB", (16, 16)).save(data, format="PNG", pnginfo=chunks)
        return data.getvalue()

    def upload(self, content, filename="saved.png", client=None):
        client = client or self.local_client()
        return self.post(
            client,
            "/api/workflows/import-media",
            params={"name": "From media", "filename": filename},
            content=content,
            headers={"content-type": "application/octet-stream"},
        )

    def test_stock_saveimage_chunks_import_identically_to_api_json(self):
        graph = json.loads(GRAPH.read_bytes())
        graph["5"]["inputs"]["seed"] = 18446744073709551615
        graph["2"]["inputs"]["text"] = "a café, with a large seed"
        raw = json.dumps(graph)
        client = self.local_client()
        api = self.post(client, "/api/workflows?name=API", content=raw).json()
        response = self.upload(
            self.png({"prompt": raw, "workflow": '{"nodes": []}'}), client=client
        )
        self.assertEqual(response.status_code, 201, response.text)
        stored = self.app.state.repository.get_workflow_graph(
            "default", response.json()["id"], 1
        )
        original = self.app.state.repository.get_workflow_graph("default", api["id"], 1)
        self.assertEqual(stored, graph)
        self.assertEqual(stored, original)
        self.assertEqual(
            list(self.app.state.config.data_dir.glob("workflow-import-*")), []
        )

    def test_stock_webp_camera_exif_imports(self):
        data = io.BytesIO()
        exif = Image.Exif()
        exif[0x0110] = "prompt:" + GRAPH.read_text(encoding="utf-8")
        exif[0x010F] = 'workflow:{"nodes": []}'
        Image.new("RGB", (16, 16)).save(data, format="WEBP", exif=exif)
        response = self.upload(data.getvalue(), "saved.webp")
        self.assertEqual(response.status_code, 201, response.text)

    def test_missing_ui_only_malformed_and_duplicate_keys_are_diagnostics(self):
        for tags, expected in (
            ({}, "API JSON"),
            ({"workflow": '{"nodes": []}'}, "UI-format"),
            ({"prompt": "{broken"}, "JSON"),
            ({"prompt": '{"1":{},"1":{}}'}, "duplicate"),
        ):
            response = self.upload(self.png(tags))
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn(expected.casefold(), response.text.casefold())
        self.assertEqual(self.db.query_one("SELECT COUNT(*) n FROM workflows")["n"], 0)
        self.assertEqual(
            list(self.app.state.config.data_dir.glob("workflow-import-*")), []
        )

    def test_video_container_prompt_and_missing_probe(self):
        probe = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "format": {
                        "tags": {
                            "comment": json.dumps(
                                {"prompt": json.loads(GRAPH.read_bytes())}
                            )
                        }
                    }
                }
            ).encode(),
            stderr=b"",
        )
        with (
            patch("app.media.metadata.shutil.which", return_value="ffprobe"),
            patch("app.media.metadata.subprocess.run", return_value=probe),
        ):
            response = self.upload(b"container-fixture", "saved.mp4")
        self.assertEqual(response.status_code, 201, response.text)
        with patch("app.media.metadata.shutil.which", return_value=None):
            response = self.upload(b"container-fixture", "saved.mp4")
        self.assertEqual(response.status_code, 422)
        self.assertIn("ffprobe", response.text)

    def test_size_type_auth_csrf_and_cleanup(self):
        self.assertEqual(self.upload(b"", "saved.txt").status_code, 415)
        with patch("app.workflows.routes.MAX_MEDIA_IMPORT_BYTES", 10):
            response = self.upload(b"x" * 11)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            list(self.app.state.config.data_dir.glob("workflow-import-*")), []
        )
        self.enable_multi_user(PASSWORD)
        client = self.local_client()
        self.assertEqual(self.upload(b"", client=client).status_code, 401)
        self.login(client, "Default", PASSWORD)
        response = client.post(
            "/api/workflows/import-media?name=Test&filename=x.png", content=b""
        )
        self.assertEqual(response.status_code, 403)
