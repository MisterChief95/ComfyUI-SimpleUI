# User-designed generation UI

Status: implemented (LAYOUT-001, LAYOUT-002 contract/model, UI-007 … UI-010, REVIEW-001, FEAT-001; follow-ups in the
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

## Layout document (v2)

Stored per **profile + workflow**, presentation only. It never changes the graph, values, or
execution. Per-control presentation stays in the existing corrections API
(`/api/workflows/{id}/corrections`). A layout only arranges and hides controls. The `group`
and `order` fields of a correction are legacy: the new UI neither shows nor writes them.

```json
{
  "version": 2,
  "sections": [
    {
      "id": "prompt",
      "title": "Prompt",
      "mode": "auto",
      "columns": 1,
      "collapsed": false,
      "items": [
        { "kind": "control", "binding_id": "53:value", "span": "full" }
      ]
    },
    {
      "id": "sampling",
      "title": "Sampling",
      "mode": "auto",
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

Each section has one of two bodies. `mode: "auto"` (the default when omitted)
uses `columns: 1 | 2 | 3` and `items`, as above. `mode: "panels"` uses `rows`
instead of section-level `columns` and `items`:

```json
{
  "id": "settings", "title": "Settings", "mode": "panels", "collapsed": false,
  "rows": [
    { "id": "row1", "columns": [
      { "id": "column1", "items": [{ "kind": "control", "binding_id": "5:steps" }] },
      { "id": "column2", "items": [{ "kind": "control", "binding_id": "5:cfg" }] }
    ] }
  ]
}
```

This is exactly section → row → column → existing leaf items. There is no recursive
row item, deeper nesting, column resizing, or per-panel disclosure. Rows have 1–3
equal-width columns, collapsing responsively like auto sections. Empty panel sections
and columns are allowed, so a row whose columns hold no items is valid (the designer
creates these when adding a row); a row with zero columns is not. Item `span: "full"` fills its column's
leaf grid in a panel section, not neighboring columns.

The backend accepts v1 and v2. V1 sections have only the original body and are
normalized in memory to v2 auto sections with order, bindings, and presentation
preserved. GET does not rewrite storage. An explicit PUT (including one carrying v1)
persists v2 JSON in the existing `layout_json TEXT` field; no database migration is needed.

Invariants, enforced by the backend on PUT (422 on violation) and by the frontend model:

| Field | Rule |
| --- | --- |
| `version` | `2` on responses and explicit saves; legacy `1` accepted and normalized. |
| `sections` | 0–40 entries. |
| `section.id` | `^[A-Za-z0-9_-]{1,40}$`, unique within the document. The client generates it. |
| `section.title` | Trimmed, 1–80 characters. |
| `section.mode` | `"auto"` (default) or `"panels"`. Bodies are mutually exclusive; unknown fields are rejected. |
| auto `section.columns` | `1`, `2`, or `3`. A desktop hint only: phones always use 1 column and tablets at most 2. |
| panel `section.rows` | 0–40 rows per section, including empty containers. |
| `row.columns` | 1–3 equal-width columns per row. Each has `items` only, never rows. |
| `row.id`, `column.id` | Same pattern as section IDs; unique across all rows and columns in the entire document (shared structural namespace, separate from section IDs). |
| `section.collapsed` | Boolean. The **default** disclosure state on the run page. The user's current open/closed state is per-device (localStorage), not saved here. |
| `items` | Typed union: `"control"` or `"aspect_ratio"`. No arbitrary widget code. |
| `item.binding_id` | 1–200 characters. |
| aspect-ratio item | `{ "kind": "aspect_ratio", "width": "4:width", "height": "4:height", "presets": [[1024,1024],[1152,896]], "span": "full" }`. New pairs require two distinct existing integer bindings; previously saved pairs may retain bindings that became stale. Presets are optional, at most 24 positive integer pairs, each dimension at most 16384. |
| `item.span` | `"auto"` (one grid cell) or `"full"` (whole row). Defaults to `"auto"`. |
| `item.when` | Optional binding id (1–200 characters) of a **boolean** control. The item renders on the run page only while that control is on. A reference, not a placement: any number of items may share one condition, placed or not. Omitted when unset. |
| `section.toggle` | Optional binding id of a **boolean** control rendered as a switch in the section header; the section body (and its disclosure) is available only while it is on. It *places* that control: it counts toward the cap and uniqueness, and placing or hiding the control elsewhere clears the toggle. Omitted when unset. |
| total bindings + hidden | At most 1000. An aspect-ratio item counts as two bindings; a section toggle counts as one. |
| uniqueness | A binding appears **at most once** across all sections (all auto and panel leaves and toggles) and `hidden` combined. |

Binding IDs that do not exist in the current schema are **kept** and reported as stale, never
dropped silently. The frontend shows them in the designer with a "Remove" action. The run
page ignores absent bindings. If only one half of an aspect-ratio pair survives, it renders
the surviving numeric field without ratio chips.

Conditions and toggles that name a missing or non-boolean control are ignored (the item or
section always shows) and flagged in the designer. Controls hidden by a condition or an off
toggle still submit their current value, exactly like hidden controls. `when` and `toggle`
are optional fields shared by both section modes.

Controls that exist in the schema but are neither placed nor hidden are **unplaced**. The run
page renders them in a trailing collapsed section titled "More". The designer lists them under
"Unplaced" in its control library. A graph that gains controls therefore never loses them.

Deleting a section moves its controls to unplaced; it never hides them. Hiding is always
explicit and reversible.

Select a placed integer control as width in the designer, choose another placed integer
control as height in the inspector, then **Create aspect-ratio control**. The pair moves,
hides, and unplaces together; **Split dimension controls** restores separate fields. Undo
reverses either operation. The same leaf item works in both section modes.

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

### Saving default values

`PUT /api/workflows/{id}/values` with `{ edits, expected_revision }` applies `edits` (same
encoding and validation as `POST /api/generations` edits, against the corrections-applied
schema) to the current graph and stores it as revision N+1. It returns the replace-graph
shape with empty `added`/`removed`. A different current revision is a 409; an unknown,
read-only or invalid edit, or empty `edits`, is a 422; nothing is written in either case.
Unresolved controls elsewhere do not block it. Layout, corrections and presets are untouched
because the structural signature ignores literal values.

In the designer, changing any control's value on the canvas drafts a new default (badge
"default changed", **Revert value** in the inspector). Save sends layout, then corrections,
then values. Editing a value clears a legacy numeric `display_default` for that control,
which the inspector no longer offers. Value drafts are not part of layout undo.

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

`layout/model.ts` stays pure, erasable TypeScript. For UI-027:

- `sectionItems(section: LayoutSection): LayoutItem[]` reads all leaves in row/column/item order.
- `mapSectionItems(section, fn: (items: LayoutItem[]) => LayoutItem[]): LayoutSection`
  transforms each leaf list immutably while keeping structural IDs and order.
- `normalizeLayout(doc: LayoutDoc): LayoutDoc` upgrades a valid legacy snapshot to v2 auto.
  `defaultLayout(schema)` and newly added sections produce v2 auto layouts.
- `locate(doc, binding)` returns `{section, index}` for auto leaves, or
  `{section, row, column, index}` for panel leaves; the index is local to the column.
  A toggle has `index: -1`; hidden is `"hidden"`; unplaced is `null`.
- `placeControl(doc, binding, sectionId, index?, target?: PanelTarget)` and
  `moveItem(doc, binding, sectionId, index?, target?: PanelTarget)` accept
  `PanelTarget = {row: string, column: string}`. Without a target, panels use the
  first row's first column. An absent or invalid destination is a no-op before
  removal: the UI must create a row/column before placing into an empty panel section.
- `resolveLayout` flattens resolved leaves in section order for existing callers.
  Use `locate` to recover panel placement for rendering. All count, removal, hiding,
  toggle, condition, span, pairing, and splitting helpers traverse both modes.
  Pairing keeps the width's column and position; moving/hiding/removing a pair handles both bindings.
- `MAX_ROWS = 40`, `MAX_COLUMNS = 3`, `MAX_SECTIONS = 40`, `MAX_ITEMS = 1000`.
  Narrow `section.mode === "panels"` before accessing rows; otherwise use columns/items.
  UI-027 owns structure editing and full panel rendering.

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
