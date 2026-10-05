"""SQLite connection handling, explicit migrations, and backup/restore.

Rules this module exists to enforce:

* One connection per thread. FastAPI handlers must not touch a connection from
  the event loop thread; call blocking work through :func:`in_thread`.
* Foreign keys on, bounded busy timeout, ``BEGIN IMMEDIATE`` for writes.
* Transactions stay short: open one around related row changes only, never
  around network calls, filesystem scans, or thumbnailing.
* Schema changes are numbered ``.sql`` files in backend/migrations/, applied in
  order and recorded in ``schema_migrations``. Adding 002_*.sql later is the
  whole upgrade procedure; applied files are never edited.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, TypeVar

import anyio.to_thread

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")

#: The coordination guide's constraint applies to this database too: bundled
#: SQLite here lacks the WAL-reset fix, so we use the tested rollback journal.
#: Pass journal="wal" only on a runtime verified per docs/AGENT_COORDINATION.md.
DEFAULT_JOURNAL = "delete"

T = TypeVar("T")


class StorageError(Exception):
    """Raised when the database cannot be opened, migrated, or restored."""


class Database:
    """A migrated application database with per-thread connections."""

    def __init__(
        self,
        path: Path | str,
        *,
        journal: str = DEFAULT_JOURNAL,
        busy_timeout_ms: int = 5000,
        migrations_dir: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._journal = journal
        self._busy_timeout_ms = busy_timeout_ms
        self._migrations_dir = migrations_dir or MIGRATIONS_DIR
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        try:
            self.migrate()
        except BaseException:
            self.close()  # do not leak a connection to an unusable database
            raise

    # --- connections ------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """Return this thread's connection, opening it on first use."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def _open(self) -> sqlite3.Connection:
        # check_same_thread=False does NOT mean connections are shared: each is
        # still used by exactly one thread (see connect()). It only lets
        # close() reclaim worker-thread connections at shutdown.
        conn = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys = ON")
        mode = conn.execute(f"PRAGMA journal_mode = {self._journal}").fetchone()[0]
        if mode.lower() != self._journal.lower():
            conn.close()
            raise StorageError(
                f"Requested journal mode {self._journal!r} but SQLite selected {mode!r}"
            )
        return conn

    def close(self) -> None:
        """Close every connection this object opened. Tests and shutdown only."""
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()
        self._local = threading.local()

    # --- transactions -----------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Short write transaction. Do no blocking I/O inside this block."""
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.connect().execute(sql, params).fetchone()

    # --- migrations -------------------------------------------------------

    def migrate(self) -> list[int]:
        """Apply pending migrations in order. Returns the versions applied."""
        done: list[int] = []
        # Its own short-lived connection in autocommit=False mode: under the
        # legacy transaction control the rest of this module uses,
        # executescript() commits any open transaction before it runs, which
        # would defeat a per-migration rollback.
        with closing(self._open()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_ms INTEGER NOT NULL)"
            )
            applied = {
                row[0] for row in conn.execute("SELECT version FROM schema_migrations")
            }
            conn.autocommit = False
            for version, script in _migration_files(self._migrations_dir):
                if version in applied:
                    continue
                source = script.read_text(encoding="utf-8")
                if source.startswith("-- requires_foreign_keys_off\n"):
                    # A table rebuild can preserve child rows only while FK
                    # enforcement is disabled outside the transaction.
                    conn.commit()
                    conn.autocommit = True
                    conn.execute("PRAGMA foreign_keys = OFF")
                    try:
                        conn.executescript(
                            "BEGIN IMMEDIATE;\n"
                            + source.split("\n", 1)[1]
                            + "\nCOMMIT;"
                        )
                    except BaseException:
                        if conn.in_transaction:
                            conn.rollback()
                        raise
                    finally:
                        conn.execute("PRAGMA foreign_keys = ON")
                        conn.autocommit = False
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_ms)"
                        " VALUES (?, ?, unixepoch() * 1000)",
                        (version, script.name),
                    )
                    conn.commit()
                    done.append(version)
                    continue
                # One transaction per migration: a failure leaves the schema at
                # the previous version rather than half-upgraded.
                try:
                    conn.executescript(source)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_ms)"
                        " VALUES (?, ?, unixepoch() * 1000)",
                        (version, script.name),
                    )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                done.append(version)
            conn.commit()
        return done

    def schema_version(self) -> int:
        row = self.query_one("SELECT MAX(version) AS v FROM schema_migrations")
        return (row["v"] if row else None) or 0

    # --- backup and restore ----------------------------------------------

    def backup(self, destination: Path | str) -> Path:
        """Consistent copy via SQLite's own backup API (never a raw file copy)."""
        destination = Path(destination)
        if destination.exists():
            raise StorageError(f"Backup destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(destination)) as target:
            self.connect().backup(target)
        return destination

    def restore(self, source: Path | str) -> None:
        """Replace this database's contents from a backup, preserving all IDs.

        Every other worker must be stopped first; this overwrites live data.
        """
        source = Path(source)
        if not source.is_file():
            raise StorageError(f"No backup file at {source}")
        with closing(sqlite3.connect(source)) as src:
            src.backup(self.connect())
        self.migrate()


def _migration_files(directory: Path) -> list[tuple[int, Path]]:
    if not directory.is_dir():
        raise StorageError(f"No migrations directory at {directory}")
    found: list[tuple[int, Path]] = []
    for script in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.match(script.name)
        if not match:
            raise StorageError(
                f"Migration {script.name} must be named NNN_lower_snake.sql"
            )
        found.append((int(match.group(1)), script))
    versions = [version for version, _ in found]
    if len(set(versions)) != len(versions):
        raise StorageError(f"Duplicate migration numbers in {directory}")
    return found


async def in_thread(fn: Callable[..., T], *args: Any) -> T:
    """Run a blocking storage call off the event loop (FastAPI's own idiom)."""
    return await anyio.to_thread.run_sync(fn, *args)
