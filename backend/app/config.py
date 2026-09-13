"""Local configuration, read from the environment and validated at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Raised when local configuration is unusable. Message lists every problem."""


@dataclass(frozen=True)
class Config:
    comfy_url: str
    data_dir: Path
    static_dir: Path
    dev_mode: bool

    @property
    def spa_index(self) -> Path:
        return self.static_dir / "index.html"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.sqlite3"

    @property
    def session_secret_path(self) -> Path:
        """Per-install HMAC key for CSRF tokens; deliberately not a setting row."""
        return self.data_dir / "session_secret"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        problems: list[str] = []

        comfy_url = env.get("SIMPLEUI_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
        parsed = urlparse(comfy_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            problems.append(
                f"SIMPLEUI_COMFY_URL must be an http(s) URL with a host, got {comfy_url!r}"
            )

        data_dir = Path(env.get("SIMPLEUI_DATA_DIR", REPO_ROOT / "data")).resolve()
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"SIMPLEUI_DATA_DIR {data_dir} is not usable: {exc}")

        static_dir = Path(
            env.get("SIMPLEUI_STATIC_DIR", REPO_ROOT / "frontend" / "build")
        ).resolve()
        dev_mode = env.get("SIMPLEUI_DEV", "").lower() in ("1", "true", "yes")
        if not dev_mode and not (static_dir / "index.html").is_file():
            problems.append(
                f"No built UI at {static_dir}. Run 'npm run build' in frontend/, "
                "or set SIMPLEUI_DEV=1 to run the API without static files."
            )

        if problems:
            raise ConfigError("; ".join(problems))
        return cls(
            comfy_url=comfy_url,
            data_dir=data_dir,
            static_dir=static_dir,
            dev_mode=dev_mode,
        )
