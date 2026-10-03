"""Behavior checks against real CLI processes and real SQLite files; stdlib only."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

# tests/coordination is two levels below the repository.
SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "agent_coord.py"
module_spec = importlib.util.spec_from_file_location("agent_coord", SCRIPT)
coord = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(coord)


def spec(task, deps=()):
    return dict(
        id=task,
        title=task,
        priority="P0",
        depends_on=list(deps),
        work_areas=["src/"],
        description="Behavior test",
        acceptance=["Observed result"],
    )


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="coord-check-")
        self.root = Path(self.temp.name)
        self.db = self.root / "tasks.sqlite3"
        self.file = self.root / "import.json"
        self.file.write_text(
            json.dumps(
                {"tasks": [spec("A-001"), spec("B-001", ["A-001"]), spec("C-001")]}
            )
        )
        self.handoff = self.root / "handoff.json"
        self.handoff.write_text(
            json.dumps(
                dict(
                    summary="Work verified; editing stopped",
                    next_steps=[],
                    changed_files=["src/a.py"],
                    checks=["Behavior verified"],
                    blockers=[],
                )
            )
        )
        self.call("init", "--journal", "delete")
        self.call("import", "--file", str(self.file))

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args, actor="alice", database=None):
        return [
            sys.executable,
            str(SCRIPT),
            *args,
            "--workspace",
            str(self.root),
            "--db",
            str(database or self.db),
            "--actor",
            actor,
            "--json",
        ]

    def call(self, *args, actor="alice", database=None, code=0):
        process = subprocess.run(
            self.command(*args, actor=actor, database=database),
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["ok"], code == 0)
        if code:
            self.assertEqual(coord.EXIT[result["error"]["code"]], code)
        return result

    def owned(self, command, claim, *args, actor="alice", code=0):
        return self.call(
            command,
            claim["task"]["id"],
            "--revision",
            str(claim["task"]["revision"]),
            "--token",
            claim["lease_token"],
            *args,
            actor=actor,
            code=code,
        )

    def test_process_race_and_dependencies(self):
        self.call("claim", "B-001", code=2)
        processes = [
            subprocess.Popen(
                self.command("claim", "A-001", "--reserve-tree", "src", actor=actor),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for actor in ("alice", "bob")
        ]
        replies = [p.communicate(timeout=15) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2], replies)
        winning = next(
            json.loads(out)
            for p, (out, err) in zip(processes, replies)
            if p.returncode == 0
        )
        actor = winning["task"]["owner"]
        self.owned(
            "complete",
            winning,
            "--file",
            str(self.handoff),
            "--ack-quiescent",
            actor=actor,
        )
        self.call("claim", "B-001", "--reserve-tree", "src")

    def test_path_conflicts_and_atomic_reservations(self):
        first = self.call("claim", "A-001", "--reserve-tree", "Src")
        self.call("claim", "C-001", "--reserve-file", "src/FILE.py", code=2)
        second = self.call("claim", "C-001", "--reserve-file", "other.py")
        self.owned(
            "reserve",
            second,
            "--reserve-file",
            "free.py",
            "--reserve-file",
            "SRC/file.py",
            code=2,
        )
        paths = self.call("show", "C-001")["task"]["reservations"]
        self.assertEqual(len(paths), 1)  # No partially acquired free.py.
        self.owned("reserve", first, "--reserve-tree", "../escape", code=3)
        self.owned("reserve", first, "--reserve-file", "*.py", code=3)
        if os.name == "nt":
            for path in ("src/file.py:stream", "src/file.py.", "NUL"):
                self.owned("reserve", first, "--reserve-file", path, code=3)
        self.owned("release", second, "--reserve-file", "other.py")

    def test_named_reservations_and_claim_next(self):
        first = self.call("claim-next", "--resource", "git-index")
        self.assertEqual(first["task"]["id"], "A-001")
        self.call("claim-next", "--resource", "GIT-INDEX", code=2)
        second = self.call("claim-next", "--reserve-file", "other.py")
        self.assertEqual(second["task"]["id"], "C-001")
        self.assertNotIn("token", self.call("show", "A-001")["task"])

    def test_junction_escape_and_actual_lease_expiry(self):
        with tempfile.TemporaryDirectory(prefix="coord-outside-") as outside:
            link = self.root / "junction"
            if os.name == "nt":
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), outside],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                self.call("claim", "A-001", "--reserve-tree", "junction", code=3)
            finally:
                if os.name == "nt":
                    link.rmdir()  # Removes only this junction, never its target.
                else:
                    link.unlink()
        first = self.call("claim", "A-001", "--lease-seconds", "1")
        time.sleep(1.1)
        self.owned("heartbeat", first, code=2)
        shown = self.call("show", "A-001")["task"]
        self.assertTrue(shown["abandoned"])
        self.assertEqual(shown["status"], "blocked")

    def test_crashed_transaction_rolls_back(self):
        program = """import sqlite3,sys,os
