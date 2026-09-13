# ComfyUI compatibility record

Owner task: COMPAT-001. This file records what has actually been established about the
ComfyUI installation this application targets, and what has not.

## Status: live gate blocked

**No ComfyUI installation was reachable on this development host.** Nothing in this record
or in `tests/fixtures/` is observed behavior. The fixture set is synthetic and is labelled
as such in `tests/fixtures/MANIFEST.json`.

Discovery was limited to explicit configuration, as the task requires. What was checked:

| Check | Result |
| --- | --- |
| Environment variables matching `COMFY*` | none present |
| `http://127.0.0.1:8188/system_stats` | connection refused |
| `http://localhost:8188/system_stats` | connection refused |
| `http://127.0.0.1:8000/system_stats` and `:8189` | connection refused |
| Directory scan for names containing `comfy` under the user profile, Program Files, Program Files (x86), and drive roots | only this repository matched |
| Repository configuration convention | none exists yet; `backend/` is not implemented |

A guessed path is not explicit configuration. FOUNDATION-001 owns the real configuration
surface (ComfyUI URL plus input, output, and temp roots); until an operator supplies it,
there is nothing legitimate to connect to.

### Not established

- ComfyUI revision, version, Python and torch versions, device.
- Installed custom node packs.
- Real `object_info` output from this machine's installation.
- Any real image generation.
- Any real video generation.
- Real output descriptor shapes, especially for video.
- Whether this installation exposes a per-job cancellation route.

These are release blockers, recorded in `tests/fixtures/catalog/capabilities.synthetic.json`
and repeated at the end of this file. VERIFY-001 cannot claim image or video support from
fixtures alone.

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

1. No live ComfyUI revision, node pack list, or device report recorded.
2. No real image generation captured. Required before claiming image support.
3. No real video generation captured. Required before claiming video support.
4. Video output descriptor shapes are assumed from upstream source, not observed.
5. Cancellation capability is unprobed; running-job cancellation stays unavailable.
6. No real loader contract confirmed for video inputs; there is no universal video upload route.

### VERIFY-001 release decision — 2026-09-12 (America/Phoenix)

Live-ComfyUI image and video generation evidence has **not** been captured in this
environment. No ComfyUI instance is available for this verification run, and VERIFY-001
does not treat synthetic results as live evidence. In its place, the repository has only:

- synthetic compatibility inputs and expected results under `tests/fixtures/`;
- `backend/tests/generations/test_generations.py` (GEN-002), which replays synthetic image,
  video, fully-cached, partial-failure, duplicate-delivery, disk-full, and restart histories;
- `backend/tests/media/test_capture.py` and `backend/tests/media/test_filters_history.py`
  (MEDIA-002), which verify private output capture, gallery ownership, missing files, and
  retained history against temporary files.

Accordingly, release claims for real image generation and real video generation are
explicitly blocked. A release decision must wait for the manual live run described below;
no installation, startup, connection attempt, or fabricated live result was performed by
VERIFY-001.

## Reopening the live gate

An operator configures the ComfyUI URL and folder roots through the configuration surface
FOUNDATION-001 provides, starts ComfyUI, and a follow-up capture run then records
`/system_stats`, the installed node packs, a sanitized `object_info`, one real image run and
one real video run with their events and history, and a cancellation probe. Add those files
beside the synthetic ones, set their `provenance` to `live` in the manifest, fill in the
installation block of the capability record, and flip `live_gate`. Sanitize every capture:
no real prompt text, no real filenames, no host paths, no credentials.
