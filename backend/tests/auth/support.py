"""Shared fixtures for the AUTH-001 tests.

A TestClient is built with an explicit peer address and base URL so the local
host gate is exercised for real: `local_client` looks like a browser on the
machine itself, `lan_client` looks like a phone on the home network.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.auth.security import CSRF_COOKIE, CSRF_HEADER
from app.auth.service import BaselineStatus
from app.config import Config
from app.main import create_app
from fastapi.testclient import TestClient

LOCAL_ORIGIN = "http://localhost:8000"
LAN_ORIGIN = "http://192.168.1.10:8000"


def ready_baseline(_db) -> BaselineStatus:
    """Stand-in for MEDIA-001's real gate, used where activation must succeed."""
    return BaselineStatus(ready=True, reason="")


class AuthTestCase(unittest.TestCase):
    """One app on a temporary database, plus local and LAN clients for it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        build = root / "build"
        build.mkdir(parents=True)
        (build / "index.html").write_text("<!doctype html>SPA shell", encoding="utf-8")
        config = Config.from_env(
            {"SIMPLEUI_DATA_DIR": str(root / "data"), "SIMPLEUI_STATIC_DIR": str(build)}
        )
        self.app = create_app(config)
        self.auth = self.app.state.auth
        self.db = self.app.state.db
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.db.close)

    # --- clients ----------------------------------------------------------

    def local_client(self) -> TestClient:
        """A browser on the computer running SimpleUI (loopback peer and Host)."""
        return self._client(LOCAL_ORIGIN, ("127.0.0.1", 51000))

    def lan_client(self) -> TestClient:
        """A phone on the home network: same app, no local host privileges."""
        return self._client(LAN_ORIGIN, ("192.168.1.50", 51000))

    def _client(self, origin: str, peer: tuple[str, int]) -> TestClient:
        client = TestClient(
            self.app, base_url=origin, client=peer, raise_server_exceptions=False
        )
        client.headers["origin"] = origin
        return client

    # --- helpers ----------------------------------------------------------

    @staticmethod
    def csrf_headers(client: TestClient) -> dict[str, str]:
        token = client.cookies.get(CSRF_COOKIE)
        return {CSRF_HEADER: token} if token else {}

    def post(self, client: TestClient, url: str, **kwargs):
        headers = {**self.csrf_headers(client), **kwargs.pop("headers", {})}
        return client.post(url, headers=headers, **kwargs)

    def put(self, client: TestClient, url: str, **kwargs):
        headers = {**self.csrf_headers(client), **kwargs.pop("headers", {})}
        return client.put(url, headers=headers, **kwargs)

    def delete(self, client: TestClient, url: str, **kwargs):
        headers = {**self.csrf_headers(client), **kwargs.pop("headers", {})}
        return client.delete(url, headers=headers, **kwargs)

    def enable_multi_user(self, password: str = "default-password") -> None:
        """Activate through the real service path, with the baseline gate stubbed."""
        self.auth.baseline_gate = ready_baseline
        self.auth.activate_multi_user(password)

    def login(self, client: TestClient, name: str, password: str):
        return self.post(
            client, "/api/session", json={"name": name, "password": password}
        )
