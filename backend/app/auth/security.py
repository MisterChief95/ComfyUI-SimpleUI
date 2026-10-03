"""Primitives for the trust boundary: passwords, tokens, CSRF, and locality.

Pure functions plus the request checks that decide whether a call is allowed to
reach a mutation or a host-level setting. Nothing here touches the database, so
every rule below is directly testable.

Two rules this module exists to keep honest:

* **Forwarded headers are never consulted.** This process listens on the same
  machine as ComfyUI and is not behind a trusted reverse proxy by default, so
  ``X-Forwarded-For`` / ``X-Real-IP`` / ``Forwarded`` / ``X-Forwarded-Host`` are
  attacker-controlled strings. Locality comes from the real socket peer, and the
  ``Host`` header is validated rather than trusted. A future proxy deployment
  needs an explicit trusted-proxy configuration, not a relaxation here.
* **Origin is compared against the request's own Host**, so a cross-site page
  cannot forge a state-changing call even though the session cookie is sent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request

# --- passwords ------------------------------------------------------------

# ARCHITECTURE.md asks for Argon2id. The pinned backend lock (requirements.txt)
# has no Argon2 binding and this deployment must install from that lock alone,
# so we use the stdlib's scrypt, which is also memory-hard. Swapping in
# argon2-cffi later only needs a new prefix here: verify_password() dispatches
# on the stored prefix, so existing hashes keep verifying.
_SCRYPT_N = 1 << 14  # ~16 MiB per verification
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
MIN_PASSWORD_LENGTH = 8


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    """Return an encoded ``scrypt$n$r$p$salt$hash`` string. Never store plaintext."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time check. A profile with no hash can never be logged into."""
    if not encoded:
        return False
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(expected)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, base64.b64decode(expected))


# --- session tokens and CSRF ---------------------------------------------

SESSION_COOKIE = "simpleui_session"
CSRF_COOKIE = "simpleui_csrf"
CSRF_HEADER = "x-csrf-token"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Only this lands in the database; a stolen table cannot replay a session."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(secret: bytes, session_token: str) -> str:
    """Derived, not stored: a session's CSRF value needs no extra column."""
    return hmac.new(secret, session_token.encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_matches(secret: bytes, session_token: str, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_token(secret, session_token), presented)


def load_or_create_secret(path: Path) -> bytes:
    """Per-install HMAC key, kept out of the settings tables so no API can read it."""
    if path.is_file():
        raw = path.read_bytes()
        if len(raw) >= 32:
            return raw
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(32)
    # O_EXCL loses harmlessly to a concurrent starter: read whatever won.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return raw


# --- request locality and origin -----------------------------------------

#: Never read. Listed so the intent is greppable and testable.
IGNORED_FORWARDED_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
)


def _split_host(value: str) -> str:
    """Hostname from a Host/netloc value, without port or IPv6 brackets."""
    value = value.strip().lower()
    if value.startswith("["):
        return value.partition("]")[0].lstrip("[")
    return value.partition(":")[0]


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def request_is_local(request: Request) -> bool:
    """True only for a real loopback peer addressing the server as loopback.

    Forwarded headers are ignored by construction, so a LAN client cannot claim
    locality by sending ``X-Forwarded-For: 127.0.0.1``.
    """
    peer = request.client.host if request.client else None
    if not _is_loopback(peer):
        return False
    return _is_loopback(_split_host(request.headers.get("host", "")))


def _normalize_origin(scheme: str, netloc: str) -> str | None:
    scheme = scheme.lower()
    netloc = netloc.strip().lower()
    if not scheme or not netloc:
        return None
    for known, port in (("http", ":80"), ("https", ":443")):
        if scheme == known and netloc.endswith(port):
            netloc = netloc[: -len(port)]
    return f"{scheme}://{netloc}"


def request_origin_ok(request: Request) -> bool:
    """Origin (or Referer) must equal the origin the request itself addressed.

    A missing Origin *and* Referer fails closed: browsers send Origin on the
    state-changing requests this guards, and a non-browser client can set it.
    """
    host = request.headers.get("host")
    if not host:
        return False
    expected = _normalize_origin(request.url.scheme, host)
    if expected is None:
        return False
    for header in ("origin", "referer"):
        raw = request.headers.get(header)
        if not raw:
            continue
        parts = urlsplit(raw)
        return _normalize_origin(parts.scheme, parts.netloc) == expected
    return False
