"""Saved mapping corrections, replayed over the deterministic control schema.

``controls.py`` derives a ``ControlSchema`` from the graph and the catalog by
fixed rules. Nothing here changes those rules: a correction is *presentation*
stored per profile and replayed on top, so resetting it returns the exact base
schema (docs/WORKFLOW_MAPPING.md "Saved correction model").

Two scopes, with exact workflow overrides winning:

* ``workflow`` -- keyed by profile, workflow, node id and input name, and
  guarded by the graph's **structural signature**.
* ``node_class`` -- keyed by profile, node class, input name and the control's
  *graph context*, and guarded by that input's schema signature. The context is
  part of the key on purpose: a "Negative prompt" label saved on one encoder
  must not spread to every text encoder in every workflow.

A correction is never silently mutated or silently dropped. When its signature
no longer matches, the base presentation is used and the schema carries a
warning so the editor can offer repair or reset.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from ..contracts import (
    Component,
    ControlDescriptor,
    ControlSchema,
    ErrorDetail,
    Group,
    Id,
    Model,
)
from .controls import GROUP_ORDER
from .importer import classify_input

CorrectionScope = Literal["workflow", "node_class"]

#: Components a correction may choose, per logical type. A correction adjusts
#: presentation only: it cannot turn a model socket into a dropdown, invent
#: options, or change a value.
ALLOWED_COMPONENTS: dict[str, frozenset[str]] = {
    "string": frozenset({"text", "textarea", "readonly", "lora_stack"}),
    "int": frozenset({"number", "slider", "seed", "readonly"}),
    "float": frozenset({"number", "slider", "readonly"}),
    "boolean": frozenset({"checkbox", "readonly"}),
    "enum": frozenset({"select", "readonly"}),
    "file": frozenset({"file", "readonly"}),
    "unknown": frozenset({"readonly"}),
}


class CorrectionError(Exception):
    """A rejected correction. ``status`` maps straight onto the HTTP response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Presentation(Model):
    """Everything a saved correction may change -- and nothing else.

    ``Model`` forbids unknown fields, so a payload carrying a value, a prompt, a
    filename or a seed is rejected at parse time rather than filtered later.
    That is what keeps a general node template presentation-only.
    """

    label: str | None = Field(default=None, min_length=1, max_length=200)
    group: Group | None = None
    order: int | None = Field(default=None, ge=0)
    help_text: str | None = Field(default=None, max_length=2000)
    component: Component | None = None
    #: Narrows the displayed numeric range; it can never widen past the
    #: validated constraints, and never applies to exact integers.
    display_min: float | None = None
    display_max: float | None = None
    #: Overrides the control's shown value; validated against the resolved
    #: (possibly narrowed) range above. Never applies to exact integers.
    display_default: float | None = None
    #: Overrides the slider/number step; never applies to exact integers.
    display_step: float | None = Field(default=None, gt=0)
    #: LoRA stack only: show entry thumbnails / the CLIP strength.
    show_thumbnails: bool | None = None
    show_clip: bool | None = None
    #: LoRA stack only: LoRAs per row on desktop (1-3).
    lora_columns: int | None = Field(default=None, ge=1, le=3)


class Correction(Model):
    """One stored correction, as exported to the mapping editor."""

    scope: CorrectionScope
    selector: str
    schema_signature: str
    presentation: Presentation
    revision: int
    #: True when the saved signature no longer matches this workflow, or when
    #: the selector binds to no control. The correction is kept, not applied.
    stale: bool = False


class SaveCorrection(Model):
    """The save request. Selector and signature are derived server-side."""

    scope: CorrectionScope = "workflow"
    binding_id: Id
    presentation: Presentation
    #: 0 means "no correction saved yet". A mismatch is a 409, so a second tab
    #: cannot overwrite the first unnoticed.
    expected_revision: int = Field(default=0, ge=0)


# --- signatures -----------------------------------------------------------


