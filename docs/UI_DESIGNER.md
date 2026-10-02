# User-designed generation UI

Status: implemented (LAYOUT-001, UI-007 … UI-010, REVIEW-001, FEAT-001; follow-ups in the
dibs database). This document is the contract those tasks implement. Change it together with the code if a task needs to deviate.

## Goal

The automatic control groups (`prompts`, `model`, …, `advanced`) are a starting point, not the
UI. Real workflows put most controls in `advanced` (for example a Krea2 workflow whose prompt
is a `PrimitiveStringMultiline` and whose sampler is a custom node). The user designs their own
run page: named **sections** containing any controls from any nodes, in any order, with some
controls hidden. One responsive layout per workflow and profile works on phone, tablet, and
desktop. There are no per-device layouts; column counts are hints that collapse on narrow
screens.

Two pages:

- **Run page** (`/generation/[id]`): renders the user's layout, submits, and shows progress
  and results.
- **Designer** (`/workflows/[id]`): edits the layout and the per-control presentation
  (label, widget, numeric display range, help text) with a live preview.

## Layout document (v1)

Stored per **profile + workflow**, presentation only. It never changes the graph, values, or
execution. Per-control presentation stays in the existing corrections API
(`/api/workflows/{id}/corrections`). A layout only arranges and hides controls. The `group`
and `order` fields of a correction are legacy: the new UI neither shows nor writes them.

```json
{
  "version": 1,
  "sections": [
    {
      "id": "prompt",
      "title": "Prompt",
      "columns": 1,
      "collapsed": false,
      "items": [
        { "kind": "control", "binding_id": "53:value", "span": "full" }
      ]
    },
    {
      "id": "sampling",
      "title": "Sampling",
      "columns": 2,
      "collapsed": false,
      "items": [
        { "kind": "control", "binding_id": "5:steps", "span": "auto" },
        { "kind": "control", "binding_id": "5:cfg", "span": "auto" }
      ]
    }
  ],
  "hidden": ["56:debug"]
}
```

Invariants, enforced by the backend on PUT (422 on violation) and by the frontend model:

| Field | Rule |
| --- | --- |
| `version` | Literal `1`. |
| `sections` | 0–40 entries. |
| `section.id` | `^[A-Za-z0-9_-]{1,40}$`, unique within the document. The client generates it. |
| `section.title` | Trimmed, 1–80 characters. |
| `section.columns` | `1`, `2`, or `3`. A desktop hint only: phones always use 1 column and tablets at most 2. |
| `section.collapsed` | Boolean. The **default** disclosure state on the run page. The user's current open/closed state is per-device (localStorage), not saved here. |
| `items` | Typed union: `"control"` or `"aspect_ratio"`. No arbitrary widget code. |
| `item.binding_id` | 1–200 characters. |
| aspect-ratio item | `{ "kind": "aspect_ratio", "width": "4:width", "height": "4:height", "presets": [[1024,1024],[1152,896]], "span": "full" }`. New pairs require two distinct existing integer bindings; previously saved pairs may retain bindings that became stale. Presets are optional, at most 24 positive integer pairs, each dimension at most 16384. |
| `item.span` | `"auto"` (one grid cell) or `"full"` (whole row). Defaults to `"auto"`. |
| total bindings + hidden | At most 1000. An aspect-ratio item counts as two bindings. |
| uniqueness | A binding appears **at most once** across all sections and `hidden` combined. |

Binding IDs that do not exist in the current schema are **kept** and reported as stale, never
dropped silently. The frontend shows them in the designer with a "Remove" action. The run
page ignores absent bindings. If only one half of an aspect-ratio pair survives, it renders
the surviving numeric field without ratio chips.

Controls that exist in the schema but are neither placed nor hidden are **unplaced**. The run
page renders them in a trailing collapsed section titled "More". The designer lists them under
"Unplaced" in its control library. A graph that gains controls therefore never loses them.

Deleting a section moves its controls to unplaced; it never hides them. Hiding is always
explicit and reversible.

Select a placed integer control as width in the designer, choose another placed integer
control as height in the inspector, then **Create aspect-ratio control**. The pair moves,
hides, and unplaces together; **Split dimension controls** restores separate fields. Undo
reverses either operation. This additive item keeps layout version 1.

On the run page, size chips set both dimensions, snapping to each binding's declared
minimum, maximum, and step. **Swap orientation** uses the same constraints. The current
size and megapixel estimate appear beneath the chips; individual numeric fields remain
editable. Defaults use SDXL-sized pairs when the layout supplies no presets. Values stay
exact integer strings, including during swaps.

No saved layout means the **automatic layout**: the frontend derives it from the schema
(one section per non-empty group in backend `GROUP_ORDER`, title-cased; `advanced`,
`output`, and `inactive` start collapsed; items keep `order`). Nothing is persisted until the
user saves.

