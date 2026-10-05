# ComfyUI SimpleUI

Status: functionally complete for image and video generation, live-verified against a real
ComfyUI installation. PWA/Android installation was cancelled at the user's request and is
not part of this release; another known gap is that no backup command exists yet. Task
completion and ownership live in the local dibs database — do not infer completion from
prose alone.

A compact, Forge-inspired generation interface over an existing, same-machine ComfyUI
installation. Import API JSON, derive controls from installed node metadata, correct
ambiguous mappings, generate images and video, and keep each profile's generations private
through the application.

## What runs today

One FastAPI process (`backend/app/main.py`) serves the built SvelteKit SPA and every API
group: `/api/session`, `/api/settings`, `/api/catalog`, `/api/workflows`, `/api/generations`,
`/api/events`, `/api/media`, `/api/uploads`.

- **Sessions and profiles.** Single-user Default with no setup; optional
  password-protected profiles. Same-origin plus double-submit CSRF on every
  mutation, and a loopback-only gate on host settings.
- **Catalog.** Sanitized node projection cached in SQLite, refreshed with a
  server-enforced cooldown. Startup and every request work with ComfyUI
  unavailable — the snapshot then reports `unavailable` and refuses submission
  rather than guessing defaults.
- **Optional node pack.** [ComfyUI-SimpleUI-Nodes](https://github.com/MisterChief95/ComfyUI-SimpleUI-Nodes)
  is detected from the cached catalog (`SimpleUI*` classes) and its `/simpleui/pack` route;
  `GET /api/catalog/pack` and Settings report it as missing, present or outdated. The app
  works the same without it.
  A workflow using its `SimpleUILoraStack` node gets a LoRA stack control (add, remove,
  reorder, enable, strengths, thumbnails, trigger words) instead of the raw JSON payload.
- **Workflows.** ComfyUI API JSON is imported with a derived control schema. Topology,
  literal types, and exact large seeds are preserved, and an ambiguous mapping is reported,
  never silently rewritten. A workflow can be renamed, or its graph replaced by a newer
  export as a new revision that keeps its layout and corrections.
- **User-designed UI.** The designer at
  `/workflows/[id]` lets each profile arrange any controls from any nodes into named
  sections, hide the rest, and relabel or re-widget them, with phone/tablet/desktop preview,
  drag and drop (mouse and touch), and undo. The run page at `/generation/[id]` renders that
  layout.
- **Generation.** The run page offers drafts that survive reloads, presets, live progress
  from ComfyUI's WebSocket, cancel (queued jobs everywhere, running jobs on ComfyUI ≥ 0.38),
  a result panel with recent outputs, and "reuse settings". Live-verified end to end for
  both an image and a video generation.
- **Media.** Owner-scoped gallery, thumbnails, range-capable streaming and
  original-filename download, plus the one-time local baseline import that
  must complete before multi-user mode can be enabled.

Checks: `cd backend && python -m unittest discover -s tests -v` (the repository has no
committed virtualenv; create one from `backend/requirements.txt` first), then
`cd frontend && npm run lint && npm run format:check && npm run check && npm run build && npm test`
(`npm test` runs the pure-logic `node --test` suites and needs Node ≥ 22). Python lint and
format: `ruff check` and `ruff format --check`.

## Running it

1. Configure ComfyUI's URL via `SIMPLEUI_COMFY_URL` (default `http://127.0.0.1:8188`), and
   optionally `SIMPLEUI_DATA_DIR` / `SIMPLEUI_STATIC_DIR` (see `backend/app/config.py`).
2. Build the frontend once (`cd frontend && npm install && npm run build`) so
   `frontend/build/index.html` exists, or set `SIMPLEUI_DEV=1` to run the API alone.
3. Start ComfyUI, then the backend: `cd backend && python -m uvicorn app.main:create_app
   --factory --port 8000` (a virtualenv per `requirements.txt` first).
4. In the app's Settings, set the ComfyUI input/output folders so uploads and gallery
   capture know where to look, then run the local media import once before enabling
   multi-user mode.

There is no backup command yet: `Database.backup()` exists in `backend/app/storage/db.py`
but nothing calls it, and the Settings page's "Backup folder" field is not read by anything.
Protect `data/` yourself (stop the server, copy the folder) until that is built.

## Implementation handoff

Run the dibs CLI's `next`/`list` commands for current task status — do not infer completion
from prose. Claim a ready task with explicit file/subtree reservations before editing; see
[AGENTS.md](AGENTS.md) for the workflow.
