# ComfyUI compatibility record

Owner task: COMPAT-001, reopened by GEN-003/RELEASE-001. This file records what has actually
been established about the ComfyUI installation this application targets, and what has not.

## Status: live gate open for image and video (2026-09-16)

A real ComfyUI installation (0.35.0) was reachable this session and was exercised **through
the running application itself**, not curl alone: one image generation and one separate
video generation were each submitted from the actual Generate page, reconciled by
`GenerationService`, correctly reached `output_state: 'ready'`, and rendered inline (UI-006).
The evidence lives in `tests/fixtures/catalog/capabilities.live.json` and
`tests/fixtures/outputs/history_{image,video}.live.json`, alongside (not replacing) the
original synthetic set. `tests/fixtures/MANIFEST.json`'s `live_gate` reflects this.

This does **not** mean everything is verified: upload (`POST /upload/image`) remains
unprobed. The live ComfyUI WebSocket and targeted running-job cancellation were exercised on
2026-10-02 (GEN-004, ComfyUI 0.38.0; see below). See "Still not established" below and
`capabilities.live.json`'s `release_blockers_remaining`.

### What the live capture found

- **Confirmed:** `GET /object_info`, `GET /system_stats`, `POST /prompt`, `GET /history`,
  `GET /queue` all behave as documented. 2673 node classes and 34 installed custom node
  packs were observed (listed in `capabilities.live.json`; ComfyUI 0.35.0, Python 3.13.13,
  torch 2.13.0+cu132, one CUDA device).
- **A real bug the synthetic fixtures missed:** `SaveWEBM`'s history entry carries an
  `"animated": [true]` sibling key next to the real `images` descriptor list. The output
  scanner in `GenerationService._apply_history` treated that boolean as a malformed
  descriptor and downgraded a fully successful video save from `ready` to `partial`. Fixed
  in GEN-003; the synthetic `outputs/history_video.json` fixture now models this key too.
