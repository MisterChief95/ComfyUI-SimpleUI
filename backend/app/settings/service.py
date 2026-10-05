"""Settings storage and the host/profile scope split.

Two scopes, from ARCHITECTURE.md "UI and settings":

* **host** — machine-wide configuration (ComfyUI connection and folders, upload
  limits, pending cap, catalog cooldown, backup location). Writable only
  through the local gate, never from an arbitrary LAN session.
* **profile** — one profile's own preferences (appearance, generation, gallery,
  history). Owner comes from the session; a request body cannot name another
  profile.

The registry below is the whole validation layer: an unknown key is rejected
rather than stored, so a typo cannot silently become a setting that nothing
reads. Values are stored as JSON text in the ``settings`` table from
migration 001.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..storage.db import Database
from ..storage.repository import now_ms

SCHEMA_VERSION = 1

#: Host key written only by the multi-user activation flow (auth/service.py),
#: never by PUT /api/settings/host: enabling it has preconditions.
MULTI_USER_ENABLED = "multi_user_enabled"


@dataclass(frozen=True)
class Spec:
    kind: type | tuple[str, ...]
    default: Any
    writable: bool = True


HOST_SETTINGS: dict[str, Spec] = {
    MULTI_USER_ENABLED: Spec(bool, False, writable=False),
    "comfy_url": Spec(str, "http://127.0.0.1:8188"),
    "comfy_input_dir": Spec(str, ""),
    "comfy_output_dir": Spec(str, ""),
    "upload_max_bytes": Spec(int, 268_435_456),
    "pending_cap": Spec(int, 8),
    "catalog_refresh_min_seconds": Spec(int, 30),
    "backup_dir": Spec(str, ""),
}

PROFILE_SETTINGS: dict[str, Spec] = {
    "theme": Spec(("system", "light", "dark"), "system"),
    "density": Spec(("comfortable", "compact"), "comfortable"),
    "show_advanced": Spec(bool, False),
    "seed_policy": Spec(("fixed", "random", "increment"), "random"),
    "live_previews": Spec(bool, True),
    "video_enabled": Spec(bool, True),
    "completion_sound": Spec(bool, False),
    "clear_generation_on_startup": Spec(bool, False),
    "clear_generation_on_generate": Spec(bool, False),
    "thumbnail_size": Spec(("small", "medium", "large"), "medium"),
    "gallery_autoplay": Spec(bool, False),
    "gallery_page_size": Spec(int, 50),
    "store_history": Spec(bool, True),
}


class SettingsError(ValueError):
    """Unknown key, wrong scope, read-only key, or a value the spec rejects."""


def _validate(registry: dict[str, Spec], key: str, value: Any) -> Any:
    spec = registry.get(key)
    if spec is None:
        raise SettingsError(f"Unknown setting {key!r}")
    if not spec.writable:
        raise SettingsError(f"Setting {key!r} is not writable through this API")
    if isinstance(spec.kind, tuple):
        if value not in spec.kind:
            raise SettingsError(f"{key!r} must be one of {', '.join(spec.kind)}")
    elif spec.kind is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"{key!r} must be a boolean")
    elif spec.kind is int:
        # bool is an int subclass; reject it explicitly.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SettingsError(f"{key!r} must be a non-negative integer")
    elif not isinstance(value, str):
        raise SettingsError(f"{key!r} must be a string")
    return value


class SettingsStore:
    """Blocking settings access. Call through ``in_thread`` from async code."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # --- host scope -------------------------------------------------------

    def host(self) -> dict[str, Any]:
        stored = {
            row["key"]: json.loads(row["value_json"])
            for row in self.db.query(
                "SELECT key, value_json FROM settings WHERE scope = 'host'"
            )
        }
        return {
            key: stored.get(key, spec.default) for key, spec in HOST_SETTINGS.items()
        }

    def host_value(self, key: str) -> Any:
        row = self.db.query_one(
            "SELECT value_json FROM settings WHERE scope = 'host' AND owner_id IS NULL AND key = ?",
            (key,),
        )
        if row is None:
            return HOST_SETTINGS[key].default
        return json.loads(row["value_json"])

    def set_host(self, key: str, value: Any) -> None:
        _validate(HOST_SETTINGS, key, value)
        with self.db.write() as conn:
            write_host(conn, key, value)

    # --- profile scope ----------------------------------------------------

    def profile(self, owner_id: str) -> dict[str, Any]:
        stored = {
            row["key"]: json.loads(row["value_json"])
            for row in self.db.query(
                "SELECT key, value_json FROM settings WHERE scope = 'profile' AND owner_id = ?",
                (owner_id,),
            )
        }
        return {
            key: stored.get(key, spec.default) for key, spec in PROFILE_SETTINGS.items()
        }

    def set_profile(self, owner_id: str, key: str, value: Any) -> None:
        """``owner_id`` always comes from the session, never from the request body."""
        _validate(PROFILE_SETTINGS, key, value)
        with self.db.write() as conn:
            conn.execute(
                "INSERT INTO settings (scope, owner_id, key, value_json, schema_version, updated_ms)"
                " VALUES ('profile', ?, ?, ?, ?, ?)"
                " ON CONFLICT (scope, IFNULL(owner_id, ''), key)"
                " DO UPDATE SET value_json = excluded.value_json, updated_ms = excluded.updated_ms",
                (owner_id, key, json.dumps(value), SCHEMA_VERSION, now_ms()),
            )


def write_host(conn: Any, key: str, value: Any) -> None:
    """Host write inside a caller's transaction (activation changes several rows)."""
    conn.execute(
        "INSERT INTO settings (scope, owner_id, key, value_json, schema_version, updated_ms)"
        " VALUES ('host', NULL, ?, ?, ?, ?)"
        " ON CONFLICT (scope, IFNULL(owner_id, ''), key)"
        " DO UPDATE SET value_json = excluded.value_json, updated_ms = excluded.updated_ms",
        (key, json.dumps(value), SCHEMA_VERSION, now_ms()),
    )
