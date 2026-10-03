# API contracts

Frozen baseline from FOUNDATION-001. Feature tasks add endpoints; they do not
change the rules below without amending this document and both contract files.

Definitions live in two mirrored files — change them together:

- `backend/app/contracts.py` (pydantic, authoritative)
- `frontend/src/lib/contracts.ts` (TypeScript mirror)

## Transport rules

| Rule | Detail |
| --- | --- |
| Exact integers | Seeds, IDs, byte counts and any value that can exceed 2^53 cross the wire as decimal strings (`ExactInt`, pattern `^-?(0\|[1-9][0-9]*)$`). The browser never `Number()`s them; Python converts validated strings when building the upstream graph. Sliders never bind to an `ExactInt`. |
| Ownership | `owner_id` is derived from the session and appears in responses only. Request bodies forbid unknown fields (`extra="forbid"`), so a client-supplied `owner_id` is rejected, not trusted. |
| Revisions | Every editable resource carries `revision` (integer, starts at 0). Writes send `expected_revision`; a mismatch is `409 conflict` and the newer state is preserved. |
| Pagination | Cursor-based only: `Page<T> { items, next_cursor }`. `next_cursor: null` means the end. Cursors are opaque; do not parse them client-side. |
| Errors | Every non-2xx body is an `ErrorEnvelope`. |
| Request ids | Every response carries an `x-request-id` header, echoed in `error.request_id`. |

## Error envelope

```json
{
  "error": {
    "code": "not_found",
    "message": "No API route /api/nope",
    "request_id": "cd2999ead02c48d7839f6e8541e9e44d",
    "details": [{ "field": "seed", "code": "string_pattern_mismatch", "message": "..." }]
  }
}
```

`code` is one of `bad_request`, `unauthorized`, `forbidden`, `not_found`,
`conflict`, `unprocessable`, `rate_limited`, `upstream_unavailable`,
`internal`. `details` carries per-field validation problems (422) and is empty
otherwise. Internal errors never leak exception text.

## Control descriptors

`ControlSchema` is the presentation projection of one workflow revision. It is
owned and revisioned, and separates blocking errors from warnings:

```json
{
  "owner_id": "profile-1",
  "revision": 3,
  "workflow_id": "wf-1",
  "controls": [
    {
      "binding_id": "b1",
      "node_id": "3",
      "class_type": "KSampler",
      "input_name": "seed",
      "logical_type": "int",
      "value": "12345678901234567890",
      "component": "seed",
      "group": "generation",
      "order": 0,
      "label": "Seed",
      "help_text": null,
      "constraints": { "min": null, "max": null, "step": null, "exact_min": "0", "exact_max": null },
      "options": null,
      "multiline": false,
      "inference_reason": "input name matched seed rule",
      "unresolved": [],
      "raw_metadata": null
    }
  ],
  "blocking": [],
  "warnings": []
}
```

- `value` encoding follows `logical_type`: `ExactInt` string for `int`, number
  for `float`, boolean for `boolean`, string for `string`/`enum`/`file`.
- `component` and `group` are closed enumerations. Svelte selects a component
  from that registry only; the backend never sends markup or code.
- `unresolved` non-empty means the user must correct the mapping before
  submission. `blocking` stops submission for the whole workflow; `warnings`
  do not.
- `raw_metadata` preserves upstream node metadata for diagnostics. It is
  rendered as plain text, never executed or injected as HTML.

## Endpoints