- **Superseded 2026-10-02 (GEN-004):** at capture time the application never opened
  ComfyUI's `/ws` endpoint, so every state transition came from history/queue reconciliation
  (hence GEN-003's reconciliation-publishes-events design). `generations/listener.py` now
  holds one `/ws?clientId=<submission client id>` connection and feeds
  `GenerationService.process_event()`. Observed live on 0.38.0: `execution_start`,
  `execution_cached`, `executing`, `progress`, `progress_state`, `executed`,
  `execution_success`, and `execution_interrupted` all reach the owning profile's
  `/api/events`. History reconciliation remains the terminal authority. Latent preview
  frames are implemented (metadata frame type 4, with the legacy type 1 attributed to the
  executing prompt) but have not been observed live, because the local ComfyUI ran
  without `--preview-method`.
- **Cancellation:** `POST /queue` with a `delete` body works for a still-pending job —
  confirmed by three attempts, two of which lost the race on this fast GPU (the job was
  promoted to running, or fully cache-hit and completed, before the delete landed and it
  silently no-op'd) and one of which cleanly removed a job that then never appeared in
  history. This empirically confirms the documented risk below rather than contradicting
  it. **2026-10-02 (GEN-004):** ComfyUI 0.38.0's `POST /interrupt {"prompt_id"}` interrupts
  only when that prompt is the running one and otherwise logs and skips. Cancelling a running
  job live produced `execution_interrupted` for it, and the queued job behind it still
  succeeded. Older builds ignore `prompt_id` and interrupt globally, so targeted interrupt
  is only used when the recorded ComfyUI version is at least 0.38.0 (GEN-005).

### Still not established

- The exact ComfyUI git revision (`/system_stats` reports a version string, not a commit).
- `POST /upload/image` / the actual video-input loader contract (INPUT-001's own tests are
  the only coverage; not exercised live).
- Output descriptor shapes for video packs other than the core `SaveWEBM` (e.g.
  VideoHelperSuite, present in the installed node packs but not exercised).
- Running-job cancellation on ComfyUI builds older than 0.38.0 (deliberately disabled there).
- Live latent preview frames (needs ComfyUI started with `--preview-method`).
- Submission's `node_errors` partial-acceptance path against a live installation (only
  synthetic `history_edge_cases.json` and its regression tests cover this).

## What the fixture set does establish

Synthetic fixtures pin the contracts that do not depend on a GPU: import validation,
literal and link classification, exact integer transport, catalog sanitization, event and
descriptor handling, and the diagnostics each failure mode must produce. They are enough
for CATALOG-001, MAPPING-001, MAPPING-002, and MEDIA-001 to develop and test against.

| Required case | Fixture |
| --- | --- |
| Core literals, untouched round trip | `graphs/image_basic.api.json` |
| Large seeds beyond JavaScript's safe range | `graphs/image_large_seed.api.json` |
| Repeated nodes, base and refiner stages | `graphs/image_repeated_stages.api.json` |
| Missing classes | `graphs/missing_class.api.json` |
| Custom and dynamic shapes, forceInput literals, flexible inputs, literal arrays | `graphs/force_input_and_flexible.api.json` |
| Image outputs | `graphs/image_basic.api.json`, `outputs/history_image.json` |
| Video outputs | `graphs/video_basic.api.json`, `outputs/history_video.json` |
| Multiple output branches | `graphs/multi_output_branches.api.json`, `events/partial_failure.events.jsonl` |
| Inactive controls | `graphs/inactive_controls.api.json` |
| Non-finite value rejection | `graphs/rejected/non_finite_values.api.json` |
| Seed retry policies | `expectations/seed_and_transport.json` |
| Latent batch size versus request count | `graphs/latent_batch_size.api.json` |
| Sanitized catalog choices | `catalog/sanitized_projection.expected.json`, `graphs/image_loader_input.api.json` |
| Loader contracts | `catalog/capabilities.synthetic.json`, `graphs/image_loader_input.api.json` |
| Execution events, including a fully cached run | `events/*.jsonl` |
| Cancellation capabilities | `events/cancelled_run.events.jsonl`, capability record |
| Older metadata shape | `catalog/object_info.legacy-shape.json` |

Self-check: `python -m unittest discover -s tests/fixtures -v` (12 tests).

## Contract notes that carry risk

These are the places where a synthetic fixture could be wrong, so they need live
confirmation before anything depends on them.

**Output descriptors.** Core `SaveImage`, `SaveVideo`, and `SaveWEBM` are expected to report
under a `ui.images` list of `{filename, subfolder, type}`. Third-party packs may use `gifs`
or a pack-specific key, and may include an absolute host path. `outputs/history_video.json`
includes an unknown descriptor key on purpose: an unrecognized key must become an unresolved
output, never a silently empty result. Do not key media classification off `images` alone.

**Submission is not all-or-nothing.** A `200` response carrying a `prompt_id` can also carry
`node_errors` for individual output branches. Execution status and output availability are
separate axes. `outputs/history_edge_cases.json` holds the accepted-with-errors shape, the
fully rejected shape, a success with no saved media, a `temp`-only preview result, and an
interrupted run.

**Submission idempotency is unverified.** Do not assume supplying a prompt ID makes
submission idempotent on any version. Ownership and deduplication must come from the
application's own request key, and a lost response must enter `submission_unknown` and look
for correlation evidence rather than resubmitting.

**Cancellation.** A pending job can be removed by name through the queue route. The only
documented way to stop a *running* job on older installations is a global interrupt, which
stops whichever job is running at that instant. A queue read before the call does not make
it safe, because the running job can change in between. Running-job cancellation must stay
disabled and explained as unavailable unless a probe confirms a per-job cancel that names
the prompt ID. Global queue or history clearing is never exposed.

**Cached runs emit no `executed` event for cached nodes.** `events/fully_cached_run.events.jsonl`
reaches a terminal state with only `execution_cached` and `execution_success`. Terminal
execution must be reconciled against history, not inferred from node completion events.

**Event ownership.** `events/foreign_and_preview.events.jsonl` includes a binary preview frame
with no prompt ID. An event whose owner cannot be proven is dropped, not broadcast.

**Catalog sanitization.** The raw catalog contains shared ComfyUI input-directory filenames.
The client projection withholds them and substitutes an owned-input reference; the backend
stages the upload and substitutes the real filename at submission. The self-check asserts
that none of the withheld names appear in the projection.

**Metadata shape drift.** `catalog/object_info.legacy-shape.json` omits `python_module`,
`input_order`, and the hint keys, and gives combo inputs as a bare option list with no
trailing options object. Normalization must tolerate both shapes. Wildcard types and
dynamically named inputs are not fully described by `object_info`, so exact name and type
matching must not be the sole rejection test.

## Release blockers

1. ~~No live ComfyUI revision, node pack list, or device report recorded.~~ **Resolved
   2026-09-16** — see `capabilities.live.json`.
2. ~~No real image generation captured. Required before claiming image support.~~
   **Resolved 2026-09-16** — reproduced end to end through the running application.
3. ~~No real video generation captured. Required before claiming video support.~~
   **Resolved 2026-09-16** — reproduced end to end through the running application; this
   capture also caught and fixed a real reconciliation bug (GEN-003).
4. ~~Video output descriptor shapes are assumed from upstream source, not observed.~~
   **Resolved for `SaveImage`/`SaveWEBM` 2026-09-16.** Other video-capable packs (e.g.
   VideoHelperSuite) remain assumed, not observed.
5. **Partially resolved 2026-09-16.** Pending-job cancellation (`POST /queue` delete) is
   confirmed working, including its documented race risk on a fast installation.
   Running-job cancellation remains correctly unavailable by construction (the app never
   calls `POST /interrupt`) and is therefore still unprobed by design, not by omission.
   **Resolved for ComfyUI ≥ 0.38.0 on 2026-10-02**: targeted `POST /interrupt` was verified
   live (GEN-004) and is version-gated (GEN-005).
6. No real loader contract confirmed for image/mask/video uploads; `POST /upload/image`
   was not exercised live. Still open.

### VERIFY-001 release decision — 2026-09-12 (America/Phoenix), superseded 2026-09-16

The original decision below is kept for history; GEN-003/RELEASE-001 superseded it once a
live ComfyUI installation became reachable.

> Live-ComfyUI image and video generation evidence has **not** been captured in this
> environment. No ComfyUI instance is available for this verification run, and VERIFY-001
> does not treat synthetic results as live evidence. In its place, the repository has only:
>
> - synthetic compatibility inputs and expected results under `tests/fixtures/`;
> - `backend/tests/generations/test_generations.py` (GEN-002), which replays synthetic image,
>   video, fully-cached, partial-failure, duplicate-delivery, disk-full, and restart histories;
> - `backend/tests/media/test_capture.py` and `backend/tests/media/test_filters_history.py`
>   (MEDIA-002), which verify private output capture, gallery ownership, missing files, and
>   retained history against temporary files.
>
> Accordingly, release claims for real image generation and real video generation are
> explicitly blocked. A release decision must wait for the manual live run described below;
> no installation, startup, connection attempt, or fabricated live result was performed by
> VERIFY-001.

### RELEASE-001 release decision — 2026-09-16

A real ComfyUI installation was reachable and was exercised through the running application
for one image and one separate video generation, each reaching `output_state: 'ready'` with
no manual intervention. Blockers 1 through 4 above are resolved; blocker 5 is partially
resolved (pending-job cancellation confirmed, running-job cancellation correctly stays
disabled by construction); blocker 6 (upload/loader contracts) remains open and is not
claimed here. Release claims for image and video generation are therefore supportable;
claims about upload-driven inputs, running-job cancellation, or any node pack beyond the
ones actually exercised are not, and must not be implied by omission elsewhere in the docs.

## Reopening further gates

The remaining unverified surface — `POST /upload/image` and the actual video-input loader
contract, and a genuine per-job running cancellation probe (if a future ComfyUI or node
pack exposes one) — follows the same procedure that opened the image/video gate: exercise
it through the running application (not curl alone) against a reachable ComfyUI, add a
sanitized capture beside the existing live ones, and update `capabilities.live.json`'s
`release_blockers_remaining`. Sanitize every capture: no real prompt text, no real
filenames, no host paths, no credentials.
