"""The authenticated media API: gallery, streaming, range requests, privacy.

These tests also pin the wiring INTEGRATE-001 has to reproduce in main.py.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from PIL import Image

from app.media import MediaService
from app.media import router as media_router
from app.storage import DEFAULT_PROFILE_ID

# Absolute: under `discover -s tests` the top-level package is tests/ itself,
# so a relative import climbs out of it.
from tests.auth.support import AuthTestCase

from .support import VIDEO_BYTES

PASSWORD = "default-password"


class MediaApiTestCase(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        data_dir = self.app.state.config.data_dir
        self.output = data_dir.parent / "output"
        self.output.mkdir(parents=True, exist_ok=True)
        self.settings = self.app.state.settings
        self.settings.set_host("comfy_output_dir", str(self.output))

        self.media = MediaService(self.db, self.settings, data_dir)
        self.addCleanup(self.media.close)
        self.app.state.media = self.media
        self.auth.baseline_gate = self.media.baseline_status
        # main.py registers /api/{path:path} as a catch-all 404, and FastAPI
        # matches routes in registration order: the media router has to come
        # first or every media request is "No API route".
        catch_alls = [
            route
            for route in self.app.router.routes
            if getattr(route, "path", "").endswith("{path:path}")
        ]
        for route in catch_alls:
            self.app.router.routes.remove(route)
        self.app.include_router(media_router)
        self.app.router.routes.extend(catch_alls)

    def png(self, name: str, color: tuple[int, int, int] = (10, 120, 200)) -> Path:
        path = self.output / name
        Image.new("RGB", (64, 48), color).save(path)
        return path

    def video(self, name: str) -> Path:
        path = self.output / name
        path.write_bytes(VIDEO_BYTES)
        return path

    def own_media(self, owner_id: str = DEFAULT_PROFILE_ID) -> dict[str, dict]:
        return {item["filename"]: item for item in self.media.list_media(owner_id, None, 50)["items"]}


class GalleryTest(MediaApiTestCase):
    def test_the_default_session_sees_and_streams_indexed_files(self) -> None:
        self.png("photo.png")
        self.video("clip.mp4")
        self.media.import_baseline()
        client = self.local_client()

        listing = client.get("/api/media").json()
        self.assertEqual(
            {item["filename"] for item in listing["items"]}, {"photo.png", "clip.mp4"}
        )
        self.assertTrue(all(item["generation_id"] is None for item in listing["items"]))

        photo = self.own_media()["photo.png"]
        response = client.get(f"/api/media/{photo['id']}/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.content, (self.output / "photo.png").read_bytes())
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_a_thumbnail_is_generated_once_for_an_owned_image(self) -> None:
        self.png("photo.png")
        self.media.import_baseline()
        media_id = self.own_media()["photo.png"]["id"]
        client = self.local_client()

        response = client.get(f"/api/media/{media_id}/thumbnail")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        cached = self.media.thumbnails / f"{media_id}.jpg"
        self.assertTrue(cached.is_file())
        self.assertFalse(list(self.media.thumbnails.glob("*.part")))

    def test_a_video_has_no_thumbnail_rather_than_a_broken_one(self) -> None:
        self.video("clip.mp4")
        self.media.import_baseline()
        media_id = self.own_media()["clip.mp4"]["id"]

        self.assertEqual(
            self.local_client().get(f"/api/media/{media_id}/thumbnail").status_code, 404
        )

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is optional and not installed")
    def test_a_real_video_gets_a_poster_frame_when_ffmpeg_is_available(self) -> None:
        clip = self.output / "real.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.5:r=10",
             "-pix_fmt", "yuv420p", "-y", str(clip)],
            check=True,
        )
        self.media.import_baseline()
        media_id = self.own_media()["real.mp4"]["id"]

        response = self.local_client().get(f"/api/media/{media_id}/thumbnail")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertFalse(list(self.media.thumbnails.glob("*.part")))

    def test_download_serves_the_original_file_under_its_own_name(self) -> None:
        self.png("photo.png")
        self.media.import_baseline()
        media_id = self.own_media()["photo.png"]["id"]

        response = self.local_client().get(f"/api/media/{media_id}/download")

        self.assertEqual(response.status_code, 200)
        self.assertIn("photo.png", response.headers["content-disposition"])
        self.assertEqual(response.content, (self.output / "photo.png").read_bytes())

    def test_the_gallery_pages_and_rejects_a_broken_cursor(self) -> None:
        for index in range(3):
            self.png(f"photo{index}.png", color=(index, 40, 90))
        self.media.import_baseline()
        client = self.local_client()

        first = client.get("/api/media?limit=2").json()
        self.assertEqual(len(first["items"]), 2)
        rest = client.get(f"/api/media?limit=2&cursor={first['next_cursor']}").json()
        self.assertEqual(len(rest["items"]), 1)
        self.assertIsNone(rest["next_cursor"])
        self.assertEqual(
            len({item["id"] for item in first["items"] + rest["items"]}), 3
        )

        self.assertEqual(client.get("/api/media?cursor=not-a-cursor").status_code, 400)

    def test_an_unknown_id_is_a_404_not_the_spa_shell(self) -> None:
        response = self.local_client().get("/api/media/nope/file")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")


class VideoStreamingTest(MediaApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.video("clip.mp4")
        self.media.import_baseline()
        self.media_id = self.own_media()["clip.mp4"]["id"]
        self.url = f"/api/media/{self.media_id}/file"
        self.client = self.local_client()

    def test_head_reports_the_size_without_the_body(self) -> None:
        response = self.client.head(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-length"], str(len(VIDEO_BYTES)))
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.content, b"")

    def test_a_seek_gets_exactly_the_requested_bytes(self) -> None:
        response = self.client.get(self.url, headers={"range": "bytes=8-15"})

        self.assertEqual(response.status_code, 206)
        self.assertEqual(
            response.headers["content-range"], f"bytes 8-15/{len(VIDEO_BYTES)}"
        )
        self.assertEqual(response.content, VIDEO_BYTES[8:16])

    def test_an_open_ended_range_runs_to_the_end(self) -> None:
        start = len(VIDEO_BYTES) - 4
        response = self.client.get(self.url, headers={"range": f"bytes={start}-"})

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, VIDEO_BYTES[start:])

    def test_a_range_past_the_end_is_refused(self) -> None:
        response = self.client.get(self.url, headers={"range": "bytes=999999-"})

        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers["content-range"], f"bytes */{len(VIDEO_BYTES)}")


class DeleteTest(MediaApiTestCase):
    def test_a_captured_copy_is_removed_but_an_indexed_original_is_only_hidden(self) -> None:
        original = self.png("photo.png")
        self.media.import_baseline()
        indexed = self.own_media()["photo.png"]["id"]
        captured = self.media.capture_output(DEFAULT_PROFILE_ID, None, "photo.png")
        self.media.thumbnail(DEFAULT_PROFILE_ID, captured)
        copy = self.media.locate(DEFAULT_PROFILE_ID, captured).path
        thumb = self.media.thumbnails / f"{captured}.jpg"
        self.assertTrue(copy.is_file() and thumb.is_file())

        response = self.post(
            self.local_client(), "/api/media/delete", json={"ids": [captured, indexed, captured]}
        )

        self.assertEqual(response.json(), {"deleted": 2})
        self.assertEqual(self.own_media(), {})
        self.assertFalse(copy.exists() or thumb.exists())
        self.assertTrue(original.is_file())


class PrivacyTest(MediaApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.png("owned-by-default.png")
        self.video("owned-by-default.mp4")
        self.media.import_baseline()
        # The real MEDIA-001 gate, not a stub: activation is only possible
        # because the baseline above completed.
        self.auth.activate_multi_user(PASSWORD)
        self.auth.create_profile("Bee", PASSWORD)
        self.owned = self.own_media()

    def bee(self):
        client = self.local_client()
        self.assertEqual(self.login(client, "Bee", PASSWORD).status_code, 200)
        return client

    def test_another_profile_cannot_read_or_flag_the_media(self) -> None:
        client = self.bee()
        image = self.owned["owned-by-default.png"]["id"]
        video = self.owned["owned-by-default.mp4"]["id"]

        self.assertEqual(client.get("/api/media").json()["items"], [])
        for url in (
            f"/api/media/{image}/file",
            f"/api/media/{image}/download",
            f"/api/media/{image}/thumbnail",
            f"/api/media/{video}/file",
        ):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 404)
        # A range request is not a way around it either.
        self.assertEqual(
            client.get(f"/api/media/{video}/file", headers={"range": "bytes=0-3"}).status_code,
            404,
        )
        self.assertEqual(client.head(f"/api/media/{video}/file").status_code, 404)
        self.assertEqual(
            self.put(client, f"/api/media/{image}", json={"favorite": True}).status_code, 404
        )
        self.assertTrue((self.media.thumbnails / f"{image}.jpg").is_file())

    def test_the_owner_still_reaches_everything(self) -> None:
        client = self.local_client()
        self.assertEqual(self.login(client, "Default", PASSWORD).status_code, 200)
        image = self.owned["owned-by-default.png"]["id"]

        self.assertEqual(len(client.get("/api/media").json()["items"]), 2)
        self.assertEqual(client.get(f"/api/media/{image}/thumbnail").status_code, 200)
        self.assertEqual(
            self.put(client, f"/api/media/{image}", json={"favorite": True}).status_code, 200
        )
        self.assertTrue(self.own_media()["owned-by-default.png"]["favorite"])

    def test_another_profile_cannot_delete_the_media(self) -> None:
        image = self.owned["owned-by-default.png"]["id"]
        response = self.post(self.bee(), "/api/media/delete", json={"ids": [image]})
        self.assertEqual(response.json(), {"deleted": 0})
        self.assertIn("owned-by-default.png", self.own_media())

    def test_an_unauthenticated_request_gets_nothing(self) -> None:
        self.assertEqual(self.local_client().get("/api/media").status_code, 401)


class ImportRouteTest(MediaApiTestCase):
    def test_importing_is_a_local_host_operation(self) -> None:
        self.png("photo.png")

        refused = self.post(self.lan_client(), "/api/media/import")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(self.media.list_media(DEFAULT_PROFILE_ID, None, 50)["items"], [])

        accepted = self.post(self.local_client(), "/api/media/import")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), {"indexed": 1, "unresolved": 0})

    def test_a_changed_root_reports_a_conflict_until_it_is_confirmed(self) -> None:
        self.png("photo.png")
        self.post(self.local_client(), "/api/media/import")

        moved = self.output.parent / "moved-output"
        moved.mkdir()
        self.settings.set_host("comfy_output_dir", str(moved))

        client = self.local_client()
        self.assertEqual(self.post(client, "/api/media/import").status_code, 409)
        self.assertEqual(
            self.post(client, "/api/media/import?explicit=true").status_code, 200
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