| Method | Path | Response |
| --- | --- | --- |
| GET | `/api/health` | `Health { status, version, time_ms }` (`time_ms` is an `ExactInt`) |
| any | `/api/*` (unmatched) | `404` `ErrorEnvelope` — never the SPA shell |
| GET | `/api/session` | `SessionInfo { authenticated, multi_user, anonymous, profile, csrf_token }` |
| POST | `/api/session` | Sign in `{ name, password }` → `SessionInfo`; `409` in single-user mode |
| DELETE | `/api/session` | Sign out → `SessionInfo` |
| GET | `/api/profiles` | `{ profiles: [{ id, name, is_default }] }` — names only, for the sign-in picker |
| POST | `/api/profiles` | Create `{ name, password }` → `201`. **Local gate.** |
| POST | `/api/profiles/me/password` | `{ current_password, new_password }` → `204`; signs that profile out everywhere |
| GET | `/api/settings` | `{ host, profile, host_writable, multi_user }` |
| GET | `/api/settings/keys` | Writable keys per scope |
| PUT | `/api/settings/profile` | `{ key, value }` → `204`; owner is the session |
| PUT | `/api/settings/host` | `{ key, value }` → `204`. **Local gate.** |
| POST | `/api/settings/multi-user/activate` | `{ default_password }` → `204`. **Local gate.** |
| POST | `/api/settings/multi-user/deactivate` | `204`. **Local gate.** |
| GET | `/api/catalog` | `CatalogSnapshot { freshness, nodes, capabilities }` — the sanitized projection; no upstream call |
| POST | `/api/catalog/refresh` | `CatalogSnapshot` after one coalesced fetch. Server-side cooldown; no client can force it |
| GET | `/api/workflows` | `Page<WorkflowInfo>` for the session's own workflows; `WorkflowInfo = { id, name, current_revision: int, updated_ms: ExactInt string }` |
| POST | `/api/workflows?name=…` | Body is the ComfyUI **API JSON** itself → `201 WorkflowInfo`; a refused graph is `422` |
| PATCH | `/api/workflows/{id}` | `{ name }` (1–120 chars) → `WorkflowInfo`; `404` if not owned |
| PUT | `/api/workflows/{id}/graph` | Body is the ComfyUI **API JSON** (same `parse_graph` rules as import) → `{ workflow: WorkflowInfo, revision, added: [binding_id], removed: [binding_id] }`. Stores revision N+1 and makes it current in one transaction; `added`/`removed` compare control binding ids of the old and new schema. Layout, corrections and presets are untouched. `422` for a refused graph (nothing changes), `409` if the workflow changed concurrently |
| GET | `/api/workflows/{id}/controls` | `ControlSchema` for the stored revision; `404` if the session does not own it |
| GET | `/api/workflows/{id}/layout` | `{ workflow_id, revision, schema_signature, layout, stale_bindings }`; `revision: 0` with `layout: null` means nothing is saved (use the automatic layout). `stale_bindings` are saved binding ids absent from the current schema. `404` if not owned |
| PUT | `/api/workflows/{id}/layout` | `{ layout: LayoutDoc, expected_revision }` → the same `WorkflowLayout`. Version 1 supports `control` and `aspect_ratio` items; both aspect-ratio bindings count toward uniqueness and the 1000-binding cap. New pairs require existing integer bindings; previously saved pairs may retain absent bindings as stale. `422` on any [layout invariant](UI_DESIGNER.md#layout-document-v1), `409` on a revision mismatch (stored document untouched). Ordinary control layouts need no node catalog |
| DELETE | `/api/workflows/{id}/layout` | `204`; reverts to the automatic layout (idempotent). Optional `?expected_revision=N` (`0` = nothing saved): a different stored revision is `409` and nothing is deleted. `404` if the workflow is not owned |
| GET | `/api/workflows/{id}/presets` | `Preset[]`, most recently updated first; `Preset = { id, name, values: { binding_id: string \| bool \| int \| float }, revision, created_ms, updated_ms }` (`*_ms` are ExactInt strings; integers beyond 2^53 come back as ExactInt strings). `404` if the workflow is not owned |
| POST | `/api/workflows/{id}/presets` | `{ name, values }` → `201 Preset`. `422` outside the [limits](UI_DESIGNER.md#presets), `409` at 100 presets |
| PUT | `/api/workflows/{id}/presets/{preset_id}` | `{ name?, values?, expected_revision }` (at least one of `name`/`values`) → `Preset`; `409` on a revision mismatch, `404` if missing or not owned |
| DELETE | `/api/workflows/{id}/presets/{preset_id}` | `204`; `404` if missing or not owned |
| GET | `/api/media` | `{ items, next_cursor }` gallery for the session's own media (`?cursor=`, `?limit=`, and filters `?media_kind=`, `?favorite=`, `?workflow_id=`, `?generation_id=`, `?created_after=`, `?created_before=`, `?prompt=`) |
| GET | `/api/media/tree` | `?path=` → `{ path, timezone: "UTC", count, children: [{ path, name, count }], breadcrumbs: [{ path, name }] }`, computed over the session's visible media |
| GET | `/api/media/suggestions` | `?q=TEXT&limit=10` → `{ items: string[] }`, owner-scoped known workflow names and saved scalar prompt/control values |
| POST | `/api/media/import` | One-time baseline index of the configured output folder. **Local gate.** `409` when refused |
| GET/HEAD | `/api/media/{id}/file` | The original bytes, with range support; `404` when not owned |
| GET/HEAD | `/api/media/{id}/download` | Same bytes under the original filename; never transcoded |
| POST | `/api/media/download-zip/preview` | JSON `{ ids: string[] }` → `{ entries: [{ id, filename, size }], skipped: [{ id, reason: "unavailable" }], total_bytes }` |
| POST | `/api/media/download-zip` | JSON `{ ids: string[] }` or URL-encoded `selection` containing that JSON plus `csrf_token`; streams `selected-media.zip` |
| GET | `/api/media/{id}/thumbnail` | `image/jpeg` preview |
| PUT | `/api/media/{id}` | `{ hidden?, favorite? }` → `{ ok: true }` |

Every route above is registered ahead of the `/api/{path:path}` catch-all in
`backend/app/main.py`; FastAPI matches in registration order, so a router
included after it could never run.

Batch ZIP downloads accept 1–200 IDs (each 1–100 characters), deduplicate repeated
IDs, and cap available original bytes at **2 GiB** (`413` above the cap; `422`
for invalid IDs/count). The download request body is capped at **64 KiB** (`413`).
Every ID is owner-checked; missing, foreign, changed, or unreadable files all
report only `unavailable`, without revealing another owner's filenames or paths.
The archive uses ZIP_STORED, ZIP64, and 64 KiB reads, with no temporary archive
or whole-media buffer. Original basenames are used (unsafe extraction characters
are replaced), with case-insensitive collisions disambiguated as `name (2).ext`.
`manifest.json` is reserved for a final report containing `included` entries and
`skipped` IDs; an I/O failure after a member starts marks that member `incomplete`
and gives its archive filename. Such incomplete members should be discarded.
`X-Media-Skipped` counts pre-stream skips; the manifest is authoritative for
failures during streaming. Empty selections of available files yield a manifest-only ZIP.
No real filesystem paths appear in either response.

JSON requests use the usual Origin and X-CSRF-Token checks. Native form downloads
retain the same origin check and accept the per-session token in `csrf_token`
instead of a header. The gallery previews with authenticated JSON, reports
skipped items, then submits a native form in the current browsing context so archive
bytes are downloaded by the browser without a JavaScript Blob. The preview is
advisory: download rechecks ownership, availability, and size; later failures
are recorded in the manifest. A successful attachment keeps the gallery open;
an HTTP failure from the form displays the error response in the current page.

Gallery suggestions require at least two non-whitespace characters (`q` max 500;
shorter queries return an empty list). `limit` is 1–10. A bounded scan uses the
owner/time index to read only the latest 100 generations, at most 65,536 characters of each
saved values snapshot and its first 200 controls. Larger snapshots are skipped.
Distinct matching strings/numbers/booleans and owner-matched workflow names are returned;
empty, nested, over-500-character, and slash/backslash-bearing values are excluded
to avoid surfacing filesystem paths. These values remain generation history,
not global tags. Purged snapshots contribute no control values; workflow names
can still be known from retained generation provenance. Both suggestions and the gallery
`prompt` filter accept `search_field=any|prompt|model` (default `any`, preserving
the previous saved-value/workflow search). Matching is a literal, case-folded
substring, including Unicode; `%` and `_` have no wildcard meaning.
The Saved metadata input debounces requests by 250 ms,
uses a native keyboard-accessible datalist, and applies a selected suggestion.

Field scope uses the final input name in the stored `node_id:input_name` key
(or the whole key for legacy snapshots), case-insensitively, never UI labels or
inferred graph roles. `prompt` selects `text`, `prompt`, `positive`, `negative`,
`positive_prompt`, and `negative_prompt`; text inputs may contain non-prompt text.
`model` selects `model`, `model_name`, `checkpoint`, `checkpoint_name`, `ckpt_name`,
`unet_name`, `diffusion_model`, `diffusion_model_name`, `lora_name`, `vae_name`,
`clip_name`, `clip_name1`, `clip_name2`, and `clip_name3`. Custom input names remain
searchable through `any`. `any` includes all stored scalar values and current
owner-matched workflow names, including seeds, sampler names, numbers and booleans.
Neither keys, nested values, raw graphs nor file metadata/locators are searched.
Imported media without a generation never matches. Cleared snapshots contribute
no values; only `any` can still match a retained workflow association.

Gallery search terms are 1–500 characters (invalid fields/lengths return `422`).
Each request uses the owner/time generation index to inspect only the latest
1,000 generations, at most 65,536 snapshot characters and the first 200 controls
per generation; larger snapshots are skipped. Workflow names are capped at 501
characters. JSON is parsed once per generation, independent of output count,
and at most 1,000 matching generation IDs are passed to the owner-scoped media
query. Suggestions retain their smaller 100-generation/10-result bounds and
path exclusion. Search does not promise matches in older history or beyond
these snapshot limits. Gallery pages remain capped at 200 items; ordinary
media keyset paging uses the owner/time index, while random ordering still
scans/sorts the owner's matching media. No filesystem scan or new search index
is required. Keep `prompt` and `search_field` unchanged with the other filters
across pages; changing the field applies a fresh cursor when Apply is pressed.

Gallery virtual folders use `path=""` for **All media**, `Date/YYYY/MM/DD`
(year and month parents are also browsable), `Workflow/<opaque workflow ID>`
(displayed with the current workflow name), `Favorites`, `Videos`, and `Unsorted`.
Workflow IDs keep duplicate names, slashes, and renames unambiguous. Unsorted means
no owner-matched generation/workflow association, including deleted workflows.
Dates use media `created_ms` grouped in **UTC**, with inclusive midnight and
exclusive next midnight; the manual date filters retain their existing behavior.
Invalid virtual paths return `400`; unknown or foreign workflow IDs return an
empty folder with an “Unknown workflow” breadcrumb. No filesystem locators are returned.

Pass the same `?path=` to `GET /api/media` to browse a folder through the existing
list query. Folder constraints intersect all other filters. `?sort=newest` (default),
`oldest`, or `random` and `?cursor=` retain GAL-003's keyset behavior; retain the
same path, filters, and sort across pages and reset the cursor when any changes.
Random cursors retain their shuffle seed; a cursor with a different sort returns `400`.
Tree counts exclude hidden media, include unavailable indexed items, and describe
the folder **before manual filters**. They count indexed rows in SQLite without
filesystem scans. SQLite uses an owner index to restrict counts to the owner
(and the owner/time index for date ranges).

### Collections

All routes below use the session owner; mutations require the existing CSRF and
same-origin checks. Names are trimmed, 1–100 characters, unique per owner with
case-sensitive matching (`409` on duplicates, `422` on invalid input).

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/api/media/collections` | `{ items: [{ id, name, count }] }`, ordered by name; counts exclude hidden media |
| POST | `/api/media/collections` | `{ name }` → `201 { id, name }` |
| PUT | `/api/media/collections/{id}` | `{ name }` → `{ id, name }`; rename |
| DELETE | `/api/media/collections/{id}` | `{ ok: true }`; remove collection and links, retain all media |
| POST | `/api/media/collections/{id}/media` | `{ ids: [media_id, ...] }` → `{ changed }`; bulk add |
| POST | `/api/media/collections/{id}/remove` | Same body/response; bulk remove |

Bulk requests accept 1–500 IDs, deduplicate input, and are idempotent. Missing or
foreign collection/media IDs return `404` with no partial mutation. Collection
rename/delete also return `404` for foreign or missing IDs.

`GET /api/media?collection_id=<id>` intersects the collection with all other
filters and paging. Missing/foreign IDs produce empty results. The virtual tree
adds `Collections` and `Collections/<opaque id>` leaves. Names (including slashes)
appear in breadcrumbs; unknown/foreign leaves show “Unknown collection”. The
parent count counts distinct visible media assigned to any collection; leaf
counts count visible members. Empty collections remain browsable with count 0.
Walk mode follows collection siblings in server name order, retaining its existing
deduplication when media belongs to multiple collections.

`/api/generations`, `/api/events`, and `/api/uploads` are specified in
[ARCHITECTURE.md](ARCHITECTURE.md).

### Generation events (`WS /api/events`)

Advisory and owner-filtered (a socket only receives its own profile's frames; on
reconnect re-fetch `/api/generations`). Every frame is JSON
`{ "generation_id": string, "type": string, "data": object }`.

A backend task (`app/generations/listener.py`) holds one websocket to ComfyUI
(`/ws?clientId=simpleui`), feeds each ComfyUI event to the generation service and
reconnects with capped backoff (1 s up to 30 s, one warning per outage), reconciling
against ComfyUI's history after each connect. `type` is the ComfyUI event name,
`data` is its payload unchanged:

| `type` | `data` (main fields) |
| --- | --- |
| `execution_start`, `execution_cached`, `execution_success` | `prompt_id`, `timestamp` (cached: `nodes`) |
| `executing` | `node`, `prompt_id`; `node: null` closes the job |
| `progress` | `value`, `max`, `node`, `prompt_id` (per sampler step) |
| `progress_state` | `prompt_id`, `nodes` (per-node state snapshot) |
| `executed` | `node`, `output`, `prompt_id` |
| `execution_error`, `execution_interrupted` | error / interruption details |
| `reconciled` | `{}`; the stored generation changed through reconciliation, re-fetch it |
| `preview` | `{ "mime": "image/jpeg" \| "image/png", "image": "<base64>" }` |

`preview` frames carry a latent preview of the generation in `generation_id`. They are
sent only to the owner, only when that profile's `live_previews` setting is on, at most
2 per second per generation (excess frames are dropped), never larger than about 1.5 MB,
and are discarded when they cannot be attributed to a prompt. They exist only if ComfyUI
produces them: start it with `--preview-method auto` (or `latent2rgb`/`taesd`); the
default `none` sends no previews. Previews never replace the saved output.

`executed` frames are relayed without their `output` payload (only the node id and prompt id).

`POST /api/generations/{id}/cancel` -> `204`. A queued job is removed from ComfyUI's
queue; a running job is stopped with a **targeted** `POST /interrupt {"prompt_id"}` that
ComfyUI ignores unless that prompt is the running one (verified on ComfyUI 0.38.0; the
global form is never sent; only when the catalog's recorded ComfyUI version is >= 0.38.0, otherwise a queued job is only removed from the queue and a running one returns `409`), after which an `execution_interrupted` event and the
`cancelled` status follow. `409` when the generation is not safely cancellable (foreign,
unknown, finished); `503 upstream_unavailable` when ComfyUI cannot be reached.

`GET /api/generations?status=active` lists only the caller's pending/queued/running
generations (same pagination as the unfiltered list). `POST /api/generations/{id}/retry`
-> `201` submits a new generation from the source's saved effective-values snapshot with a
fresh request key; `409` when the source is still active, uncertain, or its snapshot/inputs
were purged. `can_retry` on a generation says whether this will be accepted.

`seed_policy: "increment"` advances from the value this profile last submitted for that
seed control in that workflow (an explicit edit counts), falling back to the imported
seed for the first run.

Combo inputs are read from the newer `["COMBO", {"options": [...]}]` form, falling back to the
legacy `[[...]]` list. Options keep the JSON type ComfyUI listed them with (numbers and booleans are not
stringified); a chosen option is submitted exactly as listed, and `1`, `1.0`, `"1"` and `true`
are matched by type (`1` equals `1.0`; `true` is not `1`). A `remote.route` list (same-host routes only) is fetched at catalog refresh. An enum with
no options is shown disabled ("No Options Present") and edits to it are rejected (`422`); its
saved value is still submitted unchanged. Unknown values for enums with options are `422` too.

`POST /api/generations` `edits` values are `string | boolean | number`, encoded like
`ControlDescriptor.value`: a real JSON boolean for `boolean` controls, a number for `float`,
an ExactInt string for `int` (a JSON integer is accepted and stored back as the string, with
every digit kept), and a string for the rest. For `boolean` controls the exact strings
`"true"` / `"false"` are also accepted (older clients sent them) and are stored as booleans;
any other value is a `422`.

## Sessions, CSRF, and the local gate

Implemented by AUTH-001 (`backend/app/auth/`). See
[RECOVERY.md](RECOVERY.md) for the lockout procedure.

| Rule | Detail |
| --- | --- |
| Single-user mode | A request with no session resolves to the Default profile, so the app works with no setup (`anonymous: true`). That implicit fallback disappears the moment multi-user mode is enabled, which is how activation invalidates every anonymous client at once. |
| Cookies | `simpleui_session` is opaque, HttpOnly, `SameSite=Strict`, and `Secure` over HTTPS. Only its SHA-256 hash is stored. Sessions expire 14 days after issue; an expired row is rejected and swept on use. |
| CSRF | Every state-changing request must carry `Origin` (or `Referer`) equal to the origin it addressed; a missing one fails closed. When a session cookie is present, `X-CSRF-Token` must also match the readable `simpleui_csrf` cookie, which is derived per session and is useless on another one. |
| Local gate | Host settings, profile creation, and the mode switch require a loopback socket peer **and** a loopback `Host`. `X-Forwarded-For`, `X-Real-IP`, `Forwarded`, and `X-Forwarded-Host` are never read: this process runs on the same host as ComfyUI and is not behind a trusted proxy by default. A reverse-proxy deployment needs an explicit trusted-proxy configuration. |
| Sign-in throttle | 10 failures per profile name in a rolling 15 minutes → `429`. Wrong password and unknown profile return the identical `401` message. |
| Passwords | scrypt (stdlib; `requirements.txt` pins no Argon2 binding). `verify_password` dispatches on the stored prefix, so switching to Argon2id later does not invalidate stored hashes. Minimum 8 characters. |
| Activation | Requires a Default password **and** a completed media baseline. `create_app` wires `AuthService.baseline_gate` to `MediaService.baseline_status`, so activation returns `409` until `POST /api/media/import` has indexed the configured output folder — multi-user mode is never enabled over an unscanned library. Changing the output folder to a different one invalidates the baseline again. |
| Deactivation | Refused with `409` while any profile other than Default exists. |
| Mode switch | Closes generation admission atomically and waits for unresolved submissions (`submitting`, `submission_unknown`) to reconcile before committing. In-flight submissions are waited for; new ones get `409`. On timeout admission reopens and nothing is changed. GEN-001's submit path wraps its work in `AuthService.admission.admit()`. |

## Recent text history

`GET /api/generations/recent-prompts?workflow_id=ID&binding_id=NODE:INPUT&q=TEXT&limit=20&cursor=...`
returns `{items: [{text, created_ms}], next_cursor}`. Values are distinct strings,
ordered by their latest submission, scoped to the signed-in profile and owned
workflow. `q` is a literal substring filter (not a wildcard expression); `limit`
is 1–100. Non-string bindings return 422, foreign/missing workflows 404, and
malformed cursors 400. With `store_history=false` the list is empty; purged
snapshots never contribute. The run-page Recent prompts sheet inserts plain text
at the textarea cursor, replacing the selection. Named saved snippets are deferred.

## Static serving and deep links

FastAPI is the only production server; Node is a build tool.

1. `/api/*` is matched first and always answers JSON.
2. An existing file under `frontend/build` is served (path resolved inside the
   build root; traversal is refused).
3. A missing path **with** a file extension is a `404` `ErrorEnvelope`.
4. A missing path **without** an extension returns `index.html`, so browser
   deep links such as `/gallery/42` work.

## Configuration

Environment variables, validated at startup (`backend/app/config.py`); every
problem is reported at once and startup fails rather than degrading silently.

| Variable | Default | Notes |
| --- | --- | --- |
| `SIMPLEUI_COMFY_URL` | `http://127.0.0.1:8188` | Must be an http(s) URL with a host |
| `SIMPLEUI_DATA_DIR` | `<repo>/data` | Created if absent |
| `SIMPLEUI_STATIC_DIR` | `<repo>/frontend/build` | Must contain `index.html` unless `SIMPLEUI_DEV` |
| `SIMPLEUI_DEV` | unset | `1` runs the API without static files (Vite serves the UI) |

## Commands

```cmd
cd backend && python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
cd backend && .venv\Scripts\python -m unittest discover -s tests -v
cd backend && .venv\Scripts\python -m uvicorn app.main:create_app --factory --port 8000

cd frontend && npm ci
cd frontend && npm run check
cd frontend && npm run build
cd frontend && npm run dev
```

Development uses two processes: `npm run dev` (port 5173) proxies `/api` to
uvicorn on port 8000, so the browser sees one origin, as in production.
Dependency locks are `backend/requirements.txt` (exact pins) and
`frontend/package-lock.json`.
