# Local agent coordination

The installed **dibs** plugin owns coordination. The live task/status source is
`.dibs/tasks.sqlite3`. Planning documents and exports do not override it.
Application metadata uses a separate database.

Read the installed dibs `SKILL.md` for the full current contract. Resolve
`<DIBS_SCRIPT>` to `../../scripts/dibs.py` relative to that skill; currently:
`C:/Users/brend/.codex/plugins/cache/dibs/dibs/1.4.0/scripts/dibs.py`.
Do not copy the script into the repository. Python 3.9+ and standard-library SQLite
are sufficient. Check the interpreter before the first command.

```cmd
python -c "import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"
python "<DIBS_SCRIPT>" next --workspace "." --actor "agent-compat" --json
python "<DIBS_SCRIPT>" show COMPAT-001 --workspace "." --actor "agent-compat" --json
```

## Ownership and reservations

Run from the repository root. Pass the same workspace and optional database path to
every worker, and use a distinct stable actor. Common options follow the command.
Prefer JSON for mutations; retain the returned token privately and carry forward
the latest revision. Do not assume shell environment persists.

Before editing, claim a relevant dependency-ready task and reserve exact files or
subtrees. Work areas alone do not reserve anything. Literal paths are required;
globs are rejected. Reserve shared manifests, lockfiles, schemas, migrations and
the named `git-index` resource explicitly when needed. Never stage another worker's
changes. Acquire additional reservations before extending the edit scope.

```cmd
python "<DIBS_SCRIPT>" claim COMPAT-001 --workspace "." --actor "agent-compat" --reserve-tree "tests/fixtures" --reserve-file "docs/COMPATIBILITY.md" --json
python "<DIBS_SCRIPT>" heartbeat COMPAT-001 --workspace "." --actor "agent-compat" --token "TOKEN_FROM_CLAIM" --revision 1 --json
python "<DIBS_SCRIPT>" reserve COMPAT-001 --workspace "." --actor "agent-compat" --token "TOKEN_FROM_CLAIM" --revision 2 --resource "git-index" --json
```

Use actual returned revisions, not example numbers. Durable mutations advance the
revision; ephemeral tags do not. Renew the default ten-minute lease every 60 seconds,
including during checks. Stop editing on ownership conflicts. On revision conflicts,
read fresh state before choosing a valid transition.

Only done satisfies dependencies. Review, blocked and cancelled do not. Expired
leases become abandoned/blocked and retain reservations. Never reclaim until the
previous worker is confirmed stopped or quiescent. Reclaim requires the current
revision and `--ack-quiescent`; resume acquires a fresh lease for deliberately blocked
or reviewed work after dependencies pass.

## Handoffs and completion

Save uniquely named UTF-8 handoff JSON under `.dibs/`. Pass JSON by file to avoid
Windows quoting/encoding problems. Tokens must never enter tracked files.

```json
{
  "summary": "What changed and was verified; editing has stopped.",
  "next_steps": [],
  "changed_files": ["tests/fixtures/example.json"],
  "checks": ["Actual command and observed result"],
  "blockers": []
}
```

```cmd
python "<DIBS_SCRIPT>" complete COMPAT-001 --workspace "." --actor "agent-compat" --token "TOKEN_FROM_CLAIM" --revision 3 --file ".dibs/agent-compat-handoff.json" --ack-quiescent --json
```

Complete only when acceptance passes. Completion/review requires check evidence;
completion rejects blockers. Block with specific blockers and next steps when work
cannot proceed. Ownership-releasing commands require editing to have stopped;
`--ack-quiescent` asserts that fact, not user approval. A standalone handoff retains
the lease. Do not keep editing after releasing ownership.

For new authorized work, import a small structured specification with dependencies
and acceptance criteria. Do not repurpose done/unrelated tasks. Imports are additive
and idempotent for identical specs. Amend with the current revision and owner token,
or `--ack-unowned`, to change specifications without resetting progress.

## Storage, reporting and recovery

Keep the database on local disk outside network/cloud-sync storage. Dibs selects
WAL with the required SQLite fix, otherwise the tested delete-journal fallback.
This host uses the fallback; do not force WAL past its version gate. Never recreate
or overwrite the initialized live database to reset status.

```cmd
python "<DIBS_SCRIPT>" report --workspace "." --actor "coordinator" --status done
python "<DIBS_SCRIPT>" export --workspace "." --actor "coordinator" --file ".dibs/status-new.json"
python "<DIBS_SCRIPT>" backup --workspace "." --actor "coordinator" --file ".dibs/backup-new.sqlite3"
```

Snapshot destinations must be new files. Use SQLite backup, never copy only a live
database while ignoring its journal. Restore to a fresh filename with all workers
stopped; preserve workspace binding and inspect status/events before resuming.
Backups retain leases. Keep `.dibs/`, private data and credentials out of Git.

Commands include list/show/next/events/report, claim/claim-next, heartbeat,
reserve/release, note/handoff, block/resume/review/complete/cancel/reclaim,
import/amend/tag/export/backup. Use COMMAND --help for exact flags.
Exit codes: 0 success, 1 internal tool error (report without retry), 2 conflict,
3 invalid input, 4 storage failure, 5 not found.

Reservations are cooperative, not OS-enforced. Dibs does not launch/stop agents,
merge code or replace version control. Coordination checks use isolated temporary
databases and must never reset the live backlog.