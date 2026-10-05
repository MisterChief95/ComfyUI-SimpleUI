"""Storage checks, one per DATA-001 acceptance criterion.

Run from backend/:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from app.storage import DEFAULT_PROFILE_ID, Database, Repository, in_thread
from app.storage.db import MIGRATIONS_DIR, StorageError

OWNER = DEFAULT_PROFILE_ID


class StorageTestCase(unittest.TestCase):
    """A fresh migrated database per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "app.sqlite3"
        self.db = Database(self.db_path)
        self.repo = Repository(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def reopen(self) -> Repository:
        """Simulate a process restart against the same file."""
        self.db.close()
        self.db = Database(self.db_path)
        self.repo = Repository(self.db)
        return self.repo


class DurabilityTest(StorageTestCase):
    def test_data_survives_a_restart(self) -> None:
        workflow_id = self.repo.create_workflow(
            OWNER, "Portrait", {"1": {"class_type": "KSampler"}}
        )
        generation_id = self.repo.create_generation(
            OWNER,
            client_request_key="k1",
            request_fingerprint="f1",
            workflow_id=workflow_id,
            workflow_revision=1,
            graph={"1": {"class_type": "KSampler"}},
        )

        repo = self.reopen()

        self.assertEqual(repo.get_workflow(OWNER, workflow_id)["name"], "Portrait")
        self.assertEqual(
            repo.get_generation(OWNER, generation_id)["status"], "submitting"
        )
        self.assertEqual(
            repo.get_workflow_graph(OWNER, workflow_id, 1),
            {"1": {"class_type": "KSampler"}},
        )


class ForeignKeyTest(StorageTestCase):
    def test_unknown_owner_is_refused(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create_workflow("no-such-profile", "Orphan", {})

    def test_foreign_keys_stay_on_after_restart(self) -> None:
        self.reopen()
        self.assertEqual(self.db.query_one("PRAGMA foreign_keys")[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create_generation(
                "no-such-profile", client_request_key="k", request_fingerprint="f"
            )

    def test_cascade_removes_dependent_rows(self) -> None:
        other = self.repo.create_profile("Second")
        workflow_id = self.repo.create_workflow(other, "Theirs", {"1": {}})
        with self.db.write() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (other,))
        self.assertIsNone(self.repo.get_workflow(other, workflow_id))
        self.assertEqual(
            self.db.query_one(
                "SELECT COUNT(*) AS n FROM workflow_revisions WHERE workflow_id = ?",
                (workflow_id,),
            )["n"],
            0,
        )


class MigrationTest(StorageTestCase):
    def test_audio_kind_upgrade_keeps_existing_media_and_membership(self) -> None:
        migrations = self.root / "audio-migrations"
        migrations.mkdir()
        for script in MIGRATIONS_DIR.glob("*.sql"):
            if int(script.name[:3]) < 10:
                shutil.copy(script, migrations / script.name)
        path = self.root / "audio-upgrade.sqlite3"
        old = Database(path, migrations_dir=migrations)
        repo = Repository(old)
        media_id = repo.record_media(
            OWNER,
            storage_path="existing.png",
            file_version="existing",
            media_kind="image",
            media_type="image/png",
        )
        collection_id = repo.save_collection(OWNER, "Existing")["id"]
        self.assertEqual(repo.collection_members(OWNER, collection_id, [media_id]), 1)
        old.close()

        upgraded = Database(path)
        try:
            repo = Repository(upgraded)
            self.assertEqual(
                repo.get_media(OWNER, media_id)["storage_path"], "existing.png"
            )
            self.assertEqual(
                repo.list_media(OWNER).items[0]["collections"][0]["id"], collection_id
            )
            audio_id = repo.record_media(
                OWNER,
                storage_path="voice.wav",
                file_version="audio",
                media_kind="audio",
                media_type="audio/wav",
            )
            self.assertEqual(repo.get_media(OWNER, audio_id)["media_kind"], "audio")
            self.assertEqual(upgraded.query("PRAGMA foreign_key_check"), [])
        finally:
            upgraded.close()

    def test_collections_upgrade_preserves_existing_media_and_restarts(self) -> None:
        migrations = self.root / "collections-migrations"
        migrations.mkdir()
        for script in MIGRATIONS_DIR.glob("*.sql"):
            if int(script.name[:3]) <= 4:
                shutil.copy(script, migrations / script.name)
        path = self.root / "collections-upgrade.sqlite3"
        old = Database(path, migrations_dir=migrations)
        media_id = Repository(old).record_media(
            OWNER,
            storage_path="existing.png",
            file_version="existing",
            media_kind="image",
            media_type="image/png",
        )
        old.close()
        shutil.copy(
            MIGRATIONS_DIR / "005_collections.sql", migrations / "005_collections.sql"
        )
        upgraded = Database(path, migrations_dir=migrations)
        try:
            repo = Repository(upgraded)
            self.assertEqual(upgraded.schema_version(), 5)
            self.assertEqual(
                repo.get_media(OWNER, media_id)["storage_path"], "existing.png"
            )
            cid = repo.save_collection(OWNER, "Existing")["id"]
            self.assertEqual(repo.collection_members(OWNER, cid, [media_id]), 1)
            self.assertEqual(upgraded.migrate(), [])
        finally:
            upgraded.close()
        restarted = Database(path, migrations_dir=migrations)
        self.addCleanup(restarted.close)
        self.assertEqual(Repository(restarted).list_collections(OWNER)[0]["count"], 1)
        self.assertEqual(restarted.query("PRAGMA foreign_key_check"), [])

    def test_shipped_migrations_record_their_versions(self) -> None:
        # Read from the directory, so shipping 003_*.sql is not a test edit.
        latest = max(int(script.name[:3]) for script in MIGRATIONS_DIR.glob("*.sql"))
        self.assertEqual(self.db.schema_version(), latest)
        applied = self.db.migrate()  # re-running applies nothing
        self.assertEqual(applied, [])
        self.assertEqual(self.db.schema_version(), latest)

    def test_a_later_migration_applies_to_an_existing_database(self) -> None:
        migrations = self.root / "migrations"
        migrations.mkdir()
        shutil.copy(MIGRATIONS_DIR / "001_initial.sql", migrations / "001_initial.sql")

        db_path = self.root / "upgrade.sqlite3"
        first = Database(db_path, migrations_dir=migrations)
        first.query(
            "INSERT INTO settings (scope, owner_id, key, value_json, schema_version,"
            " updated_ms) VALUES ('host', NULL, 'theme', '\"dark\"', 1, 1)"
        )
        self.assertEqual(first.schema_version(), 1)
        first.close()

        (migrations / "002_add_media_note.sql").write_text(
            "ALTER TABLE media ADD COLUMN note TEXT;", encoding="utf-8"
        )
        second = Database(db_path, migrations_dir=migrations)
        self.addCleanup(second.close)

        self.assertEqual(second.schema_version(), 2)
        self.assertEqual(
            second.query_one("SELECT value_json FROM settings WHERE key = 'theme'")[
                "value_json"
            ],
            '"dark"',
        )
        self.assertIn(
            "note", {row["name"] for row in second.query("PRAGMA table_info(media)")}
        )

    def test_a_failing_migration_leaves_the_previous_version(self) -> None:
        migrations = self.root / "migrations"
        migrations.mkdir()
        shutil.copy(MIGRATIONS_DIR / "001_initial.sql", migrations / "001_initial.sql")
        (migrations / "002_broken.sql").write_text(
            "CREATE TABLE ok_so_far (id TEXT); SELECT this_is_not_valid_sql(;",
            encoding="utf-8",
        )
        db_path = self.root / "broken.sqlite3"
        with self.assertRaises(sqlite3.Error):
            Database(db_path, migrations_dir=migrations)

        # The failed migration rolled back whole: version 1, no partial table.
        (migrations / "002_broken.sql").unlink()
        repaired = Database(db_path, migrations_dir=migrations)
        self.addCleanup(repaired.close)
        self.assertEqual(repaired.schema_version(), 1)
        self.assertEqual(
            repaired.query_one(
                "SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'ok_so_far'"
            )["n"],
            0,
        )

    def test_migration_files_must_be_numbered(self) -> None:
        migrations = self.root / "bad-names"
        migrations.mkdir()
        (migrations / "initial.sql").write_text("SELECT 1;", encoding="utf-8")
        with self.assertRaises(StorageError):
            Database(self.root / "bad.sqlite3", migrations_dir=migrations)


class DefaultProfileTest(StorageTestCase):
    def test_default_exists_once_across_restarts(self) -> None:
        for _ in range(3):
            self.reopen()
        rows = self.db.query("SELECT id, name FROM profiles WHERE is_default = 1")
        self.assertEqual(
            [(r["id"], r["name"]) for r in rows], [(DEFAULT_PROFILE_ID, "Default")]
        )
        self.assertEqual(
            self.db.query_one("SELECT COUNT(*) AS n FROM profiles")["n"], 1
        )

    def test_a_second_default_is_refused_by_the_database(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError), self.db.write() as conn:
            conn.execute(
                "INSERT INTO profiles (id, name, is_default, created_ms)"
                " VALUES ('other', 'Default 2', 1, 0)"
            )
        self.assertEqual(
            self.db.query_one(
                "SELECT COUNT(*) AS n FROM profiles WHERE is_default = 1"
            )["n"],
            1,
        )


class BackupRestoreTest(StorageTestCase):
    def test_round_trip_preserves_ids_and_ownership(self) -> None:
        owner_b = self.repo.create_profile("Second")
        mine = self.repo.create_workflow(OWNER, "Mine", {"1": {}})
        theirs = self.repo.create_workflow(owner_b, "Theirs", {"2": {}})

        backup_path = self.db.backup(self.root / "backup.sqlite3")

        # Diverge after the snapshot.
        with self.db.write() as conn:
            conn.execute("DELETE FROM workflows WHERE id = ?", (mine,))
        later = self.repo.create_workflow(OWNER, "After backup", {"3": {}})
        self.assertIsNone(self.repo.get_workflow(OWNER, mine))

        self.db.restore(backup_path)

        self.assertEqual(self.repo.get_workflow(OWNER, mine)["name"], "Mine")
        self.assertEqual(self.repo.get_workflow(owner_b, theirs)["owner_id"], owner_b)
        self.assertIsNone(self.repo.get_workflow(OWNER, later))
        # Restored content is durable, not just cached in this connection.
        self.assertEqual(self.reopen().get_workflow(OWNER, mine)["name"], "Mine")

    def test_backup_refuses_to_overwrite(self) -> None:
        target = self.root / "existing.sqlite3"
        target.write_bytes(b"")
        with self.assertRaises(StorageError):
            self.db.backup(target)


class OwnerScopeTest(StorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_b = self.repo.create_profile("Second")

    def test_another_profile_sees_nothing(self) -> None:
        workflow_id = self.repo.create_workflow(OWNER, "Mine", {"1": {}})
        generation_id = self.repo.create_generation(
            OWNER,
            client_request_key="k",
            request_fingerprint="f",
            workflow_id=workflow_id,
        )
        media_id = self.repo.record_media(
            OWNER,
            storage_path="out/a.png",
            file_version="v1",
            media_kind="image",
            media_type="image/png",
            generation_id=generation_id,
        )

        self.assertIsNone(self.repo.get_workflow(self.owner_b, workflow_id))
        self.assertIsNone(self.repo.get_generation(self.owner_b, generation_id))
        self.assertIsNone(self.repo.get_media(self.owner_b, media_id))
        self.assertIsNone(self.repo.get_workflow_graph(self.owner_b, workflow_id, 1))
        self.assertEqual(self.repo.list_workflows(self.owner_b).items, [])
        self.assertEqual(self.repo.list_media(self.owner_b).items, [])

    def test_updates_cannot_cross_owners(self) -> None:
        generation_id = self.repo.create_generation(
            OWNER, client_request_key="k", request_fingerprint="f"
        )
        self.assertFalse(
            self.repo.update_generation_status(
                self.owner_b, generation_id, status="cancelled"
            )
        )
        self.assertFalse(
            self.repo.purge_generation_snapshot(self.owner_b, generation_id)
        )
        self.assertEqual(
            self.repo.get_generation(OWNER, generation_id)["status"], "submitting"
        )


class PaginationTest(StorageTestCase):
    def _make_media(self, label: str, created_ms: int) -> str:
        media_id = self.repo.record_media(
            OWNER,
            storage_path=f"out/{label}.png",
            file_version=label,
            media_kind="image",
            media_type="image/png",
        )
        with self.db.write() as conn:
            conn.execute(
                "UPDATE media SET created_ms = ? WHERE id = ?", (created_ms, media_id)
            )
        return media_id

    def test_pages_are_stable_while_rows_are_inserted(self) -> None:
        original = [self._make_media(f"m{i}", 1000 + i) for i in range(6)]

        first = self.repo.list_media(OWNER, limit=2)
        self.assertEqual([i["id"] for i in first.items], original[5:3:-1])
        self.assertIsNotNone(first.next_cursor)

        # Concurrent inserts land at the head of a newest-first list; they must
        # not shift already-read rows onto the next page or skip any.
        self._make_media("new1", 2000)
        self._make_media("new2", 2001)

        seen: list[str] = [item["id"] for item in first.items]
        cursor = first.next_cursor
        while cursor:
            page = self.repo.list_media(OWNER, cursor=cursor, limit=2)
            seen += [item["id"] for item in page.items]
            cursor = page.next_cursor

        self.assertEqual(len(seen), len(set(seen)), "a row was returned twice")
        self.assertEqual(seen, original[::-1], "paging lost or reordered a row")

    def test_identical_timestamps_still_page_exactly_once(self) -> None:
        ids = [self._make_media(f"same{i}", 5000) for i in range(5)]
        seen: list[str] = []
        cursor: str | None = None
        while True:
            page = self.repo.list_media(OWNER, cursor=cursor, limit=2)
            seen += [item["id"] for item in page.items]
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual(sorted(seen), sorted(ids))

    def test_a_malformed_cursor_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.repo.list_media(OWNER, cursor="not-a-cursor!!")


class IdempotentWriteTest(StorageTestCase):
    def test_repeated_request_key_returns_the_same_generation(self) -> None:
        first = self.repo.create_generation(
            OWNER, client_request_key="k1", request_fingerprint="f1"
        )
        again = self.repo.create_generation(
            OWNER, client_request_key="k1", request_fingerprint="f1"
        )
        self.assertEqual(first, again)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.create_generation(
                OWNER, client_request_key="k1", request_fingerprint="CHANGED"
            )

    def test_replayed_result_does_not_duplicate_a_gallery_card(self) -> None:
        generation_id = self.repo.create_generation(
            OWNER, client_request_key="k", request_fingerprint="f"
        )
        kwargs = {
            "storage_path": "out/a.png",
            "file_version": "v1",
            "media_kind": "image",
            "media_type": "image/png",
            "generation_id": generation_id,
            "output_node": "9",
            "ordinal": 0,
        }
        first = self.repo.record_media(OWNER, **kwargs)
        self.assertEqual(self.repo.record_media(OWNER, **kwargs), first)
        self.assertEqual(len(self.repo.list_media(OWNER).items), 1)


class EventLoopTest(unittest.IsolatedAsyncioTestCase):
    """Blocking storage calls must not stall the async event loop."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self._tmp.name) / "app.sqlite3")
        self.repo = Repository(self.db)

    async def asyncTearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _slow_storage_call(self) -> int:
        """~0.3s of real blocking database work on a worker thread."""
        deadline = time.monotonic() + 0.3
        calls = 0
        while time.monotonic() < deadline:
            self.repo.list_media(OWNER, limit=1)
            calls += 1
        return calls

    async def test_loop_keeps_running_during_a_blocking_call(self) -> None:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        calls = await in_thread(self._slow_storage_call)
        task.cancel()

        self.assertGreater(calls, 0)
        self.assertGreater(ticks, 5, "the event loop was blocked by storage work")

    async def test_offloaded_writes_are_visible_to_the_loop_thread(self) -> None:
        workflow_id = await in_thread(
            self.repo.create_workflow, OWNER, "Async", {"1": {}}
        )
        found = await in_thread(self.repo.get_workflow, OWNER, workflow_id)
        self.assertEqual(found["name"], "Async")


if __name__ == "__main__":
    unittest.main()