## API

All routes are owner-scoped like the rest of `/api/workflows`; mutations need CSRF.

`GET /api/workflows/{id}/layout` → `WorkflowLayout`

```json
{
  "workflow_id": "c1f3…",
  "revision": 0,
  "schema_signature": "structure:…",
  "layout": null,
  "stale_bindings": []
}
```

`revision: 0` with `layout: null` means nothing has been saved. `schema_signature` is the
workflow's current structural signature (`mapping/corrections.py`), and `stale_bindings` lists
the saved binding IDs that are absent from the current schema.

`PUT /api/workflows/{id}/layout` with body `{ "layout": LayoutDoc, "expected_revision": n }`
returns `WorkflowLayout`. If the stored revision differs, the response is 409 and the stored
document is untouched; the client re-GETs and offers "reload" or "keep mine and overwrite"
(which re-PUTs with the fresh revision). Saving does not depend on catalog availability: a
layout is pure arrangement.

`DELETE /api/workflows/{id}/layout` returns 204 and reverts to the automatic layout. With
`?expected_revision=n` (0 = nothing saved) a different stored revision is a 409 and nothing is
deleted, so a stale designer cannot discard a layout saved elsewhere.

Storage is migration `backend/migrations/003_workflow_layouts.sql`: table `workflow_layouts`
with `owner_id`, `workflow_id`, `layout_json`, `schema_signature`, `revision`, and
`updated_ms`; primary key `(owner_id, workflow_id)`; foreign keys cascade on profile and
workflow delete.

### Presets

A preset is a named snapshot of control values for one profile's workflow:
`{ id, name, values, revision, created_ms, updated_ms }`, where `values` maps binding id to
`string | boolean | number`. Routes (all owner-scoped; mutations need CSRF):

- `GET /api/workflows/{id}/presets` lists them, most recently updated first.
- `POST` with `{ name, values }` returns 201.
- `PUT /api/workflows/{id}/presets/{preset_id}` with `{ name?, values?, expected_revision }`
  returns the preset, or 409 on a revision mismatch.
- `DELETE` returns 204.

Limits: name trimmed, 1-80 characters; at most 100 presets per workflow (409 beyond that);
at most 500 values; binding keys 1-200 characters; serialized values at most 256 KB; values
are `string | boolean | number` only (no null, array or object; NaN/Infinity refused).
Integers are never lossy: a JSON integer or an ExactInt string is accepted, and integers of
2^53 or more are stored and returned as decimal strings, so the client must treat a string
value for an `int` control as exact. Binding ids are **not** checked against the current
schema: a preset may outlive graph changes, and the frontend filters on apply. `*_ms` are
ExactInt strings. Storage is migration `004_workflow_presets.sql` (`workflow_presets`, STRICT,
cascading on profile and workflow delete).

### Replacing a graph

`PUT /api/workflows/{id}/graph` takes the raw ComfyUI API JSON (read as bytes like import, same
rules) and stores it as revision N+1, sets `current_revision` and `updated_ms`, atomically. It
returns `{ workflow, revision, added, removed }` with the control binding ids that appeared or
vanished. An invalid graph is a 422 and nothing changes. Every read (controls, layout,
corrections, new generations) uses the current revision; a generation keeps the revision it
ran. Layout, corrections and presets persist untouched: `GET .../layout` reports bindings that
no longer exist as `stale_bindings`, and corrections follow the existing stale-signature rules.
`PATCH /api/workflows/{id}` with `{ name }` renames (1-120 characters).

### Related backend fixes in the same task

- **Typed edits.** `POST /api/generations` `edits` values are
  `string | boolean | number`, encoded like `ControlDescriptor.value`: ExactInt string for
  `int`, number (or numeric string) for `float`, real JSON boolean for `boolean`, string for
  the rest. Before this change, checkbox edits sent `"true"` and were rejected by
  `_decode()`.
- **`GET /api/media?generation_id=…`** filters media to one generation. Previously the run
  page fetched 20 workflow rows and filtered on the client, so a result outside that window
  disappeared.

## Frontend architecture

```
src/lib/
  contracts.ts            + LayoutDoc, LayoutSection, LayoutItem, WorkflowLayout, EditValue
  layout/model.ts         pure TS, no Svelte or DOM: types, defaultLayout(schema),
                          resolveLayout(schema, layout) → {sections, unplaced, stale},
                          operations (moveItem, addSection, removeSection, hide, unhide,
                          renameSection, …) returning new docs, validate(doc), newSectionId()
  layout/model.test.ts    node --test (Node ≥ 22 strips types), run by `npm test`
  layout/history.svelte.ts  undo/redo stack of LayoutDoc snapshots
  controls/ControlWidget.svelte  value-only renderer for one control (run page and designer
                          preview); emits typed EditValue; replaces ControlField's
                          generate mode
  ui/                     shell, nav, icons, Sheet (bottom sheet / drawer), shared primitives
```

