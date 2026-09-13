# Coordination bootstrap validation

COORD-001 implementation: [CLI](../tools/agent_coord.py) and [behavior tests](../tests/coordination/test_agent_coord.py).

Verified on Windows with Python 3.13.13 and SQLite 3.50.4. The live store uses explicit rollback-journal mode (`init --journal delete`); WAL was refused by the runtime version gate as intended. No third-party dependencies were installed.

```cmd
python -m unittest discover -s tests/coordination -v
```

Result: **13 tests passed**, 11.193 seconds on the final bootstrap run. Tests use disposable databases and real CLI subprocesses; the live backlog is not their test fixture.

| Specification check | Observed test evidence |
| --- | --- |
| 1. Concurrent claims | Two competing processes yield one winner and one conflict |
| 2. Dependencies | Unfinished/reviewed/cancelled prerequisites block claims; completion makes successors ready |
| 3. Reservations | Case-insensitive parent/child conflicts, atomic multi-path failure, disjoint paths, named resources, path traversal and Windows junction rejection |
| 4. Crashed writer | Process exits mid-transaction; task change and partial event both roll back |
| 5. Expiry/reclaim | Actual expired lease rejects heartbeat; fault-injected abandoned worker requires acknowledgement; new token invalidates the old writer |
| 6. Heartbeat/retained locks | Task and reservation expiry renew together; expired reservations still conflict |
| 7. Import safety | Completed state survives identical re-import; cycles, missing dependencies, duplicate IDs/keys, and changed specs reject atomically |
| 8. Completion/revisions | Stale revisions and empty evidence reject; handoff/status/reservation release commit together; review/resume and block/cancel paths checked |
| 9. Backup/migration/contention | Backup opens as restored store with matching tasks/events; v1-to-v2 migration preserves records; newer schemas reject; locked writer fails within a bound while reads remain available |

The first test run revealed unclosed SQLite test connections during Windows temporary-directory cleanup. Connections are now explicitly closed, including the CLI's backup destination. The subsequent full run passed.

## Backlog validation procedure

The reviewed [tasks.json](tasks.json) contains all 19 task specifications plus the architecture/mapping review requirements. It was compared with TASKS.md for IDs, dependencies, priorities, work areas, and baseline acceptance criteria. Descriptions and supplemental acceptance criteria were reviewed against the planning documents.

PLAN-001 completion evidence in the live database records the actual import, unchanged re-import, integrity/foreign-key checks, completed bootstrap records, and ready successors. Read it with:

```cmd
python tools/agent_coord.py show PLAN-001
python tools/agent_coord.py events --task PLAN-001
python tools/agent_coord.py next
```

Runtime state, handoff files, backups, and exported snapshots stay under ignored `.coord/`. This directory is not currently a Git repository; `.gitignore` is prepared for subsequent repository initialization. No commits or application implementation were performed.
