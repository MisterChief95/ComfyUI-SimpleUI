"""Profiles, sessions, and the single-user/multi-user mode switch.

Mode model (ARCHITECTURE.md "Profiles and privacy"):

* **Single-user** — the Default profile created by migration 001 is used
  automatically. No password, no login screen, nothing to set up. Requests with
  no session are resolved to Default *implicitly*; that fallback is what
  "anonymous session" means here, and it disappears the instant multi-user mode
  is on, which is why activation invalidates every anonymous client at once.
* **Multi-user** — every profile authenticates. There is no administrator role:
  profile creation and the mode switch are gated on the request being local to
  the host (see security.request_is_local), not on an account privilege.

Activation preconditions, all enforced here:

1. the media baseline scan is complete (MEDIA-001 supplies the real check),
2. the caller supplies a password for Default (it becomes Default's password),
3. admission for new generations is closed atomically and unresolved
   single-user submissions have reconciled.

Every call in this module blocks. From async code use ``await in_thread(...)``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from sqlite3 import IntegrityError
from typing import Any

from ..settings.service import MULTI_USER_ENABLED, SettingsStore, write_host
from ..storage.db import Database
from ..storage.repository import DEFAULT_PROFILE_ID, new_id, now_ms
from .security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    new_session_token,
    token_hash,
    verify_password,
)

#: Absolute session lifetime. Expired rows are rejected and swept on use.
SESSION_TTL_MS = 14 * 24 * 60 * 60 * 1000

#: Login throttle: failures per profile name inside a rolling window.
MAX_LOGIN_FAILURES = 10
LOGIN_WINDOW_S = 900

#: How long activation waits for in-flight submissions to reconcile.
DRAIN_TIMEOUT_S = 30.0

#: Generation states that are not yet reconciled with ComfyUI.
UNRESOLVED_STATUSES = ("submitting", "submission_unknown")


class AuthError(Exception):
    """Base for failures the API layer maps onto an HTTP status."""

    status = 400


class Unauthorized(AuthError):
    status = 401


class Forbidden(AuthError):
    status = 403


class Conflict(AuthError):
    status = 409


class RateLimited(AuthError):
    status = 429


class AdmissionClosed(Conflict):
    """A generation was offered while a session-mode switch held admission shut."""


@dataclass(frozen=True)
class Principal:
    """Who the server believes is calling. ``owner_id`` for every owned query."""

    profile_id: str
    name: str
    is_default: bool
    #: None for the implicit single-user session (no cookie was presented).
    session_token: str | None = None

    @property
    def owner_id(self) -> str:
        return self.profile_id


# --- media baseline gate --------------------------------------------------


@dataclass(frozen=True)
class BaselineStatus:
    ready: bool
    reason: str


def media_baseline_status(db: Database) -> BaselineStatus:
    """Readiness gate for the one-time media baseline scan.

    MEDIA-001 owns the real implementation and replaces this body with a check
    of ``scan_state`` (baseline_complete for every configured root, no pending
    resume cursor). Until then this reports "not ready" so activation fails
    safely and loudly instead of enabling multi-user mode over an unscanned
    library, which would hand Default's un-attributed files to new profiles.
    Do not "temporarily" make this return True; override
    ``AuthService.baseline_gate`` in a test instead.
    """
    return BaselineStatus(
        ready=False,
        reason=(
            "The media baseline scan is not implemented yet (MEDIA-001). "
            "Multi-user mode cannot be enabled until existing output files have "
            "been indexed and assigned to Default."
        ),
    )


# --- admission gate -------------------------------------------------------


class AdmissionGate:
    """Closes generation admission atomically for the duration of a mode switch.

    A submission is either counted as in-flight *before* the gate closes (and
    then waited for), or refused. There is no window in which a submission
    slips past the drain check and still reaches ComfyUI, because closing the
    gate and reading the in-flight count happen under the same lock.

    GEN-001's submit path wraps its work in ``with gate.admit():`` — from the
    atomic request-key reservation through recording the upstream prompt id.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._open = True
        self._in_flight = 0

    @property
    def is_open(self) -> bool:
        with self._cond:
            return self._open

    @contextmanager
    def admit(self) -> Iterator[None]:
        with self._cond:
            if not self._open:
                raise AdmissionClosed(
                    "Generation admission is closed while the session mode changes. Retry shortly."
                )
            self._in_flight += 1
        try:
            yield
        finally:
            with self._cond:
                self._in_flight -= 1
                self._cond.notify_all()

    @contextmanager
    def closed(
        self, reconciled: Callable[[], bool], timeout: float = DRAIN_TIMEOUT_S
    ) -> Iterator[None]:
        """Close admission, wait for the drain, run the body, then reopen.

        ``reconciled`` reports whether persisted submissions have reached a
        terminal or upstream-known state; in-flight requests are tracked here.
        On timeout admission reopens and the caller gets a conflict, so a stuck
        job cannot leave the server permanently refusing generations.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            self._open = False
            try:
                while self._in_flight or not reconciled():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Conflict(
                            "Submissions are still unresolved; the session mode was not changed. "
                            "Wait for running generations to finish and try again."
                        )
                    # ponytail: polled because reconciliation happens in another
                    # component that does not signal this condition. 50ms is
                    # below human perception; give it a notify hook if a
                    # reconciler ever lives in this process's hot path.
                    self._cond.wait(min(remaining, 0.05))
                yield
            finally:
                self._open = True
                self._cond.notify_all()


# --- login throttle -------------------------------------------------------


class LoginThrottle:
    """Per-profile-name failure counter.

    # ponytail: in-memory and per-process. ARCHITECTURE.md gives one backend
    # process ownership of the API, so a shared table would buy nothing today;
    # move it into SQLite if a second process ever serves requests.

    Keyed by profile name rather than client address so that guessing from many
    LAN addresses still trips it. A household member locked out by someone
    else's guessing waits out the window or uses docs/RECOVERY.md.
    """

    def __init__(
        self, max_failures: int = MAX_LOGIN_FAILURES, window_s: int = LOGIN_WINDOW_S
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        kept = [t for t in self._failures.get(key, []) if now - t < self.window_s]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def check(self, key: str) -> None:
        with self._lock:
            if len(self._recent(key.lower(), time.monotonic())) >= self.max_failures:
                raise RateLimited(
                    "Too many failed sign-in attempts. Wait a few minutes and try again."
                )

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            key = key.lower()
            self._failures.setdefault(key, []).append(now)
            self._recent(key, now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key.lower(), None)


# --- service --------------------------------------------------------------


class AuthService:
    def __init__(self, db: Database, settings: SettingsStore | None = None) -> None:
        self.db = db
        self.settings = settings or SettingsStore(db)
        self.admission = AdmissionGate()
        self.throttle = LoginThrottle()
        #: Seam for MEDIA-001 and for tests; see media_baseline_status().
        self.baseline_gate: Callable[[Database], BaselineStatus] = media_baseline_status

    # --- mode -------------------------------------------------------------

    def multi_user_enabled(self) -> bool:
        return bool(self.settings.host_value(MULTI_USER_ENABLED))

    # --- sessions ---------------------------------------------------------

    def resolve(self, token: str | None) -> Principal | None:
        """Principal for a request, or None when it is not authenticated.

        In single-user mode a request with no usable session resolves to
        Default: the app works with no setup. In multi-user mode there is no
        implicit fallback, so every previously anonymous client is unauthenticated
        from the moment activation commits.
        """
        multi_user = self.multi_user_enabled()
        if token:
            row = self.db.query_one(
                "SELECT s.profile_id, s.expires_ms, p.name, p.is_default"
                " FROM sessions s JOIN profiles p ON p.id = s.profile_id"
                " WHERE s.token_hash = ?",
                (token_hash(token),),
            )
            if row is not None:
                if row["expires_ms"] > now_ms():
                    return Principal(
                        profile_id=row["profile_id"],
                        name=row["name"],
                        is_default=bool(row["is_default"]),
                        session_token=token,
                    )
                self._delete_session(token_hash(token))
        if multi_user:
            return None
        profile = self.db.query_one(
            "SELECT id, name, is_default FROM profiles WHERE id = ?",
            (DEFAULT_PROFILE_ID,),
        )
        if profile is None:  # pragma: no cover - migration 001 guarantees it
            return None
        return Principal(
            profile["id"], profile["name"], bool(profile["is_default"]), None
        )

    def login(self, name: str, password: str) -> str:
        if not self.multi_user_enabled():
            raise Conflict("Sign-in is only used in multi-user mode.")
        self.throttle.check(name)
        row = self.db.query_one(
            "SELECT id, password_hash FROM profiles WHERE name = ?", (name,)
        )
        if row is None or not verify_password(password, row["password_hash"]):
            self.throttle.record_failure(name)
            # Same message either way: do not reveal which profile names exist.
            raise Unauthorized("Incorrect profile or password.")
        self.throttle.reset(name)
        return self._create_session(row["id"])

    def logout(self, token: str | None) -> None:
        if token:
            self._delete_session(token_hash(token))

    def _create_session(self, profile_id: str) -> str:
        token = new_session_token()
        stamp = now_ms()
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, profile_id, created_ms, expires_ms)"
                " VALUES (?, ?, ?, ?)",
                (token_hash(token), profile_id, stamp, stamp + SESSION_TTL_MS),
            )
        return token

    def _delete_session(self, hashed: str) -> None:
        with self.db.write() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hashed,))

    def expire_session_now(self, token: str) -> None:
        """Test/recovery helper: make a live session look expired without waiting."""
        with self.db.write() as conn:
            conn.execute(
                "UPDATE sessions SET expires_ms = ? WHERE token_hash = ?",
                (now_ms() - 1, token_hash(token)),
            )

    # --- profiles ---------------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        """Names only: a login picker needs them, and nothing else is exposed."""
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "is_default": bool(row["is_default"]),
            }
            for row in self.db.query(
                "SELECT id, name, is_default FROM profiles ORDER BY is_default DESC, name"
            )
        ]

    def create_profile(self, name: str, password: str) -> str:
        """Local-gate caller only. There is no administrator role to check."""
        if not self.multi_user_enabled():
            raise Conflict("Enable multi-user mode before creating profiles.")
        name = name.strip()
        if not name:
            raise AuthError("A profile name is required.")
        _check_password(password)
        profile_id = new_id()
        try:
            with self.db.write() as conn:
                conn.execute(
                    "INSERT INTO profiles (id, name, password_hash, is_default, created_ms)"
                    " VALUES (?, ?, ?, 0, ?)",
                    (profile_id, name, hash_password(password), now_ms()),
                )
        except IntegrityError as exc:
            raise Conflict(f"A profile named {name!r} already exists.") from exc
        return profile_id

    def change_own_password(self, principal: Principal, current: str, new: str) -> None:
        row = self.db.query_one(
            "SELECT password_hash FROM profiles WHERE id = ?", (principal.profile_id,)
        )
        if row is None:
            raise Unauthorized("Unknown profile.")
        if not verify_password(current, row["password_hash"]):
            raise Unauthorized("Current password is incorrect.")
        _check_password(new)
        with self.db.write() as conn:
            conn.execute(
                "UPDATE profiles SET password_hash = ? WHERE id = ?",
                (hash_password(new), principal.profile_id),
            )
            # Other devices keep working only if the owner wanted that; a
            # password change is the cheap way to evict a forgotten session.
            conn.execute(
                "DELETE FROM sessions WHERE profile_id = ?", (principal.profile_id,)
            )

    # --- mode switch ------------------------------------------------------

    def unresolved_submissions(self) -> int:
        placeholders = ",".join("?" for _ in UNRESOLVED_STATUSES)
        row = self.db.query_one(
            f"SELECT COUNT(*) AS n FROM generations WHERE status IN ({placeholders})",
            UNRESOLVED_STATUSES,
        )
        return int(row["n"]) if row else 0

    def activate_multi_user(
        self, default_password: str, timeout: float = DRAIN_TIMEOUT_S
    ) -> None:
        """Turn on multi-user mode, giving Default the supplied password.

        Order matters: cheap preconditions first, then close admission, then a
        single transaction so a crash cannot leave multi-user mode on with no
        Default password (which would lock the household out of its own data).
        """
        if self.multi_user_enabled():
            raise Conflict("Multi-user mode is already enabled.")
        _check_password(default_password)
        status = self.baseline_gate(self.db)
        if not status.ready:
            raise Conflict(status.reason)

        with self.admission.closed(lambda: self.unresolved_submissions() == 0, timeout):
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE profiles SET password_hash = ? WHERE id = ?",
                    (hash_password(default_password), DEFAULT_PROFILE_ID),
                )
                # Every existing session, anonymous or not, stops here.
                conn.execute("DELETE FROM sessions")
                write_host(conn, MULTI_USER_ENABLED, True)

    def deactivate_multi_user(self, timeout: float = DRAIN_TIMEOUT_S) -> None:
        """Blocked while other profiles exist: their data must not fall into Default."""
        if not self.multi_user_enabled():
            raise Conflict("Multi-user mode is already disabled.")
        extra = self.db.query_one(
            "SELECT COUNT(*) AS n FROM profiles WHERE id != ?", (DEFAULT_PROFILE_ID,)
        )
        if extra and int(extra["n"]) > 0:
            raise Conflict(
                "Remove the other profiles before disabling multi-user mode; otherwise their "
                "workflows, history and media would be reachable from the Default session."
            )
        with self.admission.closed(lambda: self.unresolved_submissions() == 0, timeout):
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE profiles SET password_hash = NULL WHERE id = ?",
                    (DEFAULT_PROFILE_ID,),
                )
                conn.execute("DELETE FROM sessions")
                write_host(conn, MULTI_USER_ENABLED, False)


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
