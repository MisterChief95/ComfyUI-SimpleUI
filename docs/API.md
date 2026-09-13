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
| GET | `/api/workflows` | `Page<{ id, name }>` for the session's own workflows |
| POST | `/api/workflows?name=…` | Body is the ComfyUI **API JSON** itself → `201 { id, name }`; a refused graph is `422` |
| GET | `/api/workflows/{id}/controls` | `ControlSchema` for the stored revision; `404` if the session does not own it |
| GET | `/api/media` | `{ items, next_cursor }` gallery for the session's own media (`?cursor=`, `?limit=`) |
| POST | `/api/media/import` | One-time baseline index of the configured output folder. **Local gate.** `409` when refused |
| GET/HEAD | `/api/media/{id}/file` | The original bytes, with range support; `404` when not owned |
| GET/HEAD | `/api/media/{id}/download` | Same bytes under the original filename; never transcoded |
| GET | `/api/media/{id}/thumbnail` | `image/jpeg` preview |
| PUT | `/api/media/{id}` | `{ hidden?, favorite? }` → `{ ok: true }` |

Every route above is registered ahead of the `/api/{path:path}` catch-all in
`backend/app/main.py`; FastAPI matches in registration order, so a router
included after it could never run.

`/api/generations`, `/api/events`, and `/api/uploads` are specified in
[ARCHITECTURE.md](ARCHITECTURE.md) and are not implemented yet.

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
