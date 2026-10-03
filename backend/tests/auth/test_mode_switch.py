"""Activation, deactivation, and the admission gate during a mode switch."""

from __future__ import annotations

import threading
import unittest

from app.auth.service import (
    AdmissionClosed,
    AdmissionGate,
    Conflict,
    media_baseline_status,
)
from app.storage.repository import DEFAULT_PROFILE_ID, now_ms

from .support import AuthTestCase, ready_baseline


class BaselineGateTest(AuthTestCase):
    def test_activation_fails_safely_until_the_media_baseline_is_imported(self) -> None:
        """create_app wires MEDIA-001's real gate; an un-indexed library refuses.

        This app has no configured ComfyUI output folder and no completed
        import, so multi-user mode must be refused rather than handing
        Default's un-attributed files to new profiles.
        """
        self.assertIsNot(
            self.auth.baseline_gate,
            media_baseline_status,
            "main.py must replace the placeholder gate with the media service",
        )
        self.assertFalse(self.auth.baseline_gate(self.db).ready)
        response = self.post(
            self.local_client(),
            "/api/settings/multi-user/activate",
            json={"default_password": "default-password"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("local media import", response.json()["error"]["message"])
        # Nothing was half-applied.
        self.assertFalse(self.auth.multi_user_enabled())
        self.assertIsNone(
            self.db.query_one(
                "SELECT password_hash FROM profiles WHERE id = ?", (DEFAULT_PROFILE_ID,)
            )["password_hash"]
        )

    def test_activation_requires_a_default_password_before_the_baseline_check(
        self,
    ) -> None:
        self.auth.baseline_gate = ready_baseline
        response = self.post(
            self.local_client(),
            "/api/settings/multi-user/activate",
            json={"default_password": "short"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.auth.multi_user_enabled())


class ActivationTest(AuthTestCase):
    def test_activation_is_local_only(self) -> None:
        self.auth.baseline_gate = ready_baseline
        response = self.post(
            self.lan_client(),
            "/api/settings/multi-user/activate",
            json={"default_password": "default-password"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.auth.multi_user_enabled())

    def test_activation_invalidates_anonymous_sessions_and_sets_the_default_password(
        self,
    ) -> None:
        phone = self.lan_client()
        self.assertTrue(phone.get("/api/session").json()["anonymous"])

        self.auth.baseline_gate = ready_baseline
        response = self.post(
            self.local_client(),
            "/api/settings/multi-user/activate",
            json={"default_password": "default-password"},
        )
        self.assertEqual(response.status_code, 204)

        # The phone that needed no sign-in a moment ago is now unauthenticated.
        self.assertFalse(phone.get("/api/session").json()["authenticated"])
        self.assertEqual(phone.get("/api/settings").status_code, 401)
        self.assertEqual(
            self.login(phone, "Default", "default-password").status_code, 200
        )

    def test_activation_drops_existing_session_rows(self) -> None:
        # A cookie issued before activation must not survive it.
        token = self.auth._create_session(DEFAULT_PROFILE_ID)
        self.assertIsNotNone(self.auth.resolve(token))
        self.enable_multi_user("default-password")
        self.assertIsNone(self.auth.resolve(token))

    def test_activating_twice_conflicts(self) -> None:
        self.enable_multi_user("default-password")
        with self.assertRaises(Conflict):
            self.auth.activate_multi_user("another-password")


class DeactivationTest(AuthTestCase):
    def test_disabling_is_blocked_while_extra_profiles_exist(self) -> None:
        self.enable_multi_user("default-password")
        self.auth.create_profile("Alex", "alex-password")
        response = self.post(self.local_client(), "/api/settings/multi-user/deactivate")
        self.assertEqual(response.status_code, 409)
        self.assertIn("other profiles", response.json()["error"]["message"])
        self.assertTrue(self.auth.multi_user_enabled())

    def test_disabling_works_once_only_default_remains(self) -> None:
        self.enable_multi_user("default-password")
        response = self.post(self.local_client(), "/api/settings/multi-user/deactivate")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.auth.multi_user_enabled())
        # Back to the implicit Default session, and the old password is gone.
        self.assertTrue(self.lan_client().get("/api/session").json()["anonymous"])
        self.assertIsNone(
            self.db.query_one(
                "SELECT password_hash FROM profiles WHERE id = ?", (DEFAULT_PROFILE_ID,)
            )["password_hash"]
        )

    def test_disabling_is_local_only(self) -> None:
        self.enable_multi_user("default-password")
        self.assertEqual(
            self.post(
                self.lan_client(), "/api/settings/multi-user/deactivate"
            ).status_code,
            403,
        )


class AdmissionGateTest(unittest.TestCase):
    """The gate itself, without a database."""

    def test_closing_refuses_new_admissions_and_reopens_afterwards(self) -> None:
        gate = AdmissionGate()
        with gate.closed(lambda: True), self.assertRaises(AdmissionClosed):
            with gate.admit():
                pass
        self.assertTrue(gate.is_open)
        with gate.admit():
            pass

    def test_close_waits_for_an_in_flight_admission(self) -> None:
        gate = AdmissionGate()
        entered, release = threading.Event(), threading.Event()

        def submitter() -> None:
            with gate.admit():
                entered.set()
                release.wait(5)

        worker = threading.Thread(target=submitter)
        worker.start()
        self.assertTrue(entered.wait(5))

        closed = threading.Event()

        def switcher() -> None:
            with gate.closed(lambda: True, timeout=5):
                closed.set()

        switch = threading.Thread(target=switcher)
        switch.start()
        # The switch cannot complete while the submission is in flight.
        self.assertFalse(closed.wait(0.3))
        release.set()
        self.assertTrue(closed.wait(5))
        worker.join(5)
        switch.join(5)
        self.assertTrue(gate.is_open)

    def test_close_times_out_and_reopens_rather_than_wedging_the_server(self) -> None:
        gate = AdmissionGate()
        with self.assertRaises(Conflict):
            with gate.closed(lambda: False, timeout=0.2):
                self.fail("body must not run while submissions are unresolved")
        self.assertTrue(gate.is_open)
        with gate.admit():
            pass


class ModeSwitchDuringSubmissionTest(AuthTestCase):
    """The tricky case: activation raced by an in-flight generation submission."""

    def _insert_generation(self, status: str, key: str) -> str:
        generation_id = f"gen-{key}"
        stamp = now_ms()
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO generations (id, owner_id, client_request_key, request_fingerprint,"
                " status, created_ms, updated_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    generation_id,
                    DEFAULT_PROFILE_ID,
                    key,
                    "fingerprint",
                    status,
                    stamp,
                    stamp,
                ),
            )
        return generation_id

    def _reconcile(self, generation_id: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE generations SET status = 'succeeded', updated_ms = ? WHERE id = ?",
                (now_ms(), generation_id),
            )

    def test_activation_waits_for_an_unresolved_submission_then_commits(self) -> None:
        self.auth.baseline_gate = ready_baseline
        entered, release = threading.Event(), threading.Event()
        refused: list[Exception] = []

        def submitter() -> None:
            # What GEN-001's submit path does: take admission, persist the
            # generation before contacting ComfyUI, reconcile afterwards.
            with self.auth.admission.admit():
                generation_id = self._insert_generation("submitting", "in-flight")
                entered.set()
                release.wait(5)
                self._reconcile(generation_id)

        worker = threading.Thread(target=submitter)
        worker.start()
        self.assertTrue(entered.wait(5))

        done = threading.Event()
        failures: list[Exception] = []

        def activate() -> None:
            try:
                self.auth.activate_multi_user("default-password", timeout=10)
            except Exception as exc:  # pragma: no cover - reported below
                failures.append(exc)
            done.set()

        switch = threading.Thread(target=activate)
        switch.start()

        # Admission closes immediately; a second submission is refused, not queued
        # behind a mode change it would otherwise slip past.
        for _ in range(200):
            if not self.auth.admission.is_open:
                break
            threading.Event().wait(0.01)
        self.assertFalse(
            self.auth.admission.is_open, "admission did not close atomically"
        )
        try:
            with self.auth.admission.admit():
                self.fail("a second submission was admitted during the mode switch")
        except AdmissionClosed as exc:
            refused.append(exc)

        # ...and the switch has not committed while the first one is unresolved.
        self.assertFalse(done.wait(0.3))
        self.assertFalse(self.auth.multi_user_enabled())

        release.set()
        worker.join(5)
        self.assertTrue(done.wait(10))
        switch.join(5)

        self.assertEqual(failures, [])
        self.assertEqual(len(refused), 1)
        self.assertTrue(self.auth.multi_user_enabled())
        self.assertEqual(self.auth.unresolved_submissions(), 0)
        self.assertTrue(
            self.auth.admission.is_open, "admission did not reopen after the switch"
        )

    def test_activation_refuses_while_a_submission_stays_unresolved(self) -> None:
        self.auth.baseline_gate = ready_baseline
        self._insert_generation("submission_unknown", "stuck")
        with self.assertRaises(Conflict) as ctx:
            self.auth.activate_multi_user("default-password", timeout=0.3)
        self.assertIn("unresolved", str(ctx.exception))
        self.assertFalse(self.auth.multi_user_enabled())
        # The failed switch must not leave generations permanently refused.
        self.assertTrue(self.auth.admission.is_open)
        with self.auth.admission.admit():
            pass


if __name__ == "__main__":
    unittest.main()
