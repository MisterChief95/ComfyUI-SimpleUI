# ComfyUI SimpleUI — agent guide

This is the shared repository guide for coding agents. Read it before changing files. `CLAUDE.md` points here so project instructions stay in one place. Follow the current user request and applicable higher-priority instructions; this file describes the repository's intended workflow, not authorization to expand the task.

## What we are building

A standalone, Forge-inspired interface that makes ComfyUI workflows convenient to run without a node canvas. Keep the experience clean, compact, and usable on phones, tablets, and desktops.

- Import **ComfyUI API JSON** and derive grouped controls at runtime from installed node metadata. Let users correct ambiguous mappings and save those corrections per workflow or compatible node.
- Support **image and video generation**, a gallery of saved outputs, and per-profile generation/prompt history.
- Run Python and ComfyUI on the **same machine**. Python owns authentication, upstream API calls/credentials, storage, and authorized file streaming.
- Start with a **Default profile**; optional multi-user mode adds password-protected profiles with no administrator role. Keep each profile's inputs, history, and outputs private through the app.
- Use **Svelte 5 + TypeScript + SvelteKit as a static SPA**, served by **FastAPI**. Node is needed for frontend development/builds, not as a second production server.
- Deliver an **installable PWA**, including Android use over trusted HTTPS. Native Android and offline generation are deferred.
- Use **SQLite** for application metadata and a **separate local coordination database** for agents. Keep dependencies and abstractions minimal.

The coordination tool is implemented; inspect the filesystem and database for current application progress. Do not assume a planned feature, a completed conversation, or an old status snapshot means implementation is complete.

## Where decisions and status live

Read the relevant documents before implementing a task:

| Document | Purpose |
| --- | --- |
| [README.md](README.md) | Project entry point |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Product scope, backend, profiles, media, deployment |
| [docs/WORKFLOW_MAPPING.md](docs/WORKFLOW_MAPPING.md) | Discovery, bindings, mapping persistence, compatibility |
| [docs/TASKS.md](docs/TASKS.md) | Original implementation tasks and acceptance criteria |
| [docs/tasks.json](docs/tasks.json) | Reviewed initial backlog, including review additions |
| [docs/AGENT_COORDINATION.md](docs/AGENT_COORDINATION.md) | Full CLI, storage, reservation, and recovery contract |

**`.coord/tasks.sqlite3` is the live task/status source.** Use the CLI, not direct SQL writes. JSON/Markdown task documents and exported snapshots do not supersede current ownership or completion evidence. Later user-requested tasks may be added through a separate structured import without rewriting the original release backlog.

## Before editing: claim and reserve

Read-only inspection needs no claim. Before writing repository files, use an appropriate task and reserve the exact files or subtrees you will edit. Do not repurpose a completed task or claim an unrelated task merely to obtain locks. For authorized work absent from the backlog, import a small new task specification through the CLI first; include dependencies and acceptance criteria.

Run commands from the repository root. Common options belong **after the command**. Give each agent a distinct, stable `--actor`. Use default text output for compact inspection; use `--json` for programmatic access, especially to capture lease tokens and revisions from mutations.

```cmd
python tools/agent_coord.py next
python tools/agent_coord.py show COMPAT-001
python tools/agent_coord.py claim COMPAT-001 --actor agent-compat --reserve-tree tests/fixtures --reserve-file docs/COMPATIBILITY.md --json
```

These are examples: choose the task that matches the user's request and current readiness. `claim-next` also exists, but its reservations must fit the task you will execute. A claim does **not** automatically reserve the task's listed work areas. Translate prose/globs into concrete `--reserve-file` or `--reserve-tree` paths. Reserve shared manifests, lockfiles, schemas, and migration files explicitly; use `--resource git-index` before Git staging/index operations. Never stage another worker's changes accidentally.

Save the returned `lease_token` and `task.revision`. Never put tokens in tracked files. Every successful task mutation, including a heartbeat or note, increments the revision; carry forward the new value. Defaults are a **10-minute lease** with a **heartbeat every 60 seconds**. Choose a suitable bounded lease before a long blocking operation and stop editing if ownership expires.

```cmd
python tools/agent_coord.py heartbeat COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 1 --json
python tools/agent_coord.py note COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 2 --message "Recorded findings; next action is to capture the video fixture." --json
```

