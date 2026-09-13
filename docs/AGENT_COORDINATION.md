# Local agent coordination specification

## Purpose and bootstrap

Multiple agents on one Windows machine will share a directory. Use a small Python CLI backed by a separate local SQLite database at .coord/tasks.sqlite3. Track tasks, dependencies, ownership, leases, file reservations, decisions, validation evidence, and handoffs. This is development tooling, never an application API or UI feature.

The CLI is implemented at [tools/agent_coord.py](../tools/agent_coord.py), with behavior tests at [tests/coordination/test_agent_coord.py](../tests/coordination/test_agent_coord.py). COORD-001 was bootstrapped by a sole writer. All later agents use the CLI before editing; live completion status and evidence are in the database.

## Storage and concurrency

Use Python standard-library argparse, sqlite3, json, pathlib, and uuid. No coordination server or third-party locking service. SQLite supports concurrent readers but serializes writes; immediate transactions make claims atomic. Keep transactions brief and perform no tests or file editing inside them. [SQLite transactions](https://sqlite.org/lang_transaction.html)

Enable foreign keys per connection, a bounded busy timeout, and WAL on a local disk. Each CLI call/process/thread owns its connection; disabling check_same_thread is not a substitute for a safe connection model. Use bounded retries for busy transactions and return an actionable lock error when exhausted. [Python SQLite threading](https://docs.python.org/3/library/sqlite3.html)

WAL is suitable for same-host processes, not network/shared-sync storage. Require a runtime with the WAL-reset fix: SQLite 3.51.3 or newer, or explicitly verified patched 3.50.7/3.44.6 branches. Check sqlite3.sqlite_version, not just Python's version. If unavailable, fail WAL initialization with remediation or explicitly select tested rollback-journal mode. [SQLite WAL and fix versions](https://sqlite.org/wal.html)

Git-ignore the live database and its -wal/-shm files. Use SQLite backup/export operations for snapshots. Markdown/JSON exports are read-only views, never concurrently rewritten as the live coordination store.

## Logical records

| Record | Required fields |
| --- | --- |
| tasks | id, title, spec, acceptance criteria, priority, status, revision, owner, lease token/expiry, timestamps |
| dependencies | task_id, requires_task_id; no self-links or cycles |
| reservations | normalized path, file/subtree scope, task_id, lease token, expiry |
| events | sequence ID, task_id, actor, event type, structured data, UTC timestamp |
| handoffs | task_id, summary, next steps, changed files, checks/results, blockers |
| metadata | schema version, import source hash/version |

Tasks have stable IDs from TASKS.md. Dependencies determine readiness; do not store a separate mutable ready flag. Ready means todo, all prerequisites done, and no unresolved abandoned reservations.

States: todo, in_progress, blocked, review, done, cancelled. Completion is explicit and must include acceptance evidence. Review is available for integration handoff; a separate reviewer is desirable where available, not a precondition for routine small tasks.

## Atomic ownership rules

Claim-next performs selection, dependency verification, requested reservation conflict checks, status update, owner assignment, and lease creation within one BEGIN IMMEDIATE transaction. A named claim has the same semantics. Concurrent claimers cannot both receive the same task. Ordering: priority then stable task ID.

Proposed lease: 10 minutes; heartbeat every 60 seconds, including while long tools/tests run when possible. Each mutation requires owner plus an unguessable lease token and expected revision. A heartbeat renews task and reservations together. A stale writer cannot complete, release, or overwrite a reclaimed task.

Expiry marks a task abandoned/blocked and keeps its reservations in conflict until inspected. Never automatically recycle an expired file reservation into another writer: the previous agent may still be editing. Reclaim requires an explicit local acknowledgement that the old worker is stopped or quiescent, plus a new lease token and an audit event. This is cooperative coordination; SQLite cannot prevent a rogue/stale process from editing a file directly.

Reserve exact files or directory subtrees with literal paths, not shell globs. On Windows normalize separators and case, resolve existing parent junctions, reject paths outside the workspace, and check parent/child overlap (a reservation of backend/app conflicts with backend/app/api.py). Acquire multiple paths atomically to avoid partial lock sets. Read access is unrestricted; writers must reserve before editing. File reservations are advisory but mandatory agent protocol.

Shared manifests, lockfiles, migrations, and public API schemas require explicit reservations. Reserve new migration numbering as a shared resource or serialize migration creation. Git index operations also serialize through a named reservation; never stage another agent's work unintentionally. Do not use broad cleanup/reset commands to resolve a reservation conflict.

Blocked tasks release work only with a recorded handoff and a statement that editing stopped. Resume reacquires a fresh claim. Transition to review/done atomically records evidence and releases active ownership. Dependencies unblock only on done, not review. Cancelling a task does not silently satisfy dependents; amend their dependencies explicitly.

## CLI contract

Normal output is concise text: `list`/`next` show one row per task, `show` displays specification and the latest handoff, mutations report status/revision/ownership, and `events` shows a compact audit log. Use these views for human or agent inspection to avoid repeating full records. Add `--json` when a script needs the complete structured response (including all handoffs). JSON includes ok, structured errors, task revision where applicable, and relevant records; its schema is unchanged. Exports remain JSON even without `--json`. Plain-text errors go to stderr; JSON errors remain on stdout. Nonzero exit codes distinguish conflict, invalid input, unavailable storage, and missing task. Common options go after the command; each command supports --help.

| Command | Behavior |
| --- | --- |
| init | Idempotent schema initialization and runtime checks |
| import --file | Transactionally import structured task specs, validate IDs/dependency DAG |
| list / show / next | Read status/detail/ready candidates without claiming |
| claim / claim-next | Atomic claim, return token and next-step context; accept reservation set |
| heartbeat | Renew only a currently valid owner lease |
| reserve / release | Atomically manage literal file/subtree reservations |
| note / handoff | Append findings, decisions, next steps, checks, and changed-file references |
| block / resume / review / complete | Validated state transitions with expected revision and token |
| amend | Update spec/dependencies with expected revision; never silently overwrite progress |
| reclaim | Explicit abandoned-work takeover after quiescence acknowledgement |
| export / backup / events | Snapshots and append-only audit inspection |

The task-import agent reads TASKS.md and creates a structured import file; do not build a fragile natural-language Markdown parser. Import schema: tasks array containing id, title, priority, depends_on, work_areas, description, acceptance. New imports default to todo. An unchanged re-import is idempotent. Changed specifications require explicit amendments and preserve status/ownership/history. Import validates the whole graph before committing anything.

Agent loop: read context -> claim with reservations -> make bounded edits -> record discoveries/heartbeat -> validate -> record handoff/evidence -> complete or block. A blocker should name exactly what is missing and who or what can resolve it. Avoid storing credentials or user media in task events.

## Acceptance checks for the CLI

Use real separate processes, not only threads, for contention checks:

1. Two agents racing for one task yield exactly one successful claim.
2. A task with unfinished prerequisites cannot be claimed.
3. Case variants and parent/child reservations conflict on Windows; disjoint paths succeed.
4. Crashing a writer rolls back its partial transaction; history and task remain consistent.
5. Expired leases cannot mutate tasks; reclaim invalidates old tokens and requires quiescence acknowledgement.
6. Heartbeat renews reservations and task together; an expired reservation never silently transfers.
7. Re-import does not reset completed work; a dependency cycle rejects the entire import.
8. Completion records evidence and releases reservations atomically; expected-revision conflicts preserve the newer state.
9. Backup/restore and schema migration preserve tasks and events; a busy database fails clearly within a bounded time.

The system tracks cooperative ownership and durable progress. It does not automatically merge conflicting code, enforce OS-level file access, schedule agents, or replace version control.

## Using the CLI

Python 3.13 or newer is supported. No package installation is needed. This machine's Python 3.13.13 includes SQLite 3.50.4, so the database explicitly uses the tested `delete` rollback-journal fallback. The default `wal` initialization refuses an unpatched runtime. Database files must remain on local disk, outside cloud-sync folders and network/mapped shares; filesystem mount detection is not a security boundary.

Run from the repository directory. The default workspace is the script's repository, and default database is `.coord/tasks.sqlite3`; `--workspace` and `--db` override them for isolated testing. Give every agent a distinct `--actor`. Examples show placeholder lease tokens; replace them with the actual claim response and use the latest returned revision.

```cmd
python tools/agent_coord.py --help
python tools/agent_coord.py init --journal delete
python tools/agent_coord.py import --file docs/tasks.json --actor coordinator
python tools/agent_coord.py list
python tools/agent_coord.py next --json
python tools/agent_coord.py show COMPAT-001
python tools/agent_coord.py claim COMPAT-001 --actor agent-compat --reserve-tree tests/fixtures --reserve-file docs/COMPATIBILITY.md --json
python tools/agent_coord.py heartbeat COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 1 --json
```

`claim-next` atomically selects a ready task that fits the explicit requested reservations. `next` is a read-only list of dependency-ready candidates, not a guarantee that an agent's later path requests will be free. Work-area descriptions in tasks.json may be prose or globs; translate them into concrete `--reserve-file`, `--reserve-tree`, or `--resource` arguments. Nothing auto-locks an entire work area merely by claiming a task.

Each successful heartbeat, note, handoff, reserve/release, amendment, or transition increments the revision. Save the new revision; the token stays unchanged until ownership is released or reclaimed. Read/export views omit tokens. If a token is lost, wait for expiry, stop the old worker, and use explicit reclaim. Initial claims, imports, and initialization do not require a previous token. Lease-protected mutations do.

```cmd
python tools/agent_coord.py reserve COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 2 --resource git-index
python tools/agent_coord.py release COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 3 --resource git-index
python tools/agent_coord.py note COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 4 --message "Captured sanitized node metadata; video fixture is next."
```

Handoffs use an explicit JSON file; all array entries are text. Completion/review requires nonempty checks; completion rejects unresolved blockers. Blocking requires at least one named blocker. A standalone `handoff` records context while retaining the lease.

```json
{
  "summary": "Describe the verified result and state that editing stopped.",
  "next_steps": ["Concrete next action for the next worker"],
  "changed_files": ["tests/fixtures/example.json"],
  "checks": ["Command run and actual result"],
  "blockers": []
}
```

```cmd
python tools/agent_coord.py complete COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 5 --file .coord/handoff.json --ack-quiescent
python tools/agent_coord.py events --task COMPAT-001 --after 0 --limit 100 --json
python tools/agent_coord.py export --file .coord/status-snapshot.json
python tools/agent_coord.py backup --file .coord/tasks-backup.sqlite3
```

Snapshot destinations must not already exist. Exports contain status and audit history, not a task-import payload or a restorable database. Restore a database backup only with every worker stopped, to a fresh filename, then pass that path using `--db`; inspect `list` and `events` before resuming. Preserve the workspace binding. Backups retain leases; do not assume restored claims are safe to reuse while an old worker could still be running.

Expired tasks appear blocked/abandoned immediately in reads. The next mutation materializes expiry and an audit event; the read revision already accounts for that pending change. Reservations remain held. `reclaim TASK --actor NEW_AGENT --revision N --ack-quiescent` replaces an expired token and renews existing reservations after explicit inspection/stopping of the previous worker. It cannot seize a live lease. `resume TASK --revision N` acquires a fresh lease for a deliberately blocked or reviewed task, after prerequisites pass. Resume can accept new path reservations. A review does not unblock dependents until the reviewer resumes and completes it.

`amend TASK --file spec.json --revision N` accepts one full task spec. Active tasks require their owner/token. Unowned tasks instead require `--ack-unowned` plus the current revision and actor; this explicit local operation is necessary to repair specifications/dependencies before they are claimable. Amendments preserve status/history and reject cycles or adding unfinished prerequisites to active/completed work. `cancel` requires an owned lease, handoff, and quiescence acknowledgement; cancelled prerequisites still block dependents.

Exit codes: **0** success, **2** ownership/revision/reservation conflict, **3** invalid input, **4** storage/runtime failure, **5** missing task/database. Busy writes retry at most three times, each using `--busy-timeout` milliseconds (default 2000), plus brief backoff; failed transactions roll back. Retry conflicts by reading fresh state, not by guessing a revision or bypassing reservations.

Schema migrations are transactional: version 1 contains the records, version 2 adds query indexes. `init` is idempotent, migrates older supported schemas, and refuses newer schemas. Tests verify preserved task/event data during migration and restoration.

```cmd
python -m unittest discover -s tests/coordination -v
```

The test suite creates isolated temporary databases; it never resets the live backlog. There is intentionally no ordinary reset command. Recreating the live database is a separate, explicitly authorized maintenance action with all workers stopped.
