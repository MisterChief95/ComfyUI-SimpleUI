# Runtime workflow mapping

## Contract and limitations

API JSON contains execution nodes and connections, not a dependable UI layout. Treat the imported graph as authoritative execution data and generate a separate presentation schema. Preserve node IDs, links, literal values, and metadata; never rewrite topology merely to make the form attractive.

ComfyUI exposes node inputs, outputs, ordering, and optional descriptions through object_info. A description can be absent or insufficient to infer meaning. No LLM or network inference service is required. The proposed mapping rules below are application behavior, not guarantees made by ComfyUI. See [metadata implementation](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/master/server.py).

## Catalog lifecycle

Load the last good catalog immediately and attempt one startup refresh. Serve the app even if ComfyUI is offline, with cached data clearly marked stale. With no catalog available, allow storing/importing a graph but do not claim its controls are validated or allow submission.

Coalesce concurrent refresh requests in the backend. Proposed defaults: 500 ms frontend debounce, one upstream refresh at a time, at least 30 seconds between attempts globally, and bounded exponential retry up to five minutes while disconnected. A button displays the next allowed refresh time; server enforcement remains authoritative. Refreshing a page must not bypass the cooldown. Do not fetch object_info on every control change.

Store raw response, normalized definitions, schema/content hash, last success, and last error. Replace a snapshot atomically only after validating its shape. Distinguish changed choices (models added/removed) from structural changes (type/link contract changes). Revalidate open drafts without resetting edits. If an imported value disappears from a dropdown, show the missing value and require correction instead of selecting the first option.

Raw snapshots are backend-only. Produce a sanitized client projection, excluding shared file inventories and internal paths; supported loader adapters supply profile-owned choices. Unknown file-picker shapes stay unavailable rather than falling through to a generic dropdown. A refresh that omits a previously known class marks affected workflows uncertain; it must not erase their saved controls or imported values.

Submission checks against the current known catalog and reports ComfyUI's own validation errors; metadata may change between refresh and execution. The compatibility task must test both older and current metadata shapes, including new node APIs and dynamic inputs. Unknown fields are preserved for diagnostics, not executed as UI code.

## Import and normalization

1. Accept a bounded UTF-8 JSON node map. A clearly recognized API request envelope containing prompt may be unwrapped; regular UI JSON with nodes/links arrays gets an actionable request to export API format.
2. Reject duplicate object keys, invalid class_type/inputs shapes, broken link targets/output indices, unreasonable size/depth, and ambiguous malformed values. Preserve unknown data when harmless; do not evaluate anything.
3. Resolve class_type against the catalog. Missing classes produce a node-specific compatibility error; do not install plugins automatically.
4. Classify each input as a graph link, literal, or unresolved form using schema plus reference validity. A two-element array is not always a link: literal arrays must not be silently rewritten.
5. Generate controls for editable literals. Linked sockets remain intact; show their origin in the mapping inspector. Editable upstream literal nodes may expose controls without breaking their downstream links.
6. Emit unresolved diagnostics and provenance for every control. Unsupported required input shapes block submission; an unusual presentation alone need not block a preserved valid literal.