Numbers above assume that exact sequence; use actual returned revisions. `reserve` and `release` accept the same literal path/resource flags plus your token and revision. Acquire additional reservations before touching additional files. Reservations are cooperative, not OS-enforced; an expired reservation still blocks other writers.

## Finish with evidence or a useful handoff

Validate the task's acceptance criteria. Save a uniquely named handoff JSON file under `.coord/`, such as `.coord/agent-compat-handoff.json`:

```json
{
  "summary": "What changed and what is verified; editing has stopped.",
  "next_steps": ["Concrete next action, if any"],
  "changed_files": ["tests/fixtures/example.json"],
  "checks": ["Actual command and observed result, not a planned check"],
  "blockers": []
}
```

```cmd
python tools/agent_coord.py complete COMPAT-001 --actor agent-compat --token "TOKEN_FROM_CLAIM" --revision 3 --file .coord/agent-compat-handoff.json --ack-quiescent --json
```

Use `complete` only after the requirements are met. It records evidence and releases reservations atomically. Use `block` with specific blockers and next steps when you cannot proceed, or `review` to hand off for review; both require a handoff and `--ack-quiescent`. A standalone `handoff` keeps your lease. `resume` claims deliberately blocked/reviewed work with a fresh lease after dependencies pass. Only **done** prerequisites unblock dependent tasks.

`--ack-quiescent` means editing has actually stopped; it is not a request for user confirmation. Do not keep editing after releasing ownership.

## Conflicts, maintenance, and scope changes

- On a revision conflict, read fresh state. On an ownership conflict, stop writing. Never guess revisions or bypass the CLI.
- Reclaim an expired task only after confirming the old worker is stopped/quiescent. Use `reclaim TASK --actor NEW_AGENT --revision N --ack-quiescent`; it returns a new token while retaining reservations. It cannot seize a live lease.
- Use `amend` for an existing task specification, with the expected revision and owner token if claimed, or explicit `--ack-unowned` if unowned. Keep affected planning documents and the task spec consistent. Do not reset progress through import.
- The existing database is already initialized. **Do not recreate it, overwrite it, or re-import merely to reset status.** First-time setup on a genuinely new workspace is documented in the full coordination guide.
- Use `backup` for a consistent SQLite copy and `export` for a readable status snapshot; choose new filenames. Never copy only a live database file while ignoring its journal/WAL. Keep `.coord/`, private data, and credentials out of version control.
- This machine currently uses the tested `delete` journal fallback because bundled SQLite lacks the required WAL fix. Do not force WAL past the version gate or put the store on a network/cloud-sync drive.

CLI exit codes: `0` success, `2` conflict, `3` invalid input, `4` storage/runtime failure, `5` missing task/database. Use `python tools/agent_coord.py COMMAND --help` for exact options.

## Implementation boundaries

- Preserve workflow topology, literal types, and exact large seeds. Mapping uncertainty needs a diagnostic or saved correction, not silent graph rewrites or guessed defaults.
- Keep raw catalogs and shared filenames server-side. Bind generation ownership at submission; apply authorization to media, uploads, history, and events. Handle uncertain submissions without blind retries.
- Respect partial execution, cached outputs, missing files, and temporary recovery records when history retention is disabled. Keep the original user's media intact.
- Same-host profiles provide application-level privacy among trusted home users; they do not sandbox arbitrary ComfyUI custom nodes. Do not imply hostile-tenant isolation.
- Prefer the standard library and native controls where adequate. Add architecture/dependencies only for a concrete requirement. Do not silently expand into a node editor, remote multi-server hosting, automatic model/plugin installation, or native Android.

## Working and validation conventions

The current development host is Windows/PowerShell. Prefer `rg` for search; GNU CoreUtils are available where useful. Quote command arguments with double quotes, use forward-slash relative paths where supported, exclude heavy directories from searches, and preserve file encoding/line endings. Avoid shell tricks that reinterpret paths or expose secrets. Check resolved targets before destructive filesystem operations.

Run checks appropriate to the change. For coordination tool changes:

```cmd
python -m unittest discover -s tests/coordination -v
```

These tests use temporary databases, not the live backlog. As frontend/backend code is added, use its actual documented build/typecheck/test commands; do not invent missing scripts. Use applicable Svelte skills when available. Image/video release claims require recorded live ComfyUI evidence, not only synthetic fixtures. Finish with a concise account of changes, checks, and remaining limitations, and leave the coordination record ready for the next agent.
