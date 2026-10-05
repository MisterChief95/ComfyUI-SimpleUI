"""The authenticated /api/uploads surface: streaming, bounds, and privacy.

Uses the real app from app.main.create_app (AuthTestCase), which already
wires UploadService per app/main.py -- this pins that wiring the same way
tests/media/test_api.py pins media's.
"""

from __future__ import annotations

from tests.auth.support import AuthTestCase

from .support import VIDEO_BYTES, png_bytes

ALEX_PASSWORD = "alex-password"
SAM_PASSWORD = "sam-password"


class UploadApiTestCase(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enable_multi_user(ALEX_PASSWORD)  # also names Default's own password
        self.auth.create_profile("Alex", ALEX_PASSWORD)
        self.auth.create_profile("Sam", SAM_PASSWORD)

    def _signed_in(self, name: str, password: str):
        client = self.local_client()
        response = self.login(client, name, password)
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def upload(self, client, kind: str, filename: str, data: bytes, content_type: str):
        return self.post(
            client,
            "/api/uploads",
            params={"kind": kind, "filename": filename},
            content=data,
            headers={"content-type": content_type},
        )


class UploadRoundTripTest(UploadApiTestCase):
    def test_image_upload_is_accepted_and_staged(self) -> None:
        client = self._signed_in("Alex", ALEX_PASSWORD)
        response = self.upload(client, "image", "ref.png", png_bytes(), "image/png")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["media_kind"], "image")

        listing = client.get("/api/uploads").json()
        self.assertEqual([item["id"] for item in listing["items"]], [body["id"]])

    def test_video_upload_is_accepted_without_full_body_buffering(self) -> None:
        client = self._signed_in("Alex", ALEX_PASSWORD)
        # Large enough to matter, small enough for a fast test; the point is the
        # request succeeds via the chunked read loop, not a single .read().
        large_video = VIDEO_BYTES * 4096  # ~4 MiB
        response = self.upload(client, "video", "clip.mp4", large_video, "video/mp4")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["media_kind"], "video")

    def test_oversized_upload_is_rejected(self) -> None:
        self.app.state.settings.set_host("upload_max_bytes", 1024)
        client = self._signed_in("Alex", ALEX_PASSWORD)
        response = self.upload(client, "video", "clip.mp4", VIDEO_BYTES, "video/mp4")
        self.assertEqual(response.status_code, 413, response.text)

    def test_corrupt_image_is_rejected(self) -> None:
        client = self._signed_in("Alex", ALEX_PASSWORD)
        response = self.upload(
            client, "image", "ref.png", b"not a real png", "image/png"
        )
        self.assertEqual(response.status_code, 415, response.text)

    def test_unauthenticated_upload_is_refused(self) -> None:
        client = self.local_client()
        response = self.upload(client, "image", "ref.png", png_bytes(), "image/png")
        self.assertEqual(response.status_code, 401)


class OwnershipIsolationTest(UploadApiTestCase):
    def test_profile_cannot_list_another_profiles_uploads(self) -> None:
        alex = self._signed_in("Alex", ALEX_PASSWORD)
        self.upload(alex, "image", "ref.png", png_bytes(), "image/png")

        sam = self._signed_in("Sam", SAM_PASSWORD)
        listing = sam.get("/api/uploads").json()
        self.assertEqual(listing["items"], [])

    def test_profile_cannot_delete_another_profiles_upload_by_id(self) -> None:
        alex = self._signed_in("Alex", ALEX_PASSWORD)
        upload_id = self.upload(
            alex, "image", "ref.png", png_bytes(), "image/png"
        ).json()["id"]

        sam = self._signed_in("Sam", SAM_PASSWORD)
        response = self.delete(sam, f"/api/uploads/{upload_id}")
        self.assertEqual(response.status_code, 404)

        # Alex's own delete still works: the id was never actually consumed.
        response = self.delete(alex, f"/api/uploads/{upload_id}")
        self.assertEqual(response.status_code, 204)


if __name__ == "__main__":
    import unittest

    unittest.main()