Identify declared output nodes and trace upstream reachability for presentation. Default submission keeps all original output branches; show their image/video/preview/unknown roles rather than assuming one final image. Collapse disconnected literals into an Inactive group and leave their graph data intact. A graph without a recognized executable output needs a compatibility diagnostic. For lazy or dynamically expanded branches, reachability is only a conservative hint; do not prune nodes or promise every visible control will affect a run. [Node execution properties](https://docs.comfy.org/custom-nodes/backend/server_overview)

Separate diagnostics into blocking execution errors, editable mapping issues, and nonblocking presentation warnings. Missing required values and malformed graph structure block submission. A generic label or unknown widget alone does not: a schema-supported literal can remain unchanged and execute. An opaque structure whose execution contract cannot be established requires an adapter. This distinction prevents the mapping editor from becoming a mandatory setup step for every custom node.

## Primitive control mapping

ComfyUI documents combo lists and scalar types; the UI applies these additional presentation rules. [ComfyUI datatypes](https://docs.comfy.org/custom-nodes/backend/datatypes)

| Metadata/input | Planned component | Behavior |
| --- | --- | --- |
| STRING | Text input | Multiline textarea when indicated; no automatic macro evaluation |
| INT/FLOAT | Number input, optionally paired slider | Respect declared min/max/step; slider only for sensible finite ranges |
| BOOLEAN | Checkbox/switch | Preserve boolean values, not string equivalents |
| Enumerated options | Dropdown or searchable select | Preserve underlying option values and unavailable saved selections |
| Seed-like integer | Exact integer entry plus policy selector | Fixed/random/increment; persist resolved submitted seed |
| Known file-loader literal | Upload/owned-input picker | Requires supported loader binding; raw shared filenames stay hidden |
| MODEL/CLIP/VAE/CONDITIONING/LATENT/IMAGE/VIDEO links | No ordinary form control | Internal data sockets remain connections |
| Unknown/custom object/dynamic shape | Inspector and diagnostic | Preserve safe existing value; typed adapter required for editing unsupported structures |

Imported values take precedence over node defaults. Absent optional inputs remain absent unless explicitly enabled. Backend-hidden inputs are never fabricated as form values. Treat forceInput, defaultInput, slider step, and similar presentation hints separately from execution validation: forceInput controls ComfyUI's widget/socket presentation and alone is not proof that an exported literal is invalid. Preserve supported literals and validate against the installed node contract. Wildcard types and dynamically named inputs may not be fully described by object_info; do not reject them solely through naive exact type/name matching. [Hidden and flexible inputs](https://docs.comfy.org/custom-nodes/backend/more_on_inputs)

Large integers require deliberate JSON handling: Python parses original graph bytes; the browser receives unsafe integers as decimal strings in typed descriptors and sends them back that way. Python converts validated strings for the upstream JSON. Do not JSON.parse the raw imported graph in the browser and lose a 64-bit seed before import. Sliders never handle unsafe integer values.

Reject non-finite numbers and do not clamp or round imported values silently. Seed policy is application state, separate from the graph: default imported seeds to fixed, randomize within supported bounds, and increment only once per newly accepted local request, never on a retry or UI rerender. Report overflow instead of wrapping implicitly. One Generate action submits one graph initially; a workflow's latent batch size remains a node input, not an invented count of separate requests. Frontend-only conveniences such as dynamic prompt expansion or control-after-generate behavior are not recreated from the exported scalar unless explicitly supported; flag relevant metadata and preserve literal text.

## Grouping and inference

Default groups: Prompts, Model, Generation, Dimensions, Inputs, Video, Advanced, Output. Only show populated groups. Group ordering follows generation use, while repeated stages receive clear suffixes such as Base and Refiner when evidence supports them; otherwise use node titles/IDs.

Use deterministic evidence in this order: exact workflow override; compatible personal node template; verified small semantic rules; node/input names and metadata; generic node-based grouping. Keep semantic rules small and typed, not a registry of whole model-family workflows.

For example, CLIPTextEncode exposes one text field and a conditioning output. Whether it is positive or negative depends on where that output is connected. Trace known conditioning paths to a sampler's positive/negative inputs. If it feeds both, or traverses an unknown transform, label it neutrally and offer correction. Do not treat every text input as a prompt. [Core node definitions](https://raw.githubusercontent.com/Comfy-Org/ComfyUI/master/nodes.py)

Do not merge two controls because they share names or values. Several samplers, text encoders, dimensions, or seeds may intentionally differ. Derived convenience actions that update several values require explicit saved bindings and remain deferred until a demonstrated workflow needs them.

## Saved correction model

The mapping editor supports label, group, order, visibility, help text, suitable widget choice, and a numeric display range within validated constraints. It cannot convert a model socket to a dropdown or remove a required execution input.

Each control descriptor includes binding ID, node ID/class, input name, logical type, encoded current value, constraints/options, component, group/order, inference reason, and unresolved status. Python produces this schema and validates changes; Svelte chooses only components from a built-in registry. Node descriptions are rendered as plain text or sanitized supported markup, never HTML/scripts from custom nodes.

Save scope options:

- This workflow: keyed by profile, stored workflow revision/structural signature, node ID, and input name.
- Similar nodes: keyed by profile, node class, input name, and compatible input schema signature. Store presentation preferences only; do not copy prompts, file paths, or seeds into a global template.

Exact workflow overrides win. General node templates should not spread a graph-context label such as Negative prompt to every text encoder unless the saved selector includes that context. Default corrections do not become shared across profiles automatically.

A structural signature includes node IDs/classes, connections, and input shape, while ignoring editable literal values. Persist a separate exact content hash. Changing prompt text can reuse a workflow mapping; changing a node's type invalidates affected bindings. Re-exported graphs with renumbered nodes receive compatible node templates and suggested workflow rebinding, requiring confirmation when ambiguous. No speculative full graph-isomorphism engine.

Catalog refresh checks each override's type/constraints. Show stale corrections and a reset option; never silently mutate a saved layout or drop controls. Mapping saves use a revision token so concurrent tabs cannot overwrite one another unnoticed.

## Image/video compatibility boundary

The same graph machinery handles both media types. FPS, frame count, dimensions, duration inputs, and sampler settings appear when metadata permits. Do not invent duration-to-frames conversion for arbitrary nodes; expose declared inputs. Known output/loader adapters handle filesystem and result-descriptor differences.

Release support includes core scalar/combo graphs, repeated nodes, image saving, one real video-saving path, and owned file-loader inputs. Dynamic custom widgets, arbitrary JS extensions, bespoke upload protocols, unsupported output descriptors, and external paid API nodes receive explicit compatibility diagnostics. Users can continue using supported workflows while resolving others.

## Required verification

- Round-trip untouched graphs without changing links, omitted optionals, or literal types.
- Preserve a seed larger than JavaScript's safe integer range exactly.
- Distinguish literal arrays from links; reject malformed links and missing nodes.
- Render repeated text encoders/samplers without accidental merging or incorrect positive/negative labels.
- Reuse overrides for literal edits; flag stale schemas and renumbered ambiguous bindings.
- Preserve a missing dropdown value and edits during refresh; coalesce requests and enforce cooldown on the server.
- Keep private filenames out of catalog controls; authorize uploaded inputs on submission.
- Run an image graph and a video graph end to end; document adapters and actual unsupported cases.
- Show multiple output branches and inactive controls without pruning; distinguish blocking errors from presentation warnings.
- Preserve valid forceInput literals and supported flexible inputs; reject non-finite values without rounding imported values.
- Resolve random/increment policies once across retries; keep latent batch size separate from request count.
- Sanitize catalog choices after startup and refresh; do not expose the raw global catalog to a profile.

These cases extend COMPAT-001, CATALOG-001, MAPPING-001, UI-002, and VERIFY-001 in TASKS.md and should be included when those task specifications are imported.
