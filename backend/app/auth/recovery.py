"""Local recovery for a household that has locked itself out.

There is no administrator account to ask for help and no remote reset path, by
design. Recovery is therefore a physical-access operation: whoever can run this
script on the machine already has the database and the ComfyUI output folders,
so this grants nothing they did not have. It is deliberately *not* reachable
over HTTP, not even from loopback.

    cd backend
    .venv\\Scripts\\python -m app.auth.recovery list
    .venv\\Scripts\\python -m app.auth.recovery set-password "Alex"
    .venv\\Scripts\\python -m app.auth.recovery unlock "Alex"
    .venv\\Scripts\\python -m app.auth.recovery disable-multi-user

See docs/RECOVERY.md for the full procedure.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from ..config import Config
from ..settings.service import MULTI_USER_ENABLED, SettingsStore, write_host
from ..storage.db import Database
from ..storage.repository import DEFAULT_PROFILE_ID
from .security import MIN_PASSWORD_LENGTH, hash_password


def open_database(data_dir: Path | None = None) -> Database:
    env = {"SIMPLEUI_DEV": "1"}
    if data_dir is not None:
        env["SIMPLEUI_DATA_DIR"] = str(data_dir)
    return Database(Config.from_env(env).db_path)


def _profile_id(db: Database, name: str) -> str:
    row = db.query_one("SELECT id FROM profiles WHERE name = ?", (name,))
    if row is None:
        raise SystemExit(f"No profile named {name!r}. Run 'list' to see the names.")
    return row["id"]


def _prompt_password() -> str:
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords did not match; nothing was changed.")
    if len(first) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.auth.recovery", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Show profile names and whether they have a password")
    for name, help_text in (
        ("set-password", "Set a profile's password and sign that profile out everywhere"),
        ("unlock", "Clear the in-database sessions for one profile"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("profile")
    sub.add_parser(
        "disable-multi-user",
        help="Return to the single-user Default session (refuses while other profiles exist)",
    )

    args = parser.parse_args(argv)
    db = open_database(args.data_dir)
    try:
        return _run(db, args)
    finally:
        db.close()


def _run(db: Database, args: argparse.Namespace) -> int:
    store = SettingsStore(db)
    if args.command == "list":
        multi_user = bool(store.host_value(MULTI_USER_ENABLED))
        print(f"multi-user mode: {'on' if multi_user else 'off'}")
        for row in db.query(
            "SELECT name, is_default, password_hash IS NOT NULL AS has_password FROM profiles"
            " ORDER BY is_default DESC, name"
        ):
            flags = "default" if row["is_default"] else "profile"
            state = "password set" if row["has_password"] else "no password"
            print(f"  {row['name']}  ({flags}, {state})")
        return 0

    if args.command == "set-password":
        profile_id = _profile_id(db, args.profile)
        password = _prompt_password()
        with db.write() as conn:
            conn.execute(
                "UPDATE profiles SET password_hash = ? WHERE id = ?",
                (hash_password(password), profile_id),
            )
            conn.execute("DELETE FROM sessions WHERE profile_id = ?", (profile_id,))
        print(f"Password updated for {args.profile!r}; its other sessions were signed out.")
        return 0

    if args.command == "unlock":
        profile_id = _profile_id(db, args.profile)
        with db.write() as conn:
            conn.execute("DELETE FROM sessions WHERE profile_id = ?", (profile_id,))
        print(
            f"Sessions cleared for {args.profile!r}. The sign-in rate limit is held in memory, "
            "so restarting the SimpleUI server also clears a throttled profile."
        )
        return 0

    # disable-multi-user
    extra = db.query_one("SELECT COUNT(*) AS n FROM profiles WHERE id != ?", (DEFAULT_PROFILE_ID,))
    if extra and int(extra["n"]) > 0:
        print(
            "Refused: other profiles still exist. Disabling multi-user mode would expose their "
            "workflows, history and media through the Default session. Sign in as each profile "
            "and export or delete its data first.",
            file=sys.stderr,
        )
        return 2
    with db.write() as conn:
        conn.execute(
            "UPDATE profiles SET password_hash = NULL WHERE id = ?", (DEFAULT_PROFILE_ID,)
        )
        conn.execute("DELETE FROM sessions")
        write_host(conn, MULTI_USER_ENABLED, False)
    print("Multi-user mode disabled. Stop and restart the SimpleUI server.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