db=sqlite3.connect(sys.argv[1]); db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE tasks SET status='done' WHERE id='A-001'")
db.execute("INSERT INTO events(actor,type,data,timestamp) VALUES ('crash','partial','{}',0)")
os._exit(17)
"""
        process = subprocess.run(
            [sys.executable, "-c", program, str(self.db)], timeout=10
        )
        self.assertEqual(process.returncode, 17)
        self.assertEqual(self.call("show", "A-001")["task"]["status"], "todo")
        self.assertNotIn("partial", [r["type"] for r in self.call("events")["events"]])
        self.call("claim", "A-001")

    def test_expiry_heartbeat_and_safe_reclaim(self):
        first = self.call(
            "claim", "A-001", "--reserve-tree", "src", "--lease-seconds", "2"
        )
        refreshed = self.owned("heartbeat", first, "--lease-seconds", "10")
        self.assertGreater(refreshed["task"]["expires"], first["task"]["expires"])
        self.assertEqual(
            refreshed["task"]["expires"],
            refreshed["task"]["reservations"][0]["expires"],
        )
        first["task"] = refreshed["task"]
        # Fault injection simulates a dead worker without sleeping for a production lease.
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE tasks SET expires=0 WHERE id='A-001'")
            db.execute("UPDATE reservations SET expires=0 WHERE task_id='A-001'")
        abandoned = self.call("show", "A-001")["task"]
        self.assertTrue(abandoned["abandoned"])
        self.owned("note", first, "--message", "stale", code=2)
        self.call("claim", "C-001", "--reserve-file", "src/a.py", code=2)
        self.call("resume", "A-001", "--revision", str(abandoned["revision"]), code=2)
        self.call(
            "reclaim",
            "A-001",
            "--revision",
            str(abandoned["revision"]),
            actor="bob",
            code=3,
        )
        taken = self.call(
            "reclaim",
            "A-001",
            "--revision",
            str(abandoned["revision"]),
            "--ack-quiescent",
            actor="bob",
        )
        self.assertNotEqual(taken["lease_token"], first["lease_token"])
        self.assertEqual(len(taken["task"]["reservations"]), 1)
        self.call(
            "complete",
            "A-001",
            "--token",
            first["lease_token"],
            "--revision",
            str(taken["task"]["revision"]),
            "--file",
            str(self.handoff),
            "--ack-quiescent",
            code=2,
        )
        self.owned(
            "complete",
            taken,
            "--file",
            str(self.handoff),
            "--ack-quiescent",
            actor="bob",
        )

    def test_import_idempotency_cycles_and_amendment(self):
        first = self.call("claim", "A-001")
        self.owned("complete", first, "--file", str(self.handoff), "--ack-quiescent")
        before = self.call("show", "A-001")["task"]
        self.assertEqual(self.call("import", "--file", str(self.file))["added"], [])
        self.assertEqual(self.call("show", "A-001")["task"], before)
        cyclic = self.root / "cycle.json"
        cyclic.write_text(
            json.dumps({"tasks": [spec("D-001", ["E-001"]), spec("E-001", ["D-001"])]})
        )
        self.call("import", "--file", str(cyclic), code=3)
        self.call("show", "D-001", code=5)
        changed = spec("A-001") | {"title": "Revised"}
        cyclic.write_text(json.dumps({"tasks": [changed]}))
        self.call("import", "--file", str(cyclic), code=2)
        cyclic.write_text(json.dumps(changed))
        self.call(
            "amend",
            "A-001",
            "--revision",
            str(before["revision"]),
            "--file",
            str(cyclic),
            code=2,
        )
        amended = self.call(
            "amend",
            "A-001",
            "--revision",
            str(before["revision"]),
            "--file",
            str(cyclic),
            "--ack-unowned",
        )
        self.assertEqual(amended["task"]["status"], "done")
        self.assertEqual(amended["task"]["spec"]["title"], "Revised")

    def test_evidence_revision_and_review_handoffs(self):
        first = self.call("claim", "A-001", "--reserve-file", "src/a.py")
        self.owned("complete", first, "--file", str(self.handoff), code=3)
        self.assertEqual(self.call("show", "A-001")["task"]["handoffs"], [])
        note = self.owned("note", first, "--message", "Verified a finding")
        self.owned(
            "complete", first, "--file", str(self.handoff), "--ack-quiescent", code=2
        )
        first["task"] = note["task"]
        data = json.loads(self.handoff.read_text())
        self.handoff.write_text(json.dumps(data | {"checks": []}))
        self.owned(
            "complete", first, "--file", str(self.handoff), "--ack-quiescent", code=3
        )
        self.handoff.write_text(json.dumps(data))
        reviewed = self.owned(
            "review", first, "--file", str(self.handoff), "--ack-quiescent"
        )
        self.assertEqual(reviewed["task"]["reservations"], [])
        self.call("claim", "B-001", code=2)
        resumed = self.call(
            "resume",
            "A-001",
            "--revision",
            str(reviewed["task"]["revision"]),
            actor="bob",
        )
        final = self.owned(
            "complete",
            resumed,
            "--file",
            str(self.handoff),
            "--ack-quiescent",
            actor="bob",
        )
        self.assertEqual(len(final["task"]["handoffs"]), 2)
        self.assertIn("B-001", [r["id"] for r in self.call("next")["tasks"]])

    def test_block_resume_cancel_and_missing_task(self):
        self.call("show", "MISSING-001", code=5)
        first = self.call("claim", "A-001")
        self.owned(
            "block", first, "--file", str(self.handoff), "--ack-quiescent", code=3
        )
        data = json.loads(self.handoff.read_text())
        self.handoff.write_text(
            json.dumps(data | {"blockers": ["Waiting for fixture"]})
        )
        blocked = self.owned(
            "block", first, "--file", str(self.handoff), "--ack-quiescent"
        )
        resumed = self.call(
            "resume", "A-001", "--revision", str(blocked["task"]["revision"])
        )
        self.owned("cancel", resumed, "--file", str(self.handoff), "--ack-quiescent")
        self.call("claim", "B-001", code=2)

    def test_backup_export_and_non_destructive_migrations(self):
        first = self.call("claim", "A-001")
        self.owned("note", first, "--message", "Persist this event")
        backup = self.root / "backup.sqlite3"
        self.call("backup", "--file", str(backup))
        self.assertEqual(
            self.call("show", "A-001"), self.call("show", "A-001", database=backup)
        )
        self.assertEqual(self.call("events"), self.call("events", database=backup))
        self.call("backup", "--file", str(backup), code=2)
        snapshot = self.root / "export.json"
        self.call("export", "--file", str(snapshot))
        self.assertEqual(len(json.loads(snapshot.read_text())["tasks"]), 3)
        self.call("export", "--file", str(snapshot), code=2)
        # Reconstruct the actual v1 schema by removing v2's indexes, retaining records.
        with closing(sqlite3.connect(backup)) as db, db:
            for index in ("tasks_ready", "events_task", "reservations_task"):
                db.execute(f"DROP INDEX {index}")
            db.execute("PRAGMA user_version=1")
        self.call("init", "--journal", "delete", database=backup)
        self.assertEqual(
            self.call("show", "A-001"), self.call("show", "A-001", database=backup)
        )
        self.assertEqual(
            self.call("events", database=backup)["events"][-1]["type"], "migrate"
        )
        self.call("init", "--journal", "delete", database=backup)
        with closing(sqlite3.connect(backup)) as db, db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            db.execute("PRAGMA user_version=999")
        self.call("init", "--journal", "delete", database=backup, code=4)

    def test_lock_failure_is_bounded_and_json_errors(self):
        program = """import sqlite3,sys
