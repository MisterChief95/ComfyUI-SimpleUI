# Release review (RELEASE-001)

Point-in-time review against [ARCHITECTURE.md](ARCHITECTURE.md), dated 2026-09-16. Task
completion and current status live in the dibs task database (`python
"<dibs script>" list --workspace .`), not in this document; this is a snapshot of evidence,
not a substitute for that source. PWA-001 (installable Android/PWA experience) was
**cancelled** at the user's request before this review and is treated as descoped below, not
as a still-open item.

## What actually runs today

One FastAPI process (`backend/app/main.py`) serves the built SvelteKit SPA and every API
group ARCHITECTURE.md's "API and execution contract" table calls for: `/api/session`,
`/api/settings`, `/api/catalog`, `/api/workflows`, `/api/generations`, `/api/events`,
`/api/media`, `/api/uploads`. All eight routers are registered in `create_app()`. 338
backend tests pass (`cd backend && python -m unittest discover -s tests -v`, temporary venv
per `requirements.txt`'s own comment since none is committed); `cd frontend && npm run check
&& npm run build` both pass.

Frontend routes: `/` (health check), `/workflows` (import, list, remove, per-workflow
mapping editor), `/workflows/[id]` (mapping editor), `/generation` (workflow picker),
`/generation/[id]` (generate form, submit, live output preview), `/gallery`, `/history`,
`/settings`. This is the full navigation surface; there is no admin or hidden route.

## Evidence by requirement

| ARCHITECTURE.md requirement | Status | Evidence |
| --- | --- | --- |
| Import API JSON, derive controls, mapping editor | Done | MAPPING-001/002; `backend/tests/mapping/` (36+ tests); UI-004 split the mapping editor onto its own `/workflows/[id]` page |
| Image and video generation, first-release requirement | Done, live-verified | GEN-002/GEN-003; see "Live ComfyUI evidence" below |
| No node canvas, no automatic model install, no admin role, no native Android | Held | Not present anywhere in the codebase; PWA-001 (the only Android-adjacent task) is cancelled, not silently dropped |
| Profiles, passwords, sessions, CSRF/origin checks | Done | AUTH-001; `backend/tests/auth/` |
| Owner-scoped everything (workflows, media, generations, uploads, events) | Done | Every repository method takes `owner_id`; `backend/tests/test_integration.py`'s ownership-isolation tests across workflows/media/generations |
| Raw catalog stays server-side; sanitized projection | Done | `backend/app/catalog/normalize.py`; `tests/fixtures/catalog/sanitized_projection.expected.json` self-check |
| Submission sequence (idempotent request key, seed resolution, upstream correlation) | Done | GEN-002; `backend/tests/generations/test_generations.py`'s `SubmissionTests` |
| Reconcile terminal execution with history, including cached nodes | Done, and repaired live | GEN-003 found and fixed two related gaps: `reconcile()` never published a completion event on the only path that ever finishes a real generation, and nothing re-invoked reconciliation while a generation was being watched. Both fixed; see `backend/app/generations/service.py` and `backend/app/events/routes.py` |
| Event delivery is advisory; reconnect re-fetches | Done, but see note | `/api/events` is a real WebSocket, owner-filtered, throttled. **The application never opens a connection to ComfyUI's own `/ws` endpoint** — confirmed by live capture (`tests/fixtures/catalog/capabilities.live.json`). Every real transition goes through history/queue polling instead, which is why GEN-003 had to tie that polling to the socket's own lifetime |
| Cancellation targets an owned prompt; no global fallback | Done, live-probed | `GenerationService.cancel()`; pending-job cancellation confirmed live including its documented race risk; running-job cancellation stays correctly disabled since `ComfyClient` exposes no per-job cancel |
| Durable filesystem index, one-time baseline scan | Done | MEDIA-001/002; `backend/app/media/service.py`'s `baseline_status`/`import_baseline`; confirmed live this session (`{"indexed": 493, "unresolved": 0}` against the reviewer's real ComfyUI output folder) |
| Streaming with byte ranges, opaque IDs, no static output mount | Done | `backend/app/media/routes.py`'s `stream`/`download`; `backend/tests/media/` |
| SQLite persistence, migrations, foreign keys | Done | `backend/migrations/001_initial.sql`; `ON DELETE CASCADE`/`SET NULL` used correctly (confirmed when adding the workflow-delete endpoint in UI-004) |
| **Database backup facility** | **Partially done — operator-facing gap** | `Database.backup()` exists (`backend/app/storage/db.py`) but nothing calls it: no CLI, no scheduled job, no button. The Settings page even exposes a `backup_dir` field (`frontend/src/routes/settings/+page.svelte`) that is stored but **never read by any backend code** (`grep -rn backup_dir backend/app` finds only its own `Spec` declaration). An operator who fills in that field gets no backup. This is a real, previously undocumented gap — see "Known limitations" |
| Local recovery (password reset, no admin, no remote reset) | Done | `docs/RECOVERY.md`; `python -m app.auth.recovery` CLI, tested |
| Responsive: phone/tablet/desktop, 360/768/1440, touch targets ~44px, no horizontal scroll | Spot-checked this review, not automated | `--touch-target: 2.75rem` (44px) token confirmed live; `/` and `/workflows` confirmed no horizontal scroll at 360×740 and 768×1024 in a real browser. No automated visual-regression suite exists; this remains a manual/spot-check item, not a continuously-verified one |
| Settings scopes (host, appearance, generation, gallery, history, diagnostics) | Partially done | Host settings (`comfy_url`, `comfy_input_dir`, `comfy_output_dir`, `backup_dir`, pending caps) and history preferences exist and are wired; per-profile appearance/theme preferences were not independently re-verified this review |
| PWA delivery, Android installation | **Cancelled** | PWA-001 cancelled at the user's explicit request (real-device testing was never available). No manifest, no service worker, no install flow exists. This is now the documented shape of the release, not a deferred gap — see "Known limitations" |
| Privacy: isolation through the app, not hostile-tenant sandboxing | Correctly stated, not overclaimed | ARCHITECTURE.md and `docs/RECOVERY.md` both state this explicitly and consistently; no file in `docs/` or `README.md` claims sandboxing or universal custom-node support |

## Live ComfyUI evidence (new this review)

A real ComfyUI 0.35.0 installation was reachable this session and exercised through the
running application itself, not curl alone:

- One image generation and one separate video generation were each submitted from the real
  Generate page, correctly reached `output_state: 'ready'`, and rendered inline (UI-006).
- This live video run caught a real bug no synthetic fixture modeled: `SaveWEBM`'s history
  entry carries an `"animated": [true]` sibling key that the output scanner mis-treated as a
  malformed descriptor, silently downgrading `ready` to `partial`. Fixed in GEN-003.
- Cancellation was probed live: `POST /queue` delete works for a still-pending job (two
  attempts lost the race on a fast GPU — empirically confirming the documented risk rather
  than contradicting it — a third succeeded cleanly and the job never reached history).
- 2673 node classes and 34 installed custom node packs were observed and recorded.

Full detail: [COMPATIBILITY.md](COMPATIBILITY.md), `tests/fixtures/catalog/capabilities.live.json`,
`tests/fixtures/outputs/history_{image,video}.live.json`.

**What remains unverified even after this capture:** `POST /upload/image` and the real
video-input loader contract (not exercised live), running-job cancellation (never offered by
the app, so unobservable), and output descriptor shapes for video packs other than the core
`SaveWEBM` (e.g. VideoHelperSuite, installed but not exercised).

## Known limitations (for the operator)

1. **No operator-facing backup.** The Settings page's "Backup folder" field does nothing yet;
   `Database.backup()` exists but nothing calls it. Until this is implemented, an operator
   must protect `data/app.sqlite3` and their media folders by their own means (e.g. stopping
   the server and copying `data/` with a file-level backup tool).
2. **No PWA / Android installation.** PWA-001 was cancelled. The app is a responsive web UI
   reachable from a phone or tablet browser on the home network; it is not installable, has
   no offline mode, and has no service worker.
3. **The app never opens ComfyUI's own live WebSocket.** All state comes from polling
   `/queue` and `/history`. This works (confirmed live) but means progress granularity is
   bounded by the ~1s poll tied to an open `/api/events` connection, not by ComfyUI's own
   per-node events.
4. **Upload and running-job cancellation are unverified against a live ComfyUI.** Their
   synthetic test coverage stands; nothing observed this review contradicts it, but nothing
   confirms it either.
5. **Responsive/accessibility conformance is spot-checked, not continuously tested.** No
   automated visual-regression or axe-style accessibility suite exists.

## Not reviewed this pass

Per-profile appearance/theme settings, prompt search, favorites, and clear-own-history were
not independently re-exercised (UI-003/MEDIA-002 already cover them with passing tests, and
nothing in this session's changes touched that code).

## Mobile display fit — UI-017 (2026-10-02)

The mobile main content now uses 14px body text, 13px button labels, 10px horizontal
button padding, 12px content gaps/page padding, and 20px page headings. Desktop and
tablet tokens remain unchanged. The run toolbar uses an accessible icon for Design
on narrow phones and reserves at least 96px for the workflow selector; its name no
longer disappears at 320px. Long content buttons may wrap instead of overflowing.
The existing coarse-pointer rules still provide 44px control heights and 16px input
text; mouse-pointer controls remain 34px high with 14px input text.

Verification used disposable `.dibs/codex-browser-data` and a separate multi-user
`.dibs/codex-mobile-auth-data` with a synthetic test password. No pre-existing user
gallery or credentials were accessed. Synthetic image tiles were used for gallery
inspection. Sign-in succeeded, and the Default/Switch profile header remained clear
of the content and bottom navigation on phones.

| Pages | Viewport widths checked | Observed fit |
| --- | --- | --- |
| Run, designer, workflows, gallery, history, settings | 320, 375, 430, 820, 1440 CSS px | Document scroll width equaled its client width. All measured buttons, inputs, selects, textareas, headings and labels stayed within the viewport except intentional horizontally scrollable jump chips. |
| Sign-in and signed-in multi-user header | 320, 375, 430 CSS px | No horizontal overflow; profile/password fields, sign-in action, profile switch and navigation remained visible. |

Pages with a native vertical scrollbar had 10px less client width than the requested
viewport; this was measured explicitly, rather than treating hidden body overflow as
proof of fit. Screenshots were inspected for clipping, wrapping and fixed-bar clearance,
including the phone designer, synthetic gallery, history, settings, workflows, sign-in,
and tablet/desktop run/designer views. The 375px run screenshot also verifies light-theme
contrast and the smaller size chips with both width/height inputs visible.

Local evidence: `.dibs/mobile-fit-main-measurements.json` (30 page/viewport records),
`.dibs/mobile-fit-measurements.json` (sign-in/header observations), and
`.dibs/mobile-fit-{page}-{width}.jpg` screenshots. These are local ignored verification
artifacts, not application data or committed fixtures.

Checks: `npm run check` reported zero errors/warnings; `npm test` passed 40 tests;
`npm run build` passed. The Svelte analyzer found only the existing root-path navigation
advisories in RunView; the changed markup and styles passed compiler/type checks.
This is resized desktop-browser verification, not a physical-phone or soft-keyboard
test. VERIFY-002 still owns quota failure, interrupted uploads, live latent previews,
and its remaining release evidence; this review does not claim those checks.

## Browser verification pass — VERIFY-002 (2026-10-02)

Run against the dev backend/frontend (resized desktop browser). Observations:

- **Draft persistence failure:** with `Storage.prototype.setItem` forced to throw
  `QuotaExceededError`, editing the prompt on the run page showed the warning
  "draft on this device — it will be lost on reload"; no crash or console error.
- **Two-tab layout conflict:** a second writer saved the layout (revision 5 -> 6) while
  the designer was open; saving an edit (section columns 1 -> 2) produced the
  "Changed elsewhere" banner ("No section or item changes; the saved revision changed")
  with Reload (discard mine) / Keep mine and overwrite / Dismiss. Reload (which asks
  `confirm()` when dirty) cleared the banner and restored the saved value (columns 1).
  The server layout was unchanged by the discarded edit. "Keep mine" was not exercised.
- **Interrupted upload:** a 30 MB multipart POST to `/api/uploads` aborted after 30 ms
  failed client-side; `GET /api/uploads` stayed empty and no `verify-abort.png` file
  appeared under `data/`.
- **Light theme:** designer and run page screenshots at desktop width were legible
  (text, section headers, accent buttons, inspector). Not measured for contrast ratios.
- **Sign-in / phone multi-user header / 320px widths:** covered by the UI-017 section above.
- Incidental: one run-page screenshot included an existing generated output image;
  nothing from it was recorded or acted on.

Not verified (blocked): live latent previews (needs ComfyUI restarted with
`--preview-method auto`, not done without the user's go-ahead); live ComfyUI cancel/retry
for FEAT-003 (ComfyUI not running here); compare drawer value table with real saved values.

## Live ComfyUI 0.38.0 verification — VERIFY-003 (2026-10-02)

Workflow `Krea2-Simple.api` (Ollama prompt step disabled via `54:switch=false`; with it
enabled and Ollama not running, the run failed cleanly with the upstream
`ConnectionError` recorded in `error.execution` and `output_state: unavailable`).

- **Cancel (running):** a running generation appeared in `GET /api/generations?status=active`;
  `POST .../cancel` -> `204`; status became `cancelled`; ComfyUI `/queue` was empty afterwards.
- **Retry:** `POST .../retry {request_key}` on the cancelled generation -> `201` with
  effective values identical to the source; it ran to `succeeded`/`ready`. A second retry
  queued behind it was cancelled while queued.
- **Cancel after finish:** `409`, as documented.
- **Previews:** over an 8-step run `/api/events` delivered execution_start, progress,
  progress_state, executing, executed and execution_success frames but **no preview
  frames**; ComfyUI appears to be running with the default `--preview-method none`.
  Live latent previews remain unverified.
- Note: early runs used 60 steps instead of the workflow's 8 (my mistake); the later
  preview run used 8.

### Follow-up with `--preview-method auto` and Ollama running (2026-10-02)

- **Previews:** an 8-step run delivered 8 `preview` frames (`image/jpeg`, base64) on
  `/api/events`, each with the owning `generation_id`, before `execution_success`.
  Live latent previews are verified.
- **Ollama prompt step enabled, unedited:** the Ollama server was reachable, but the
  workflow's saved model (`hf.co/DuoNeural/Gemma-4-E4B-Abliterated-GGUF:Q4_K_M`) is not
  installed there, so ComfyUI failed node 50 with a 404 and the error was recorded.
- **Finding:** selecting the installed model via `61:model` is rejected with `422`
  ("not an installed choice"): ComfyUI's `OllamaConnectivityV2.model` option list is empty
  in `/object_info` (the ComfyUI frontend fills it in dynamically), even after
  `POST /api/catalog/refresh`. So the app cannot edit that control for this node; an
  unedited value passes through. A successful run with Ollama enabled was not achieved.
