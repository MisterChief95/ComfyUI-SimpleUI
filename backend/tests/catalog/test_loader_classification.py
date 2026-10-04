import unittest

from app.catalog.normalize import _project_input
from app.comfy_client import sanitize_rejection


def combo(options, **opts):
    return _project_input("X", "f", [options, opts], {}, False)[0]


class LoaderClassificationTests(unittest.TestCase):
    def test_flag_beats_extensions(self) -> None:
        got = combo(["a.mp4", "b.ogg"], audio_upload=True)
        self.assertEqual(got["media_kind"], "audio")
        self.assertEqual(got["classification_conflict"]["extensions"], "video")

    def test_flag_with_empty_options(self) -> None:
        self.assertEqual(combo([], video_upload=True)["media_kind"], "video")

    def test_generic_uploads_are_not_images(self) -> None:
        for flag in ("file_upload", "model_upload", "text_upload"):
            got = combo(["a.png"], **{flag: True})
            self.assertEqual(got["media_kind"], "file")
            self.assertIsNone(got["loader_adapter"])

    def test_extension_fallback_without_flag(self) -> None:
        got = combo(["a.png"])
        self.assertEqual(got["media_kind"], "image")
        self.assertEqual(got["loader_adapter"], "comfy_input_dir_filename")

    def test_output_folder_loader_has_no_input_adapter(self) -> None:
        got = combo(["a.png"], image_upload=True, image_folder="output")
        self.assertIsNone(got["loader_adapter"])

    def test_vhs_path_string_stays_string(self) -> None:
        got = _project_input(
            "V", "video", ["STRING", {"vhs_path_extensions": ["mp4"]}], {}, False
        )[0]
        self.assertEqual(got["logical_type"], "STRING")

    def test_rejection_is_sanitized(self) -> None:
        body = {
            "error": {
                "message": "Prompt outputs failed validation",
                "details": "/secret/path",
            },
            "node_errors": {
                "3": {
                    "errors": [
                        {
                            "message": "Value not in list",
                            "details": "/x",
                            "extra_info": {"input_name": "ckpt_name"},
                        }
                    ]
                }
            },
        }
        got = sanitize_rejection(body)
        self.assertEqual(
            got["node_errors"],
            [
                {
                    "node_id": "3",
                    "message": "Value not in list",
                    "input_name": "ckpt_name",
                }
            ],
        )
        self.assertNotIn("/secret", str(got))
        self.assertIsNone(sanitize_rejection({"nope": 1}))
