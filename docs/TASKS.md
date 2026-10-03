# Implementation tasks

These are task specifications; live status is maintained by the coordination CLI (the `dibs`
plugin's SQLite store — `.coord/tasks.sqlite3` below and in [tasks.json](tasks.json) is this
document's original, now-superseded path). The reviewed import payload is [tasks.json](tasks.json). Each task retains its stable ID, title, priority, dependencies, work areas, description, and acceptance criteria. Work areas are reservation guidance, not already-held locks. P0 precedes P1 when dependencies permit; P1 still belongs to the first release. There are no effort estimates until compatibility fixtures are established. This file is kept as the original planning record and is **not** rewritten task-by-task as work completes — see [RELEASE_REVIEW.md](RELEASE_REVIEW.md) for current, evidence-backed status and [COMPATIBILITY.md](COMPATIBILITY.md) for what has actually been observed against a live ComfyUI installation.

## Reconciliation notes (2026-09-16, DOCS-001)

Two deviations from the graph below, both by explicit user request, not technical necessity:

- **PWA-001 is cancelled**, not merely pending. It required real Android/Chrome device
  testing over trusted HTTPS that was never available in this environment; the user chose to
  drop it rather than keep it open. Nothing below that names PWA-001 should be read as still
  scheduled.
- **RELEASE-001's live dependency graph no longer includes PWA-001** (only `VERIFY-001`), so
  it could proceed without Android support. Its own acceptance criteria were amended to
  require no PWA/offline/service-worker claims instead. `tasks.json` below is left exactly as
  originally imported, per this project's own rule not to rewrite import payloads after the
  fact — read the amendment on the live RELEASE-001 task (`dibs show RELEASE-001`) or
  [RELEASE_REVIEW.md](RELEASE_REVIEW.md) for the current acceptance criteria actually used.

All other tasks below reflect what was actually delivered; see [RELEASE_REVIEW.md](RELEASE_REVIEW.md)'s
requirement table for evidence per task, and its "Known limitations" section for gaps found
during review that were never their own task (an unwired backup-folder setting; the
application never opening ComfyUI's own live WebSocket).

When importing acceptance criteria, also incorporate the review regressions assigned to task IDs in ARCHITECTURE.md's Release evidence and WORKFLOW_MAPPING.md's Required verification. These clarify existing tasks without adding tasks or changing dependencies.

## COORD-001 — Bootstrap local coordination CLI

- Priority: P0
- Depends on: none
- Work areas: tools/agent_coord.py, tests/coordination/, .gitignore
- Work: implement AGENT_COORDINATION.md using standard-library SQLite; initialize only during implementation. One agent owns bootstrap.
- Acceptance: all nine coordination checks pass across processes on Windows; help documents CLI operations and failure codes; no runtime database committed; task import can proceed.

## PLAN-001 — Import and validate implementation backlog

- Priority: P0
- Depends on: COORD-001
- Work areas: .coord/ via CLI, docs/ if import clarification is necessary
- Work: read planning documents, produce a structured import payload, import every task, and attach bootstrap evidence. Check dependency graph and explicit reservations for future work.
- Acceptance: all 19 task IDs exist once; dependencies match this document; unchanged re-import preserves progress; only actually completed bootstrap/import tasks are done.

## COMPAT-001 — Capture ComfyUI capabilities and fixtures

- Priority: P0
- Depends on: PLAN-001
- Work areas: tests/fixtures/, docs/COMPATIBILITY.md
- Work: locate the user's installed ComfyUI through explicit configuration, record revision and node packs, capture sanitized object_info and API graphs for image/video, execution events, output descriptors, loader contracts, and cancellation capabilities. If installation is unavailable, build synthetic contract fixtures and record the live gate as blocked rather than claiming verification.
- Acceptance: fixture set covers core literals, large seeds, repeated nodes, missing classes, custom/dynamic shapes, image and video outputs; live evidence is distinguished from synthetic evidence; no personal prompts, filenames, or secrets committed. Real image/video fixtures are required before release.

## FOUNDATION-001 — Scaffold frontend and backend and freeze contracts

- Priority: P0
- Depends on: PLAN-001
- Work areas: frontend/, backend/, docs/API.md, root manifests and .gitignore
- Work: establish SvelteKit static SPA and FastAPI baseline, local config validation, development proxy, production static fallback, explicit dependency locks, health response, typed contracts, structured errors, and check commands. Follow applicable Svelte skills during component implementation.
- Acceptance: Python serves built UI and browser deep links; API/asset 404s remain proper 404s; no production Node server required; typecheck/build/backend smoke check pass; contract covers control descriptors, exact integer transport, ownership, revisions, pagination, and errors.

## DATA-001 — Application schema and durable storage

- Priority: P0
- Depends on: FOUNDATION-001
- Work areas: backend/app/storage/, backend/tests/storage/, backend/migrations/
- Work: implement tables and indexes from ARCHITECTURE.md, explicit migrations, Default creation, short SQLite transactions, backup/restore, and repository methods scoped by owner.
- Acceptance: restart retains data; foreign keys enforced; migrations and backup restore verified; Default created exactly once; owner/time pagination stable; blocking storage operations do not stall async requests.

## AUTH-001 — Profiles, sessions, and local settings boundary

- Priority: P0
- Depends on: DATA-001
- Work areas: backend/app/auth/, backend/app/settings/, backend/tests/auth/
- Work: single-user Default session, locally gated multi-user activation/profile creation, passwords/sessions, CSRF/origin/Host checks, login rate limits, session expiry, settings scopes, and local recovery procedure. Activation calls the media-baseline readiness gate; until MEDIA-001 implements it, activation fails safely.
- Acceptance: two accounts cannot access each other's API resources; activation invalidates anonymous sessions and requires Default password plus completed baseline; disabling while extra profiles exist is blocked; untrusted forwarded headers cannot unlock host settings; session/logout tests pass.

## CATALOG-001 — Cached node discovery and refresh

- Priority: P0
- Depends on: DATA-001, COMPAT-001
- Work areas: backend/app/comfy_client.py, backend/app/catalog/, backend/tests/catalog/
- Work: reusable async client and object_info normalization, cached startup, schema hashes, global cooldown/coalescing, retry/backoff, freshness/error reporting, and capability record.
- Acceptance: simultaneous refresh requests produce one upstream fetch; failed refresh retains last good catalog; first-run offline state is explicit; model option changes invalidate selections safely; no credentials leak in responses/logs.

## MAPPING-001 — API import and deterministic control schema

- Priority: P0
- Depends on: CATALOG-001
- Work areas: backend/app/workflows/, backend/app/mapping/, backend/tests/mapping/
- Work: implement import validation, immutable graph storage, link/literal classification, scalar/combo controls, semantic grouping, exact seed transport, custom-node diagnostics, and submission-copy construction.
- Acceptance: required verification in WORKFLOW_MAPPING.md passes for untouched round-trip, arrays/links, large seeds, optional omissions, repeated stages, and missing classes; controls include binding provenance; no graph rewiring.

## MAPPING-002 — Persist personal mapping corrections

- Priority: P0
- Depends on: MAPPING-001, AUTH-001
- Work areas: backend/app/mapping/, backend/tests/mapping/
- Work: per-workflow and compatible per-node overrides, structural signatures, precedence, optimistic revisions, stale-mapping detection, reset/export behavior.
- Acceptance: literal edits retain layout; incompatible schema changes require repair; general templates contain presentation only; context-specific prompt labels do not spread incorrectly; profiles cannot read others' corrections; conflicting saves return revision conflicts.

## MEDIA-001 — Owned filesystem index, imports, and streaming

- Priority: P0
- Depends on: AUTH-001, COMPAT-001
- Work areas: backend/app/media/, backend/tests/media/
- Work: configured-root scanner and initial Default baseline, opaque media IDs, path/identity validation, ownership resolver, unresolved-file handling, immutable captures, image thumbnails, optional video posters, authenticated file streaming, and gallery queries.
- Acceptance: existing image/video files appear under Default; missing metadata is shown as unknown; post-baseline uncertain files never leak into Default; overwrite/path traversal/junction tests pass; video HEAD/range/seek and forbidden thumbnail access verified; baseline gate enables safe multi-user activation.

## INPUT-001 — Private image, mask, and video inputs

- Priority: P0
- Depends on: MEDIA-001, MAPPING-001
- Work areas: backend/app/uploads/, backend/app/mapping/input_adapters.py, backend/tests/uploads/
- Work: streamed bounded uploads, private asset IDs, supported loader bindings, scoped ComfyUI input staging, and selection filtering. Upload a mask file; no drawing editor.
- Acceptance: real or captured supported image/video loader contracts validate; users cannot reference another profile's uploads through IDs or substituted filenames; large videos do not require whole-file memory buffering; unavailable loader adapters show actionable diagnostics.

## GEN-001 — Durable submission and execution tracking

- Priority: P0
- Depends on: MAPPING-002, INPUT-001
- Work areas: backend/app/generations/, backend/app/events/, backend/tests/generations/
- Work: persist owner and exact effective graph/seeds before submission, request idempotency, upstream correlation, filtered events, pending caps, completion/output association, restart reconciliation, and capability-safe cancellation.
- Acceptance: image/video fixture executions traverse states and attach all outputs to the original owner; profile switch cannot change ownership; lost submission response never blindly resubmits; restart/disconnect recover where evidence exists and show unknown otherwise; unsafe global cancellation unavailable; foreign/unattributable previews dropped.

## UI-001 — Responsive shell, profile flow, and settings

- Priority: P0
- Depends on: AUTH-001
- Work areas: frontend/src/routes/ layouts/login/settings, frontend/src/lib/ui/, frontend/src/lib/api/
- Work: establish visual tokens, responsive navigation, session flow, settings controls/scopes, accessibility behavior, and shared typed API client. Agree exact file reservations before overlapping UI tasks.
- Acceptance: mobile/tablet/desktop shells fit target sizes and keyboard navigation works; profile switch clears sensitive state; global settings cannot be changed from unprivileged remote sessions; toggles persist and accurately describe scope.

## UI-002 — Automatic generation form and mapping editor

- Priority: P0
- Depends on: UI-001, GEN-001
- Work areas: frontend/src/routes/ generation/workflows, frontend/src/lib/controls/, frontend/tests/mapping/
- Work: API JSON picker, generated grouped controls, compact/advanced layout, mapping correction UI, seed policy, owned file inputs, submit/progress/errors, and latest-result panel.
- Acceptance: a known workflow opens without mandatory setup; ambiguous mapping can be corrected and reused; repeated stages and exact seeds survive edits; catalog refresh preserves drafts; image and video submissions work; sticky action does not cover fields on phone keyboards.

## UI-003 — Gallery and generation/prompt history

- Priority: P0
- Depends on: UI-001, GEN-001
- Work areas: frontend/src/routes/ gallery/history, frontend/src/lib/media/
- Work: paginated media grid, filters/favorites, image viewer/video player, generation details, known-prompt search, download, missing-file state, and reuse-as-draft. Implement history-retention preferences and clear-own-history semantics without destroying ownership records.
- Acceptance: both media types render; old unknown prompts stay unknown; disabling future snapshots removes reuse only for affected jobs; another user's cached/private content never appears on switch; pagination handles a large synthetic library without fetching every item.

## PWA-001 — Installable Android experience and local deployment

- Priority: P1
- Depends on: UI-002, UI-003
- Work areas: frontend/static/, frontend/src/service-worker.*, docs/DEPLOYMENT.md, backend/app/static_serving.py
- Work: manifest/icons, static-shell-only service worker, offline/reconnect behavior, trusted HTTPS home-network instructions, Python startup/config documentation, and Android installation flow.
- Acceptance: install and launch on Android Chrome over trusted HTTPS; navigation and media playback work; no API/private media in service-worker storage; offline Generate cannot queue hidden requests; production startup uses Python plus existing ComfyUI only, with HTTPS hosting as configured.

## VERIFY-001 — Integrated privacy, reliability, and compatibility checks

- Priority: P0
- Depends on: UI-002, UI-003
- Work areas: tests/integration/, docs/COMPATIBILITY.md
- Work: run two-profile ownership attacks across API/events/media/uploads, baseline/import races, expired sessions, malformed graphs, unavailable/restarting ComfyUI, missing outputs, and compatibility fixtures. Record actual live image and video generation outcomes.
- Acceptance: no cross-profile access via IDs/ranges/thumbnails/input pickers; original owner survives switches and restarts; ambiguous outputs withheld; real image and video generation evidence exists or release is explicitly blocked; tests target behavior, not duplicate implementation details.

## RELEASE-001 — End-to-end release review and operator handoff

- Priority: P1
- Depends on: PWA-001, VERIFY-001
- Work areas: docs/, README.md
- Work: review must-have coverage, setup from clean checkout, backup/restore, Android installation, responsive/accessibility checks, documented limitations, dependency versions, and remaining blockers.
- Acceptance: every release requirement in ARCHITECTURE.md has recorded evidence; operator can configure folders and start the app; runtime data stays ignored; unsupported nodes/codecs are identified accurately; no claim of hostile-user isolation or universal custom-node support.

## DOCS-001 — Reconcile implementation and planning records

- Priority: P1
- Depends on: RELEASE-001
- Work areas: docs/, README.md, .coord/ via CLI
- Work: replace planning-only status with actual capabilities, reconcile design deviations and task evidence, export a final coordination snapshot, and retain deferred scope explicitly.
- Acceptance: documents match delivered behavior; all completed tasks have evidence; remaining work is not falsely marked done; future agents can find known limitations and next steps without reading a conversation transcript.

## Scheduling guidance

After backlog import, COMPAT-001 and FOUNDATION-001 can proceed independently. After storage, authentication and catalog work can proceed in parallel. Later, UI-002 and UI-003 can run concurrently with disjoint route/component reservations. Shared contracts, migrations, API clients, and lockfiles remain serialized by explicit reservations. File areas above do not override runtime conflict detection.

## Deferred backlog (do not import as release tasks)

Native Android rewrite/APK distribution; remote/user-owned ComfyUI servers; public registrations/admin roles; hostile-user execution isolation; ordinary workflow JSON/canvas editing; automatic custom-node/model installation; live mask drawing; arbitrary custom frontend extension execution; automatic video transcoding; permanent deletion of original ComfyUI files; distributed agents or databases.