def structural_signature(graph: dict[str, Any]) -> str:
    """Node ids/classes, connections and input shape; literal values ignored.

    Editing prompt text or a seed keeps the signature, so the saved layout is
    reused. Changing a node's class, a connection, or an input's shape changes
    it, and the affected corrections are reported stale instead of applied.
    """
    parts = []
    for node_id in sorted(graph):
        node = graph[node_id]
        inputs = node.get("inputs", {})
        shapes = []
        for name in sorted(inputs):
            link = classify_input(inputs[name], graph, declared_socket=False)
            shape = (
                f"link:{link.node_id}.{link.output_index}"
                if link
                else _shape(inputs[name])
            )
            shapes.append(f"{name}={shape}")
        parts.append(f"{node_id}:{node.get('class_type')}({','.join(shapes)})")
    return _digest("structure", parts)


def content_hash(graph: dict[str, Any]) -> str:
    """Exact content, literal values included -- kept separate from the signature."""
    return hashlib.sha256(
        json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def input_signature(control: ControlDescriptor, catalog_nodes: dict[str, Any]) -> str:
    """The compatible input-schema signature a node template is keyed to.

    Deliberately excludes the option list: models being added or removed is a
    *changed choices* event, not a structural one, and must not invalidate a
    saved label (docs/WORKFLOW_MAPPING.md "Catalog lifecycle").
    """
    node_spec = catalog_nodes.get(control.class_type)
    spec = (node_spec or {}).get("inputs", {}).get(control.input_name, {})
    return _digest(
        "input",
        [
            control.class_type,
            control.input_name,
            str(spec.get("logical_type")),
            str(bool(spec.get("multiline"))),
            str(spec.get("choices_source")),
            str(spec.get("socket_type")),
        ],
    )


def workflow_selector(control: ControlDescriptor) -> str:
    """``node_id:input_name`` -- the exact binding in this graph."""
    return control.binding_id


def node_selector(control: ControlDescriptor) -> str:
    """``class:input:context``. The context keeps a label from over-spreading."""
    return f"{control.class_type}:{control.input_name}:{context_of(control)}"


def context_of(control: ControlDescriptor) -> str:
    """The graph-context half of ``inference_reason`` (e.g. traced conditioning)."""
    return control.inference_reason.rpartition(";")[2] or control.inference_reason


def _shape(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"  # one tag: 8 and 8.0 are the same shape, not a repair
    if isinstance(value, str):
        return "str"
    if value is None:
        return "null"
    return "list" if isinstance(value, list) else "dict"


def _digest(kind: str, parts: list[str]) -> str:
    joined = "\x1f".join(parts)
    return f"{kind}:{hashlib.sha256(joined.encode()).hexdigest()[:32]}"


# --- replay ---------------------------------------------------------------


def expected_signature(
    scope: str,
    control: ControlDescriptor,
    signature: str,
    catalog_nodes: dict[str, Any],
) -> str:
    return signature if scope == "workflow" else input_signature(control, catalog_nodes)


def apply_corrections(
    schema: ControlSchema,
    overrides: list[dict[str, Any]],
    catalog_nodes: dict[str, Any],
    *,
    signature: str,
) -> ControlSchema:
    """Replay the profile's saved corrections over a freshly derived schema."""
    if not overrides:
        return schema

    stored = {(row["scope"], row["binding_selector"]): row for row in overrides}
    warnings = list(schema.warnings)
    controls: list[ControlDescriptor] = []
    matched: set[tuple[str, str]] = set()
    changed = False

    for control in schema.controls:
        applied = False
        for scope, selector in (
            ("workflow", workflow_selector(control)),
            ("node_class", node_selector(control)),
        ):
            row = stored.get((scope, selector))
            if row is None:
                continue
            matched.add((scope, selector))
            if row["schema_signature"] != expected_signature(
                scope, control, signature, catalog_nodes
            ):
                warnings.append(_stale(control.binding_id, scope, "signature"))
                continue  # exact overrides win, but a stale one is not applied
            control = _apply(control, _presentation(row), scope, warnings)
            applied = True
            break
        changed = changed or applied
        controls.append(control)

    for (scope, selector), row in stored.items():
        # A workflow correction that binds to nothing: renumbered or removed
        # nodes need confirmation, not a silent drop of the saved layout.
        if scope == "workflow" and (scope, selector) not in matched:
            warnings.append(_stale(selector, scope, "unbound"))

    if changed:
        controls = _resequence(controls)
    return schema.model_copy(update={"controls": controls, "warnings": warnings})


def _presentation(row: dict[str, Any]) -> Presentation:
    return Presentation.model_validate(json.loads(row["presentation_json"]))


def _apply(
    control: ControlDescriptor,
    presentation: Presentation,
    scope: str,
    warnings: list[ErrorDetail],
) -> ControlDescriptor:
    update: dict[str, Any] = {}
    if presentation.label is not None:
        update["label"] = presentation.label
    if presentation.group is not None:
        update["group"] = presentation.group
    if presentation.order is not None:
        update["order"] = presentation.order
    if presentation.help_text is not None:
        update["help_text"] = presentation.help_text
    if presentation.show_thumbnails is not None:
        update["show_thumbnails"] = presentation.show_thumbnails
    if presentation.show_clip is not None:
        update["show_clip"] = presentation.show_clip
    if presentation.lora_columns is not None:
        update["lora_columns"] = presentation.lora_columns

    if presentation.component is not None:
        if presentation.component in ALLOWED_COMPONENTS.get(
            control.logical_type, frozenset()
        ):
            update["component"] = presentation.component
        else:
            # The catalog changed under a saved widget choice. Keep the derived
            # component rather than presenting an editor the type cannot support.
            warnings.append(
                ErrorDetail(
                    field=control.binding_id,
                    code="override_component_unavailable",
                    message=(
                        f"The saved {presentation.component} widget no longer suits "
                        f"{control.class_type}.{control.input_name} ({control.logical_type}). "
                        "The derived control is shown; save a new widget choice to replace it."
                    ),
                )
            )

    constraints = _display_range(control, presentation)
    if constraints is not None:
        update["constraints"] = constraints
    if presentation.display_default is not None:
        update["value"] = (
            str(int(presentation.display_default))
            if control.logical_type == "int"
            else float(presentation.display_default)
        )

    update["inference_reason"] = f"{control.inference_reason};override:{scope}"
    return control.model_copy(update=update)


def effective_bounds(
    control: ControlDescriptor, presentation: Presentation
) -> tuple[float | None, float | None] | None:
    """Declared min/max narrowed by a presentation's display range.

    Returns ``None`` when the control has no adjustable numeric range at all
    (missing constraints, or an exact-transport integer).
    """
    limits = control.constraints
    if limits is None or limits.exact_min is not None or limits.exact_max is not None:
        return None
    low, high = limits.min, limits.max
    if presentation.display_min is not None and (
        low is None or presentation.display_min > low
    ):
        low = presentation.display_min
    if presentation.display_max is not None and (
        high is None or presentation.display_max < high
    ):
        high = presentation.display_max
    return low, high


def _display_range(control: ControlDescriptor, presentation: Presentation) -> Any:
    """Narrow the shown range/step inside the validated constraints, never past them."""
    if (
        presentation.display_min is None
        and presentation.display_max is None
        and presentation.display_step is None
    ):
        return None
    bounds = effective_bounds(control, presentation)
    if bounds is None:
        return None
    low, high = bounds
    if low is not None and high is not None and low >= high:
        return None
    limits = control.constraints
    step = (
        presentation.display_step
        if presentation.display_step is not None
        else limits.step
    )
    return limits.model_copy(update={"min": low, "max": high, "step": step})


def _resequence(controls: list[ControlDescriptor]) -> list[ControlDescriptor]:
    """Re-sort after a correction moved a control's group or order."""
    rank = {group: index for index, group in enumerate(GROUP_ORDER)}
    ordered = sorted(
        enumerate(controls),
        key=lambda pair: (rank.get(pair[1].group, len(rank)), pair[1].order, pair[0]),
    )
    return [
        control.model_copy(update={"order": index})
        for index, (_, control) in enumerate(ordered)
    ]


def _stale(selector: str, scope: str, why: str) -> ErrorDetail:
    reason = (
        "this workflow's structure changed incompatibly"
        if why == "signature"
        else "no control in this workflow matches it any more"
    )
    return ErrorDetail(
        field=selector,
        code="mapping_correction_stale",
        message=(
            f"A saved {scope.replace('_', ' ')} correction for {selector} is not applied because "
            f"{reason}. The derived controls are shown unchanged; repair the correction by saving "
            "it again, or reset it."
        ),
    )
