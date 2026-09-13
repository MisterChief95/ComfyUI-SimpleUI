# ComfyUI SimpleUI — implementation plan

Status: partially implemented. The FastAPI backend and the SvelteKit SPA build and run; **generation is not wired up yet**, so nothing in this repository has been verified against a live GPU run. Use the coordination CLI for current task status — do not infer completion from these documents.

Build a compact, Forge-inspired generation interface over an existing, same-machine ComfyUI installation. Import API JSON, derive controls from installed node metadata, remember mapping corrections, and keep each profile's generations private through the application.

## What runs today

One FastAPI process (`backend/app/main.py`) serves the built SPA and the API.
Assembled and covered by `backend/tests/test_integration.py`:

- **Sessions and profiles.** Single-user Default with no setup; optional
  password-protected profiles. Same-origin plus double-submit CSRF on every
  mutation, and a loopback-only gate on host settings.
- **Catalog.** Sanitized node projection cached in SQLite, refreshed with a
  server-enforced cooldown. Startup and every request work with ComfyUI
  unavailable — the snapshot then reports `unavailable` and refuses submission
  rather than guessing defaults.
- **Workflows.** ComfyUI API JSON imported as an immutable revision 1, with a
  derived control schema. Topology, literal types, and exact large seeds are
  preserved; an ambiguous mapping is reported, never silently rewritten.
- **Media.** Owner-scoped gallery, thumbnails, range-capable streaming and
  original-filename download, plus the one-time local baseline import that
  must complete before multi-user mode can be enabled.

Not implemented: generation submission, progress events, and uploads
(`/api/generations`, `/api/events`, `/api/uploads`).

Checks: `cd backend && .venv\Scripts\python -m unittest discover -s tests -v`
(233 tests), then `cd frontend && npm run check && npm run build`.

## Read in this order

1. [Product and architecture](docs/ARCHITECTURE.md): scope, Svelte versus SvelteKit, backend, profiles, gallery, settings, and delivery.
2. [Workflow mapping](docs/WORKFLOW_MAPPING.md): runtime discovery, control rules, saved corrections, and compatibility boundaries.
3. [Agent coordination](docs/AGENT_COORDINATION.md): local SQLite task store, atomic claims, file reservations, handoffs, and script contract.
4. [Implementation tasks](docs/TASKS.md): stable task IDs, dependencies, work areas, and acceptance criteria.

## Recommended direction

- Svelte 5 and TypeScript, using SvelteKit as a static SPA; Python serves the build and owns all server behavior.
- FastAPI, one backend process initially, SQLite application storage, and direct access to configured ComfyUI folders.
- Installable PWA for Android, tablet, and desktop; generation continues on the home machine.
- A separate SQLite database and a small Python standard-library CLI for agents on this machine.

## Implementation handoff

The next agent should read all four documents, then run `python tools/agent_coord.py next`. The structured backlog is [docs/tasks.json](docs/tasks.json); completion and ownership live in the local database. Claim a ready task with explicit file/subtree reservations before editing. See [CLI usage](docs/AGENT_COORDINATION.md#using-the-cli) for commands and handoff formats. Do not mark planned work completed merely because it appears in these documents.

The task database becomes the live status source after import. These documents remain the design and acceptance baseline. Scope changes should update the affected document and task specification together.

Technical sources are linked beside relevant decisions. Upstream source links track moving branches; the implementation compatibility task must record the actual tested ComfyUI revision and dependency versions.
