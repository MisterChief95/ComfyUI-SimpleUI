"""Sessions, isolation, CSRF, the host gate, throttling, and expiry, over HTTP."""

from __future__ import annotations

import unittest

from app.auth.security import CSRF_HEADER, SESSION_COOKIE
from app.auth.service import MAX_LOGIN_FAILURES
from app.storage.repository import DEFAULT_PROFILE_ID, Repository

from .support import LAN_ORIGIN, AuthTestCase


class SingleUserTest(AuthTestCase):
    def test_default_session_works_with_no_setup(self) -> None:
        body = self.lan_client().get("/api/session").json()
        self.assertTrue(body["authenticated"])
        self.assertTrue(body["anonymous"])
        self.assertFalse(body["multi_user"])
        self.assertEqual(body["profile"]["id"], DEFAULT_PROFILE_ID)
        self.assertIsNone(body["csrf_token"])

    def test_profile_settings_round_trip_without_signing_in(self) -> None:
        client = self.lan_client()
        response = self.put(client, "/api/settings/profile", json={"key": "theme", "value": "dark"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.get("/api/settings").json()["profile"]["theme"], "dark")

    def test_profile_settings_survive_repeated_writes(self) -> None:
        client = self.lan_client()
        for value in ("dark", "light", "light"):
            self.assertEqual(
                self.put(
                    client, "/api/settings/profile", json={"key": "theme", "value": value}
                ).status_code,
                204,
            )
        self.assertEqual(client.get("/api/settings").json()["profile"]["theme"], "light")
        rows = self.db.query("SELECT key FROM settings WHERE scope = 'profile' AND key = 'theme'")
        self.assertEqual(len(rows), 1, "repeated writes must update one row, not append")

    def test_unknown_setting_key_is_rejected(self) -> None:
        client = self.lan_client()
        response = self.put(
            client, "/api/settings/profile", json={"key": "not_a_setting", "value": True}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown setting", response.json()["error"]["message"])

    def test_login_is_refused_while_single_user(self) -> None:
        response = self.login(self.local_client(), "Default", "anything")
        self.assertEqual(response.status_code, 409)


class CsrfTest(AuthTestCase):
    def test_cross_site_origin_cannot_change_a_setting(self) -> None:
        client = self.lan_client()
        response = self.put(
            client,
            "/api/settings/profile",
            json={"key": "theme", "value": "dark"},
            headers={"origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.get("/api/settings").json()["profile"]["theme"], "system")

    def test_missing_origin_fails_closed(self) -> None:
        client = self.lan_client()
        client.headers.pop("origin")
        response = self.put(client, "/api/settings/profile", json={"key": "theme", "value": "dark"})
        self.assertEqual(response.status_code, 403)

    def test_authenticated_mutation_needs_the_csrf_header(self) -> None:
        self.enable_multi_user("default-password")
        client = self.lan_client()
        self.login(client, "Default", "default-password")
        # Same-origin header present, session cookie present, CSRF header absent.
        response = client.put("/api/settings/profile", json={"key": "theme", "value": "dark"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["error"]["message"])
        bad = client.put(
            "/api/settings/profile",
            json={"key": "theme", "value": "dark"},
            headers={CSRF_HEADER: "0" * 64},
        )
        self.assertEqual(bad.status_code, 403)

    def test_safe_requests_need_no_csrf_token(self) -> None:
        self.assertEqual(self.lan_client().get("/api/session").status_code, 200)


class HostGateTest(AuthTestCase):
    def test_lan_client_cannot_write_host_settings(self) -> None:
        client = self.lan_client()
        response = self.put(client, "/api/settings/host", json={"key": "pending_cap", "value": 3})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(client.get("/api/settings").json()["host_writable"])

    def test_forged_forwarded_headers_cannot_unlock_host_settings(self) -> None:
        client = self.lan_client()
        response = self.put(
            client,
            "/api/settings/host",
            json={"key": "pending_cap", "value": 3},
            headers={
                "x-forwarded-for": "127.0.0.1",
                "x-real-ip": "127.0.0.1",
                "forwarded": 'for="127.0.0.1";host=localhost',
                "x-forwarded-host": "localhost:8000",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.app.state.settings.host_value("pending_cap"), 8)

    def test_lan_client_claiming_a_loopback_host_is_still_refused(self) -> None:
        # Host and Origin both say localhost; only the real socket peer does not.
        client = self.lan_client()
        response = self.put(
            client,
            "/api/settings/host",
            json={"key": "pending_cap", "value": 3},
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:8000",
                "x-forwarded-for": "127.0.0.1",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.app.state.settings.host_value("pending_cap"), 8)

    def test_host_settings_survive_repeated_writes(self) -> None:
        client = self.local_client()
        for value in (3, 5, 5):
            self.assertEqual(
                self.put(
                    client, "/api/settings/host", json={"key": "pending_cap", "value": value}
                ).status_code,
                204,
            )
        self.assertEqual(self.app.state.settings.host_value("pending_cap"), 5)
        rows = self.db.query("SELECT key FROM settings WHERE scope = 'host' AND key = 'pending_cap'")
        self.assertEqual(len(rows), 1, "repeated writes must update one row, not append")

    def test_local_client_can_write_host_settings(self) -> None:
        client = self.local_client()
        response = self.put(client, "/api/settings/host", json={"key": "pending_cap", "value": 3})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(client.get("/api/settings").json()["host"]["pending_cap"], 3)
        self.assertTrue(client.get("/api/settings").json()["host_writable"])

    def test_multi_user_flag_is_not_writable_as_a_plain_host_setting(self) -> None:
        # Enabling it has preconditions; the settings API must not be a bypass.
        response = self.put(
            self.local_client(),
            "/api/settings/host",
            json={"key": "multi_user_enabled", "value": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.auth.multi_user_enabled())


class MultiUserSessionTest(AuthTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enable_multi_user("default-password")
        self.auth.create_profile("Alex", "alex-password")

    def test_unauthenticated_requests_are_rejected(self) -> None:
        client = self.lan_client()
        self.assertFalse(client.get("/api/session").json()["authenticated"])
        self.assertEqual(client.get("/api/settings").status_code, 401)

    def test_login_sets_an_httponly_session_cookie_and_logout_clears_it(self) -> None:
        client = self.lan_client()
        response = self.login(client, "Alex", "alex-password")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["authenticated"])
        self.assertFalse(body["anonymous"])
        self.assertEqual(body["profile"]["name"], "Alex")
        self.assertTrue(body["csrf_token"])
        cookie = response.headers["set-cookie"]
        self.assertIn("httponly", cookie.lower())
        self.assertIn("samesite=strict", cookie.lower())
        self.assertEqual(client.get("/api/settings").status_code, 200)

        self.assertEqual(self.delete(client, "/api/session").status_code, 200)
        self.assertFalse(client.get("/api/session").json()["authenticated"])
        self.assertEqual(client.get("/api/settings").status_code, 401)

    def test_wrong_password_and_unknown_profile_are_indistinguishable(self) -> None:
        client = self.lan_client()
        wrong = self.login(client, "Alex", "not-the-password")
        missing = self.login(client, "Nobody", "not-the-password")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.json()["error"]["message"], missing.json()["error"]["message"])

    def test_login_is_rate_limited(self) -> None:
        client = self.lan_client()
        for _ in range(MAX_LOGIN_FAILURES):
            self.assertEqual(self.login(client, "Alex", "wrong").status_code, 401)
        throttled = self.login(client, "Alex", "wrong")
        self.assertEqual(throttled.status_code, 429)
        # Even the correct password waits out the window.
        self.assertEqual(self.login(client, "Alex", "alex-password").status_code, 429)
        self.auth.throttle.reset("Alex")
        self.assertEqual(self.login(client, "Alex", "alex-password").status_code, 200)

    def test_expired_session_is_rejected_and_swept(self) -> None:
        client = self.lan_client()
        self.login(client, "Alex", "alex-password")
        token = client.cookies.get(SESSION_COOKIE)
        self.auth.expire_session_now(token)
        self.assertEqual(client.get("/api/settings").status_code, 401)
        self.assertEqual(
            self.db.query("SELECT token_hash FROM sessions"), [], "expired row was not swept"
        )

    def test_profile_creation_requires_the_local_gate(self) -> None:
        lan = self.lan_client()
        self.login(lan, "Alex", "alex-password")
        refused = self.post(lan, "/api/profiles", json={"name": "Sam", "password": "sam-password"})
        self.assertEqual(refused.status_code, 403)

        local = self.local_client()
        self.login(local, "Alex", "alex-password")
        created = self.post(
            local, "/api/profiles", json={"name": "Sam", "password": "sam-password"}
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            self.post(local, "/api/profiles", json={"name": "Sam", "password": "other-password"}).status_code,
            409,
        )

    def test_short_passwords_are_refused(self) -> None:
        response = self.post(
            self.local_client(), "/api/profiles", json={"name": "Sam", "password": "short"}
        )
        self.assertEqual(response.status_code, 400)

    def test_password_change_signs_the_profile_out_everywhere(self) -> None:
        phone, laptop = self.lan_client(), self.lan_client()
        self.login(phone, "Alex", "alex-password")
        self.login(laptop, "Alex", "alex-password")
        response = self.post(
            phone,
            "/api/profiles/me/password",
            json={"current_password": "alex-password", "new_password": "alex-new-password"},
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(laptop.get("/api/settings").status_code, 401)
        self.assertEqual(self.login(laptop, "Alex", "alex-new-password").status_code, 200)

    def test_password_change_needs_the_current_password(self) -> None:
        client = self.lan_client()
        self.login(client, "Alex", "alex-password")
        response = self.post(
            client,
            "/api/profiles/me/password",
            json={"current_password": "guessed", "new_password": "alex-new-password"},
        )
        self.assertEqual(response.status_code, 401)


class CrossAccountIsolationTest(AuthTestCase):
    """Two accounts must never reach each other's API resources."""

    def setUp(self) -> None:
        super().setUp()
        self.enable_multi_user("default-password")
        self.auth.create_profile("Alex", "alex-password")
        self.auth.create_profile("Sam", "sam-password")
        self.alex = self.lan_client()
        self.sam = self.lan_client()
        self.login(self.alex, "Alex", "alex-password")
        self.login(self.sam, "Sam", "sam-password")

    def test_owner_comes_from_the_session_not_the_request(self) -> None:
        alex_id = self.alex.get("/api/session").json()["profile"]["id"]
        sam_id = self.sam.get("/api/session").json()["profile"]["id"]
        self.assertNotEqual(alex_id, sam_id)
        # A body that tries to name an owner is rejected outright (extra=forbid).
        response = self.put(
            self.sam,
            "/api/settings/profile",
            json={"key": "theme", "value": "dark", "owner_id": alex_id},
        )
        self.assertEqual(response.status_code, 422)

    def test_profile_settings_are_private_to_their_owner(self) -> None:
        self.put(self.alex, "/api/settings/profile", json={"key": "theme", "value": "dark"})
        self.assertEqual(self.alex.get("/api/settings").json()["profile"]["theme"], "dark")
        self.assertEqual(self.sam.get("/api/settings").json()["profile"]["theme"], "system")

    def test_owned_records_are_not_reachable_across_accounts(self) -> None:
        """The session's owner_id is what DATA-001's owner-scoped repository gets."""
        repo = Repository(self.db)
        alex_id = self.alex.get("/api/session").json()["profile"]["id"]
        sam_id = self.sam.get("/api/session").json()["profile"]["id"]

        workflow_id = repo.create_workflow(alex_id, "Alex portrait", {"1": {"class_type": "X"}})
        generation_id = repo.create_generation(
            alex_id, client_request_key="k1", request_fingerprint="f1", workflow_id=workflow_id
        )
        media_id = repo.record_media(
            alex_id,
            storage_path="alex/out_00001.png",
            file_version="v1",
            media_kind="image",
            media_type="image/png",
            generation_id=generation_id,
        )

        for getter, row_id in (
            (repo.get_workflow, workflow_id),
            (repo.get_generation, generation_id),
            (repo.get_media, media_id),
        ):
            self.assertIsNotNone(getter(alex_id, row_id))
            self.assertIsNone(getter(sam_id, row_id), "another profile's row was reachable")
        self.assertEqual(repo.list_workflows(sam_id).items, [])
        self.assertIsNone(repo.get_workflow_graph(sam_id, workflow_id, 1))

    def test_profile_list_exposes_names_only(self) -> None:
        profiles = self.sam.get("/api/profiles").json()["profiles"]
        self.assertEqual({p["name"] for p in profiles}, {"Default", "Alex", "Sam"})
        self.assertEqual(set(profiles[0]), {"id", "name", "is_default"})

    def test_a_stolen_csrf_token_is_useless_on_another_session(self) -> None:
        alex_csrf = self.alex.get("/api/session").json()["csrf_token"]
        response = self.sam.put(
            "/api/settings/profile",
            json={"key": "theme", "value": "dark"},
            headers={CSRF_HEADER: alex_csrf, "origin": LAN_ORIGIN},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
