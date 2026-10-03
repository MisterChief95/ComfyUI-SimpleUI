"""The documented lockout path (docs/RECOVERY.md) must actually work."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from app.auth import recovery
from app.auth.security import verify_password
from app.storage.repository import DEFAULT_PROFILE_ID

from .support import AuthTestCase


class RecoveryTest(AuthTestCase):
    def _run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(recovery, "open_database", return_value=self.db):
            with mock.patch.object(recovery.Database, "close", lambda _self: None):
                with redirect_stdout(out), redirect_stderr(err):
                    code = recovery.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _default_hash(self) -> str | None:
        return self.db.query_one(
            "SELECT password_hash FROM profiles WHERE id = ?", (DEFAULT_PROFILE_ID,)
        )["password_hash"]

    def test_list_reports_mode_and_password_state(self) -> None:
        self.enable_multi_user("default-password")
        self.auth.create_profile("Alex", "alex-password")
        code, out, _ = self._run("list")
        self.assertEqual(code, 0)
        self.assertIn("multi-user mode: on", out)
        self.assertIn("Alex", out)
        self.assertIn("password set", out)

    def test_set_password_recovers_a_locked_out_household(self) -> None:
        self.enable_multi_user("forgotten-password")
        token = self.auth.login("Default", "forgotten-password")
        self.assertIsNotNone(self.auth.resolve(token))

        with mock.patch.object(
            recovery.getpass, "getpass", return_value="recovered-password"
        ):
            code, out, _ = self._run("set-password", "Default")
        self.assertEqual(code, 0)
        self.assertIn("Password updated", out)
        self.assertTrue(verify_password("recovered-password", self._default_hash()))
        # The old session is gone: a lost device stops working immediately.
        self.assertIsNone(self.auth.resolve(token))
        self.assertIsNotNone(self.auth.login("Default", "recovered-password"))

    def test_mismatched_or_short_passwords_change_nothing(self) -> None:
        self.enable_multi_user("default-password")
        with mock.patch.object(
            recovery.getpass, "getpass", side_effect=["one-password", "two"]
        ), self.assertRaises(SystemExit):
            self._run("set-password", "Default")
        with mock.patch.object(recovery.getpass, "getpass", return_value="short"):
            with self.assertRaises(SystemExit):
                self._run("set-password", "Default")
        self.assertTrue(verify_password("default-password", self._default_hash()))

    def test_unknown_profile_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            self._run("unlock", "Nobody")

    def test_unlock_clears_only_that_profiles_sessions(self) -> None:
        self.enable_multi_user("default-password")
        self.auth.create_profile("Alex", "alex-password")
        default_token = self.auth.login("Default", "default-password")
        alex_token = self.auth.login("Alex", "alex-password")

        code, _, _ = self._run("unlock", "Alex")
        self.assertEqual(code, 0)
        self.assertIsNone(self.auth.resolve(alex_token))
        self.assertIsNotNone(self.auth.resolve(default_token))

    def test_disable_multi_user_refuses_while_other_profiles_exist(self) -> None:
        self.enable_multi_user("default-password")
        self.auth.create_profile("Alex", "alex-password")
        code, _, err = self._run("disable-multi-user")
        self.assertEqual(code, 2)
        self.assertIn("Refused", err)
        self.assertTrue(self.auth.multi_user_enabled())

    def test_disable_multi_user_restores_the_default_session(self) -> None:
        self.enable_multi_user("default-password")
        code, out, _ = self._run("disable-multi-user")
        self.assertEqual(code, 0)
        self.assertIn("disabled", out)
        self.assertFalse(self.auth.multi_user_enabled())
        self.assertIsNone(self._default_hash())
        self.assertEqual(self.auth.resolve(None).profile_id, DEFAULT_PROFILE_ID)


if __name__ == "__main__":
    unittest.main()