Global styles in `ui/tokens.css` define the visual system: tokens plus base element styles
and a few utility classes (`.btn`, `.btn-primary`, `.btn-ghost`, `.btn-danger`, `.card`,
`.chip`, `.input`, …), so pages stop re-declaring button and input CSS.

### Breakpoints

| Name | Width | Shell | Run page | Designer |
| --- | --- | --- | --- | --- |
| phone | < 768px | bottom tab bar | one column; sticky bottom Generate bar; result card above the controls | canvas only; library and inspector as bottom sheets |
| tablet | 768–1199px | left icon rail | controls + result side by side (result 40%), section columns ≤ 2 | canvas + inspector; library as a drawer |
| desktop | ≥ 1200px | left rail with labels | controls + sticky result panel | library · canvas · inspector |

The minimum touch target is `--touch-target` (44px). Respect `env(safe-area-inset-*)` and avoid
horizontal page scroll. Honour `prefers-reduced-motion`.

### Run page

- Header: workflow switcher (select among workflows), a "Design" link, and a status chip.
- Section jump chips (sticky) when there are more than 3 sections.
- Sections render with their columns. Collapse state persists per workflow in localStorage.
- **Drafts:** edits persist in localStorage keyed by profile and workflow (debounced) and
  survive reloads. Each modified control shows a dot with a reset action. "Reset all" restores
  imported values. "Show modified only" filters the controls.
- **Presets:** save only modified draft values; apply, rename, overwrite, or delete
  a workflow's presets from the Presets sheet.
- **Generate bar:** seed policy (compact), Generate (Ctrl/⌘+Enter, even inside a textarea),
  and Cancel while queued or running. On phone it is sticky at the bottom above the tab bar.
- **Progress:** the WebSocket `/api/events` reconnects with backoff. Show a `progress` event as
  a bar (`value/max`), an `executing` event as the current node label, and the status chip.
  While the latest generation is non-terminal, also poll `GET /api/generations/{id}` every 3s
  as a fallback.
- **Result panel:** the latest result (image or video) uses `GET /api/media?generation_id=`;
  below it sits a strip of recent outputs for this workflow (`/api/media?workflow_id=&limit=12`).
  Clicking one shows it, and "Reuse settings" loads its generation's `effective_values` into
  the draft.
- Unplaced controls appear in a collapsed "More" section. Hidden controls are never rendered,
  but their imported values are still submitted unchanged.

### Designer

- Toolbar: Save (dirty indicator; Ctrl/⌘+S), Undo/Redo (Ctrl/⌘+Z, Shift+Ctrl/⌘+Z), a preview
  width toggle (Phone 390 · Tablet 820 · Full) that constrains the canvas, "Reset to automatic",
  and "Open run page".
- **Library:** every control grouped by node (node title or class, plus node id). Search covers
  label, class, input name, and binding id. Filter chips are All / Unplaced / Placed / Hidden.
  Each row shows its label, a type badge, and where it is placed. Tap "Add" to put it in the
  target section (the last selected section, else the first, else a new one).
- **Canvas:** sections as cards in preview width, with live widget previews (non-submitting).
  The section header has inline rename, a columns toggle, a collapsed-by-default toggle, move
  up/down, and delete. Items are selectable, and the selected item shows Up, Down, a "Move to…"
  select, Hide, and a span toggle.
- **Drag and drop:** one Pointer Events implementation for mouse and touch, started only from a
  visible drag handle (`touch-action: none` on the handle and `pan-y` elsewhere), with a
  movement threshold, `setPointerCapture`, a drop indicator, and capped edge auto-scroll. It
  supports reordering within a section, moving between sections, reordering sections by their
  header handle, and dragging from the library onto the canvas. Tap buttons remain the primary
  touch path, and an `aria-live` region announces moves. No drag library.
- **Inspector** (selected control): label, widget (`ALLOWED_COMPONENTS[logical_type]`), numeric
  display min/max/step/default for ranged numbers, help text, span, section, hide/unhide,
  provenance (class, node id, input, inference reason, imported value), and "Reset
  presentation" (DELETE that correction). For a selected section: title, columns, and
  collapsed.
- **Save:** PUT the layout, then the dirty corrections **sequentially**, collecting failures
  into one summary. A conflict offers reload, or overwrite (retry with the fresh revision). An
  unsaved-changes guard uses `beforeNavigate` and `beforeunload`.
- Stale bindings appear in a banner, each with a Remove button.
- **Copy layout from…:** copy another workflow's saved sections and hidden controls,
  keeping bindings available in this workflow. The copy is unsaved and supports Undo;
  feedback reports a missing layout or dropped bindings.

## Deferred (later waves, not part of the tasks above)

These are the `aspect_ratio` composite item, prompt
history autocomplete and a queue panel.
