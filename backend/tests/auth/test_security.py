"""Unit checks for the trust-boundary primitives (no database, no app)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.auth.security import (
    IGNORED_FORWARDED_HEADERS,
    csrf_matches,
    csrf_token,
    hash_password,
    load_or_create_secret,
    new_session_token,
    request_is_local,
    request_origin_ok,
    token_hash,
    verify_password,
)


def fake_request(
    *, peer: str | None, headers: dict[str, str], scheme: str = "http"
) -> SimpleNamespace:
    lowered = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        client=None if peer is None else SimpleNamespace(host=peer),
        headers=lowered,
        url=SimpleNamespace(scheme=scheme),
    )


class PasswordTest(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self) -> None:
        first, second = hash_password("correct horse"), hash_password("correct horse")
        self.assertNotEqual(first, second)  # per-hash salt
        self.assertNotIn("correct horse", first)
        self.assertTrue(verify_password("correct horse", first))
        self.assertFalse(verify_password("Correct horse", first))

    def test_missing_or_corrupt_hash_never_verifies(self) -> None:
        # A profile with no password (single-user Default) cannot be logged into.
        self.assertFalse(verify_password("anything", None))
        self.assertFalse(verify_password("anything", ""))
        self.assertFalse(verify_password("anything", "not-an-encoded-hash"))
        self.assertFalse(verify_password("anything", "argon2$unsupported$scheme$a$b"))


class TokenTest(unittest.TestCase):
    def test_session_tokens_are_unique_and_stored_only_as_hashes(self) -> None:
        tokens = {new_session_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        token = tokens.pop()
        self.assertNotEqual(token_hash(token), token)
        self.assertEqual(token_hash(token), token_hash(token))

    def test_csrf_token_is_bound_to_the_session(self) -> None:
        secret = b"x" * 32
        token = new_session_token()
        self.assertTrue(csrf_matches(secret, token, csrf_token(secret, token)))
        self.assertFalse(csrf_matches(secret, token, csrf_token(secret, new_session_token())))
        self.assertFalse(csrf_matches(secret, token, None))
        self.assertFalse(csrf_matches(b"y" * 32, token, csrf_token(secret, token)))

    def test_secret_is_created_once_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "session_secret"
            first = load_or_create_secret(path)
            self.assertEqual(len(first), 32)
            self.assertEqual(load_or_create_secret(path), first)


class LocalityTest(unittest.TestCase):
    def test_loopback_peer_with_loopback_host_is_local(self) -> None:
        for peer, host in (
            ("127.0.0.1", "localhost:8000"),
            ("127.0.0.1", "127.0.0.1:8000"),
            ("::1", "[::1]:8000"),
        ):
            self.assertTrue(request_is_local(fake_request(peer=peer, headers={"host": host})))

    def test_lan_peer_is_never_local(self) -> None:
        self.assertFalse(
            request_is_local(fake_request(peer="192.168.1.50", headers={"host": "localhost:8000"}))
        )

    def test_forged_forwarded_headers_do_not_grant_locality(self) -> None:
        # The exact attack the host gate must survive: a LAN device claiming to
        # be loopback through proxy headers nobody configured.
        forged = {header: "127.0.0.1" for header in IGNORED_FORWARDED_HEADERS}
        forged["forwarded"] = 'for="127.0.0.1";host=localhost;proto=http'
        forged["x-forwarded-host"] = "localhost:8000"
        self.assertFalse(
            request_is_local(
                fake_request(peer="192.168.1.50", headers={"host": "192.168.1.10:8000", **forged})
            )
        )
        # The peer check must carry this on its own: a LAN device that also
        # addresses the server as "localhost" (an /etc/hosts entry, an SSH
        # tunnel, a Host header it simply made up) is still not local.
        self.assertFalse(
            request_is_local(
                fake_request(peer="192.168.1.50", headers={"host": "localhost:8000", **forged})
            )
        )
        # ...and a loopback peer still cannot be talked into a non-loopback Host.
        self.assertFalse(
            request_is_local(
                fake_request(peer="127.0.0.1", headers={"host": "simpleui.example:8000", **forged})
            )
        )

    def test_missing_client_or_host_is_not_local(self) -> None:
        self.assertFalse(request_is_local(fake_request(peer=None, headers={"host": "localhost"})))
        self.assertFalse(request_is_local(fake_request(peer="127.0.0.1", headers={})))


class OriginTest(unittest.TestCase):
    def test_matching_origin_is_accepted(self) -> None:
        request = fake_request(
            peer="192.168.1.50",
            headers={"host": "192.168.1.10:8000", "origin": "http://192.168.1.10:8000"},
        )
        self.assertTrue(request_origin_ok(request))

    def test_default_ports_normalize(self) -> None:
        self.assertTrue(
            request_origin_ok(
                fake_request(
                    peer="127.0.0.1",
                    headers={"host": "localhost", "origin": "http://localhost:80"},
                )
            )
        )

    def test_cross_site_origin_is_refused(self) -> None:
        for origin in ("http://evil.example", "https://localhost:8000", "http://localhost:9999"):
            self.assertFalse(
                request_origin_ok(
                    fake_request(peer="127.0.0.1", headers={"host": "localhost:8000", "origin": origin})
                )
            )

    def test_referer_is_the_fallback_and_absence_fails_closed(self) -> None:
        self.assertTrue(
            request_origin_ok(
                fake_request(
                    peer="127.0.0.1",
                    headers={"host": "localhost:8000", "referer": "http://localhost:8000/gallery"},
                )
            )
        )
        self.assertFalse(
            request_origin_ok(fake_request(peer="127.0.0.1", headers={"host": "localhost:8000"}))
        )


if __name__ == "__main__":
    unittest.main()