db=sqlite3.connect(sys.argv[1]); db.execute('BEGIN IMMEDIATE')
print('locked',flush=True); sys.stdin.readline(); db.rollback()
"""
        holder = subprocess.Popen(
            [sys.executable, "-c", program, str(self.db)],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            start = time.monotonic()
            self.call("claim", "A-001", "--busy-timeout", "50", code=4)
            self.assertLess(time.monotonic() - start, 3)
            self.call(
                "show", "A-001"
            )  # Readers still work during a reserved write lock.
        finally:
            holder.communicate("release\n", timeout=10)
        self.call("claim", "A-001")
        self.call("note", "A-001", code=3)

    def test_wal_runtime_gate(self):
        self.assertFalse(coord.wal_safe((3, 50, 4)))
        self.assertTrue(coord.wal_safe((3, 50, 7)))
        self.assertTrue(coord.wal_safe((3, 44, 6)))
        self.assertTrue(coord.wal_safe((3, 51, 3)))
        result = self.call(
            "init",
            "--journal",
            "wal",
            code=0 if coord.wal_safe(sqlite3.sqlite_version_info) else 4,
        )
        if result["ok"]:
            self.assertEqual(result["journal"], "wal")

    def test_human_task_views_and_structured_output(self):
        def plain(*args, code=0):
            command = self.command(*args)
            command.remove("--json")
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            return result

        listing = plain("list").stdout
        self.assertIn("TITLE", listing)
        self.assertIn("B-001", listing)
        self.assertNotIn("acceptance", listing)
        self.assertNotIn("B-001", plain("next").stdout)
        self.assertIn("No matching tasks.", plain("list", "--status", "done").stdout)
        detail = plain("show", "B-001").stdout
        self.assertIn("Depends on: A-001", detail)
        self.assertIn("Acceptance:", detail)
        self.assertIn("Observed result", detail)
        claimed = plain("claim", "A-001").stdout
        self.assertIn("Revision: 1", claimed)
        self.assertIn("Lease token:", claimed)
        self.assertIn("UTC", claimed)  # Human UTC expiry rather than an epoch float.
        self.assertIn("claim", plain("events").stdout)
        self.assertEqual(len(json.loads(plain("export").stdout)["tasks"]), 3)
        self.assertEqual(len(self.call("list")["tasks"]), 3)
        error = plain("show", "MISSING-001", code=5)
        self.assertEqual(error.stdout, "")
        self.assertIn("Error (not_found):", error.stderr)

    def test_invalid_import_and_amend_are_atomic(self):
        bad = self.root / "bad.json"
        for payload in (
            {"tasks": [spec("D-001"), spec("D-001")]},
            {"tasks": [spec("D-001", ["MISSING-001"])]},
            {"tasks": [spec("D-001", ["D-001"])]},
        ):
            bad.write_text(json.dumps(payload))
            self.call("import", "--file", str(bad), code=3)
            self.call("show", "D-001", code=5)
        bad.write_text('{"tasks":[],"tasks":[]}')
        self.call("import", "--file", str(bad), code=3)
        before = self.call("show", "A-001")["task"]
        bad.write_text(json.dumps(spec("A-001", ["B-001"])))
        self.call(
            "amend",
            "A-001",
            "--revision",
            str(before["revision"]),
            "--ack-unowned",
            "--file",
            str(bad),
            code=3,
        )
        self.assertEqual(self.call("show", "A-001")["task"], before)
        claim = self.call("claim", "A-001")
        bad.write_text(json.dumps(spec("A-001") | {"title": "Owner amendment"}))
        self.call(
            "amend",
            "A-001",
            "--revision",
            str(claim["task"]["revision"]),
            "--ack-unowned",
            "--file",
            str(bad),
            code=2,
        )
        changed = self.owned("amend", claim, "--file", str(bad))
        self.assertEqual(changed["task"]["spec"]["title"], "Owner amendment")


if __name__ == "__main__":
    unittest.main()
