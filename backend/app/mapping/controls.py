"""Turn an imported graph plus a sanitized catalog into a ``ControlSchema``.

The graph is read, never rewritten. Topology, literal types, omitted optionals
and unknown data survive untouched; everything produced here is *presentation*
laid over the stored graph (docs/WORKFLOW_MAPPING.md).

Three diagnostic channels, deliberately distinct, so that the mapping editor
does not become a mandatory setup step for every custom node:

* ``ControlSchema.blocking`` -- execution cannot proceed: a class missing from
  the catalog, a broken link, an absent required input, no executable output,
  or no catalog at all.
* ``ControlDescriptor.unresolved`` -- an editable mapping issue: the user has
  to pick something (an imported combo value the catalog no longer offers).
  The imported value is shown, never replaced with the first option.
* ``ControlSchema.warnings`` -- presentation only, still submittable: an opaque
  custom type awaiting a typed adapter, a literal whose shape does not match
  the declared scalar, a flexible input object_info does not enumerate, or a
  frontend-only macro hint that is preserved literally rather than evaluated.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..contracts import (
    Component,
    ControlDescriptor,
    ControlSchema,
    EnumOption,
    ErrorDetail,
    Group,
    LogicalType,
    NumberConstraints,
)
from .importer import classify_input, looks_like_link

#: Group order follows generation use (docs/WORKFLOW_MAPPING.md "Grouping").
#: Only populated groups are emitted.
GROUP_ORDER: tuple[Group, ...] = (
    "prompts",
    "model",
    "generation",
    "dimensions",
    "inputs",
    "video",
    "advanced",
    "output",
    "inactive",
)

#: Small, typed name rules -- not a registry of model-family workflows.
_GENERATION_INPUTS = frozenset(
    {"seed", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "batch_size"}
)
_DIMENSION_INPUTS = frozenset({"width", "height", "length", "tile_width", "tile_height"})
_VIDEO_INPUTS = frozenset({"fps", "frame_rate", "frame_count", "num_frames", "format", "codec", "crf"})
#: Inputs whose *value* names a conditioning role on the consuming node.
_POSITIVE = "positive"
_NEGATIVE = "negative"

#: Macro syntax this app does not expand: ComfyUI's own ``%node.widget%`` /
#: ``%date:...%`` substitutions and frontend dynamic-prompt ``{a|b}`` wildcards.
#: Matched against the literal itself -- a node merely *declaring*
#: ``dynamicPrompts`` is recorded in ``raw_metadata`` and does not warn, or
#: every ordinary prompt in every graph would carry a warning.
_PROMPT_MACRO = re.compile(r"%[^%\s][^%]*%|\{[^{}]*\|[^{}]*\}")


def build_control_schema(
    graph: dict[str, Any],
    catalog_nodes: dict[str, Any],
    *,
    owner_id: str,
    workflow_id: str,
    revision: int,
    catalog_available: bool = True,
) -> ControlSchema:
    """Build the presentation schema for one stored workflow revision."""
    blocking: list[ErrorDetail] = []
    warnings: list[ErrorDetail] = []

    if not catalog_available:
        blocking.append(
            ErrorDetail(
                field=None,
                code="catalog_unavailable",
                message=(
                    "No node catalog is available, so these controls are not validated "
                    "against the installed nodes and the workflow cannot be submitted."
                ),
            )
        )

    links = _resolve_links(graph, catalog_nodes, blocking)
    consumers = _consumers(links)
    output_nodes = _output_nodes(graph, catalog_nodes)
    reachable = _reachable(graph, links, output_nodes)

    if catalog_available and not output_nodes:
        blocking.append(
            ErrorDetail(
                field=None,
                code="no_output_node",
                message=(
                    "No node in this workflow is an output node, so there is nothing for "
                    "ComfyUI to execute. Add a save or preview node and export again."
                ),
            )
        )

    controls: list[ControlDescriptor] = []
    for node_id in _ordered_ids(graph):
        node = graph[node_id]
        class_type = node["class_type"]
        spec = catalog_nodes.get(class_type)
        if spec is None:
            if catalog_available:
                blocking.append(
                    ErrorDetail(
                        field=node_id,
                        code="class_missing",
                        message=(
                            f"Node {node_id} uses {class_type}, which is not installed. "
                            "Install the node pack that provides it; nothing is installed "
                            "automatically and the node stays in the workflow."
                        ),
                    )
                )
            controls.extend(_unknown_class_controls(node_id, node, graph, reachable))
            continue

        controls.extend(
            _node_controls(
                node_id,
                node,
                spec,
                graph,
                links,
                consumers,
                reachable,
                warnings,
            )
        )
        _check_required(node_id, node, spec, blocking)

    controls.extend(_branch_controls(graph, catalog_nodes, output_nodes))

    if any(control.group == "inactive" for control in controls):
        warnings.append(
            ErrorDetail(
                field=None,
                code="inactive_controls",
                message=(
                    "Some nodes are not reachable from any output node. Their controls are "
                    "grouped as Inactive and their graph data is left untouched; reachability "
                    "is a hint, so a lazily expanded branch may still run."
                ),
            )
        )

    controls = _order(controls)
    return ControlSchema(
        owner_id=owner_id,
        revision=revision,
        workflow_id=workflow_id,
        controls=controls,
        blocking=blocking,
        warnings=warnings,
    )


# --- graph structure ------------------------------------------------------


def _ordered_ids(graph: dict[str, Any]) -> list[str]:
    """Numeric node ids sort numerically; anything else sorts after, by text."""
    return sorted(graph, key=lambda n: (0, int(n), "") if n.lstrip("-").isdigit() else (1, 0, n))


def _resolve_links(
    graph: dict[str, Any], catalog_nodes: dict[str, Any], blocking: list[ErrorDetail]
) -> dict[tuple[str, str], Any]:
    """``{(node_id, input_name): Link}``. Broken targets/indices become blocking."""
    resolved: dict[tuple[str, str], Any] = {}
    for node_id, node in graph.items():
        spec = catalog_nodes.get(node["class_type"], {})
        declared = spec.get("inputs", {}) if isinstance(spec, dict) else {}
        for name, value in node.get("inputs", {}).items():
            input_spec = declared.get(name) or {}
            declared_socket = input_spec.get("logical_type") in ("LINK", "WILDCARD")
            link = classify_input(value, graph, declared_socket=declared_socket)
            if link is None:
                continue
            resolved[(node_id, name)] = link

            target = graph.get(link.node_id)
            if target is None:
                blocking.append(
                    ErrorDetail(
                        field=f"{node_id}.inputs.{name}",
                        code="broken_link",
                        message=(
                            f"Node {node_id} input {name!r} is connected to node "
                            f"{link.node_id}, which is not in this workflow. The link is "
                            "not repaired and no node is removed; export the workflow again."
                        ),
                    )
                )
                continue
            target_spec = catalog_nodes.get(target["class_type"])
            outputs = target_spec.get("outputs") if isinstance(target_spec, dict) else None
            if outputs is not None and link.output_index >= len(outputs):
                blocking.append(
                    ErrorDetail(
                        field=f"{node_id}.inputs.{name}",
                        code="broken_link",
                        message=(
                            f"Node {node_id} input {name!r} requests output "
                            f"{link.output_index} of node {link.node_id} "
                            f"({target['class_type']}), which has {len(outputs)}."
                        ),
                    )
                )
    return resolved


def _consumers(links: dict[tuple[str, str], Any]) -> dict[str, set[str]]:
    """``{source_node_id: {input names it feeds}}`` -- the conditioning evidence."""
    consumers: dict[str, set[str]] = {}
    for (_, input_name), link in links.items():
        consumers.setdefault(link.node_id, set()).add(input_name)
    return consumers


def _output_nodes(graph: dict[str, Any], catalog_nodes: dict[str, Any]) -> list[str]:
    return [
        node_id
        for node_id in _ordered_ids(graph)
        if bool(catalog_nodes.get(graph[node_id]["class_type"], {}).get("output_node"))
    ]


def _reachable(
    graph: dict[str, Any], links: dict[tuple[str, str], Any], output_nodes: list[str]
) -> set[str]:
    """Nodes upstream of any output node. A conservative presentation hint only."""
    seen: set[str] = set()
    stack = list(output_nodes)
    upstream: dict[str, list[str]] = {}
    for (node_id, _), link in links.items():
        upstream.setdefault(node_id, []).append(link.node_id)
    while stack:
        node_id = stack.pop()
        if node_id in seen or node_id not in graph:
            continue
        seen.add(node_id)
        stack.extend(upstream.get(node_id, ()))
    return seen


# --- per-node controls ----------------------------------------------------


def _node_controls(
    node_id: str,
    node: dict[str, Any],
    spec: dict[str, Any],
    graph: dict[str, Any],
    links: dict[tuple[str, str], Any],
    consumers: dict[str, set[str]],
    reachable: set[str],
    warnings: list[ErrorDetail],
) -> list[ControlDescriptor]:
    class_type = node["class_type"]
    declared = spec.get("inputs", {})
    title = _title(node)
    controls: list[ControlDescriptor] = []

    for name, value in node.get("inputs", {}).items():
        if (node_id, name) in links:
            continue  # a connection; it stays a connection, never a form control
        input_spec = declared.get(name)
        if input_spec is None:
            controls.append(
                _flexible_control(node_id, class_type, name, value, spec, title, reachable, warnings)
            )
            continue
        controls.append(
            _literal_control(
                node_id,
                class_type,
                name,
                value,
                input_spec,
                spec,
                title,
                consumers,
                reachable,
                warnings,
            )
        )
    return controls


def _literal_control(
    node_id: str,
    class_type: str,
    name: str,
    value: Any,
    input_spec: dict[str, Any],
    node_spec: dict[str, Any],
    title: str | None,
    consumers: dict[str, set[str]],
    reachable: set[str],
    warnings: list[ErrorDetail],
) -> ControlDescriptor:
    logical = input_spec.get("logical_type")
    unresolved: list[ErrorDetail] = []
    options: list[EnumOption] | None = None
    constraints: NumberConstraints | None = None
    multiline = bool(input_spec.get("multiline"))
    reason = f"catalog:{logical}"
    component: Component
    kind: LogicalType

    if logical == "STRING":
        kind, component = "string", ("textarea" if multiline else "text")
        encoded: Any = value if isinstance(value, str) else None
        if not isinstance(value, str):
            encoded = _preserved(value, warnings, node_id, class_type, name, "STRING")
            component = "readonly"
            kind = "unknown"
        elif _PROMPT_MACRO.search(value):
            # Frontend-only conveniences are not recreated from the exported
            # scalar; the literal text is submitted exactly as imported.
            reason += ",macro_preserved_literally"
            warnings.append(
                ErrorDetail(
                    field=f"{node_id}.inputs.{name}",
                    code="macro_not_evaluated",
                    message=(
                        f"{class_type}.{name} contains prompt macro or dynamic-prompt syntax. "
                        "It is preserved literally and sent to ComfyUI unchanged; this app "
                        "does not expand it."
                    ),
                )
            )
        value = encoded

    elif logical == "BOOLEAN":
        kind, component = "boolean", "checkbox"
        if not isinstance(value, bool):
            value = _preserved(value, warnings, node_id, class_type, name, "BOOLEAN")
            kind, component = "unknown", "readonly"

    elif logical in ("INT", "INT_EXACT"):
        kind = "int"
        if isinstance(value, bool) or not isinstance(value, int):
            value = _preserved(value, warnings, node_id, class_type, name, "INT")
            kind, component = "unknown", "readonly"
        elif logical == "INT_EXACT":
            # Exact integers cross the wire as decimal strings and never drive a
            # slider (app/contracts.py, fixtures expectations/seed_and_transport).
            component = "seed" if input_spec.get("seed_like") else "number"
            constraints = NumberConstraints(
                exact_min=_exact(input_spec.get("min")), exact_max=_exact(input_spec.get("max"))
            )
            value = str(value)
            reason += ",exact_transport"
        else:
            constraints = NumberConstraints(
                min=_number(input_spec.get("min")),
                max=_number(input_spec.get("max")),
                step=_number(input_spec.get("step")),
            )
            component = "slider" if _sliderable(constraints) else "number"
            value = str(value)

    elif logical == "FLOAT":
        kind = "float"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            value = _preserved(value, warnings, node_id, class_type, name, "FLOAT")
            kind, component = "unknown", "readonly"
        else:
            constraints = NumberConstraints(
                min=_number(input_spec.get("min")),
                max=_number(input_spec.get("max")),
                step=_number(input_spec.get("step")),
            )
            component = "slider" if _sliderable(constraints) else "number"
            value = float(value)

    elif logical == "COMBO":
        kind, component = "enum", "select"
        choices = [c for c in input_spec.get("choices", []) if isinstance(c, str)]
        options = [EnumOption(value=c, label=c, available=True) for c in choices]
        if isinstance(value, str) and choices and value not in choices:
            # The imported value is shown and must be corrected; it is never
            # swapped for the first remaining option.
            options.append(EnumOption(value=value, label=f"{value} (not installed)", available=False))
            unresolved.append(
                ErrorDetail(
                    field=f"{node_id}.inputs.{name}",
                    code="choice_missing",
                    message=(
                        f"{value!r} is not offered for {class_type}.{name} by the current "
                        "catalog. Choose a replacement; the imported value is kept until you do."
                    ),
                )
            )
        elif not isinstance(value, str):
            value = _preserved(value, warnings, node_id, class_type, name, "COMBO")
            kind, component, options = "unknown", "readonly", None

    elif logical == "OWNED_INPUT_REF":
        # Shared ComfyUI input-directory filenames are withheld by the catalog
        # projection and are never re-offered here as a generic dropdown.
        kind = "file"
        if input_spec.get("loader_adapter"):
            component = "file"
            reason = f"loader_adapter:{input_spec['loader_adapter']}"
        else:
            component, reason = "readonly", "adapter_required"
            warnings.append(
                ErrorDetail(
                    field=f"{node_id}.inputs.{name}",
                    code="adapter_required",
                    message=(
                        f"{class_type}.{name} takes a {input_spec.get('media_kind', 'file')} from "
                        "the shared ComfyUI input directory and no supported upload adapter "
                        "exists for it. The imported value is preserved and submittable."
                    ),
                )
            )
        value = value if isinstance(value, str) else None

    elif logical == "WILDCARD":
        kind, component, reason = "unknown", "readonly", "wildcard_literal"

    else:  # UNSUPPORTED, or a shape this build does not model
        kind, component, reason = "unknown", "readonly", "adapter_required"
        warnings.append(
            ErrorDetail(
                field=f"{node_id}.inputs.{name}",
                code="adapter_required",
                message=(
                    f"{class_type}.{name} is a {input_spec.get('socket_type', 'custom')} value with "
                    "no described editing contract. It is preserved exactly as imported and "
                    "submitted unchanged; editing it needs a typed adapter."
                ),
            )
        )

    if component == "readonly" and not isinstance(value, (str, bool, float, type(None))):
        value = _encode_opaque(value)

    group, label, group_reason = _group_and_label(
        node_id, class_type, name, input_spec, node_spec, title, consumers, kind
    )
    if node_id not in reachable:
        group = "inactive"

    return ControlDescriptor(
        binding_id=f"{node_id}:{name}",
        node_id=node_id,
        class_type=class_type,
        input_name=name,
        logical_type=kind,
        value=value,
        component=component,
        group=group,
        order=0,
        label=label,
        help_text=None,
        constraints=constraints,
        options=options,
        multiline=multiline,
        inference_reason=f"{reason};{group_reason}",
        unresolved=unresolved,
        raw_metadata=_raw_metadata(input_spec),
    )


def _flexible_control(
    node_id: str,
    class_type: str,
    name: str,
    value: Any,
    node_spec: dict[str, Any],
    title: str | None,
    reachable: set[str],
    warnings: list[ErrorDetail],
) -> ControlDescriptor:
    """An input object_info does not enumerate: kept, never rejected by name.

    Wildcard and dynamically named inputs are not fully described by
    object_info, so naive exact name matching must not reject them
    (docs/WORKFLOW_MAPPING.md).
    """
    flexible = bool(node_spec.get("flexible_inputs"))
    warnings.append(
        ErrorDetail(
            field=f"{node_id}.inputs.{name}",
            code="flexible_input" if flexible else "undeclared_input",
            message=(
                f"{class_type}.{name} is not enumerated by the node metadata. The imported "
                "value is preserved and submitted unchanged."
            ),
        )
    )
    return ControlDescriptor(
        binding_id=f"{node_id}:{name}",
        node_id=node_id,
        class_type=class_type,
        input_name=name,
        logical_type="unknown",
        value=_encode_opaque(value),
        component="readonly",
        group="inactive" if node_id not in reachable else "advanced",
        order=0,
        label=_qualify(title, class_type, node_id, _pretty(name)),
        inference_reason="flexible_input" if flexible else "undeclared_input",
        raw_metadata={"imported_value_kind": type(value).__name__},
    )


def _unknown_class_controls(
    node_id: str, node: dict[str, Any], graph: dict[str, Any], reachable: set[str]
) -> list[ControlDescriptor]:
    """Every literal of an uninstalled class, preserved and read-only."""
    class_type = node["class_type"]
    title = _title(node)
    controls = []
    for name, value in node.get("inputs", {}).items():
        if looks_like_link(value) and value[0] in graph:
            continue
        controls.append(
            ControlDescriptor(
                binding_id=f"{node_id}:{name}",
                node_id=node_id,
                class_type=class_type,
                input_name=name,
                logical_type="unknown",
                value=_encode_opaque(value),
                component="readonly",
                group="inactive" if node_id not in reachable else "advanced",
                order=0,
                label=_qualify(title, class_type, node_id, _pretty(name)),
                inference_reason="class_missing",
                raw_metadata={"imported_value_kind": type(value).__name__},
            )
        )
    return controls


def _branch_controls(
    graph: dict[str, Any], catalog_nodes: dict[str, Any], output_nodes: list[str]
) -> list[ControlDescriptor]:
    """One read-only row per declared output branch, with its media role.

    Every original output branch is kept and shown; nothing assumes a single
    final image (docs/WORKFLOW_MAPPING.md "Import and normalization").
    """
    controls = []
    for node_id in output_nodes:
        node = graph[node_id]
        class_type = node["class_type"]
        role = catalog_nodes.get(class_type, {}).get("output_role", "unknown")
        controls.append(
            ControlDescriptor(
                binding_id=f"{node_id}:__branch__",
                node_id=node_id,
                class_type=class_type,
                input_name="",
                logical_type="unknown",
                value=role,
                component="readonly",
                group="output",
                order=0,
                label=_title(node) or class_type,
                help_text=f"Output branch: {role.replace('_', ' ')}.",
                inference_reason=f"output_branch:{role}",
                raw_metadata={"output_role": role},
            )
        )
    return controls


def _check_required(
    node_id: str, node: dict[str, Any], spec: dict[str, Any], blocking: list[ErrorDetail]
) -> None:
    """Absent *required* inputs block; absent optionals stay absent.

    Backend-hidden inputs are not in the sanitized projection, so they are never
    fabricated as form values.
    """
    present = set(node.get("inputs", {}))
    for name, input_spec in spec.get("inputs", {}).items():
        if input_spec.get("optional") or name in present:
            continue
        blocking.append(
            ErrorDetail(
                field=f"{node_id}.inputs.{name}",
                code="required_input_missing",
                message=(
                    f"Node {node_id} ({node['class_type']}) is missing the required input "
                    f"{name!r}. No default is invented for it."
                ),
            )
        )


# --- grouping and labels --------------------------------------------------


def _group_and_label(
    node_id: str,
    class_type: str,
    name: str,
    input_spec: dict[str, Any],
    node_spec: dict[str, Any],
    title: str | None,
    consumers: dict[str, set[str]],
    kind: LogicalType,
) -> tuple[Group, str, str]:
    outputs = node_spec.get("outputs", [])
    pretty = _pretty(name)

    if "CONDITIONING" in outputs and kind == "string":
        role, reason = _conditioning_role(node_id, consumers)
        label = title or role
        return "prompts", label, reason

    if kind == "file":
        return "inputs", _qualify(title, class_type, node_id, pretty), "owned_input"

    if node_spec.get("output_node") and name == "filename_prefix":
        return "output", _qualify(title, class_type, node_id, pretty), "output_filename"

    if name in _VIDEO_INPUTS and ("VIDEO" in outputs or node_spec.get("output_role") == "video"):
        return "video", _qualify(title, class_type, node_id, pretty), "video_input_name"

    if name in _DIMENSION_INPUTS:
        return "dimensions", _qualify(title, class_type, node_id, pretty), "dimension_input_name"

    if name in _GENERATION_INPUTS:
        return "generation", _qualify(title, class_type, node_id, pretty), "generation_input_name"

    if kind == "enum" and input_spec.get("choices_source") == "server_model_catalog":
        return "model", _qualify(title, class_type, node_id, pretty), "model_catalog_choices"

    return "advanced", _qualify(title, class_type, node_id, pretty), "default_group"


def _conditioning_role(node_id: str, consumers: dict[str, set[str]]) -> tuple[str, str]:
    """Positive/negative comes from where the CONDITIONING output goes.

    Feeding both, or passing through an unknown transform, is labelled neutrally
    and offered for correction rather than guessed.
    """
    fed = consumers.get(node_id, set())
    if fed == {_POSITIVE}:
        return "Positive prompt", "conditioning_traced:positive"
    if fed == {_NEGATIVE}:
        return "Negative prompt", "conditioning_traced:negative"
    if not fed:
        return "Prompt (unconnected)", "conditioning_traced:unconnected"
    return "Prompt", "conditioning_traced:ambiguous"


def _qualify(title: str | None, class_type: str, node_id: str, pretty: str) -> str:
    """Repeated stages stay distinguishable: node title, else class and id."""
    return f"{title or f'{class_type} {node_id}'}: {pretty}"


def _pretty(name: str) -> str:
    return name.replace("_", " ").strip().capitalize() or name


def _title(node: dict[str, Any]) -> str | None:
    meta = node.get("_meta")
    title = meta.get("title") if isinstance(meta, dict) else None
    return title if isinstance(title, str) and title else None


def _order(controls: list[ControlDescriptor]) -> list[ControlDescriptor]:
    rank = {group: index for index, group in enumerate(GROUP_ORDER)}
    ordered = sorted(
        controls,
        key=lambda c: (
            rank.get(c.group, len(rank)),
            (0, int(c.node_id), "") if c.node_id.lstrip("-").isdigit() else (1, 0, c.node_id),
            c.input_name,
        ),
    )
    return [control.model_copy(update={"order": index}) for index, control in enumerate(ordered)]


# --- encoding helpers -----------------------------------------------------


def _sliderable(constraints: NumberConstraints) -> bool:
    """A slider needs a sensible finite range and never an exact integer."""
    low, high = constraints.min, constraints.max
    return low is not None and high is not None and high > low


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _exact(value: Any) -> str | None:
    """Catalog bounds for exact ints arrive as decimal strings already."""
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _preserved(
    value: Any,
    warnings: list[ErrorDetail],
    node_id: str,
    class_type: str,
    name: str,
    declared: str,
) -> Any:
    """A literal whose shape does not match the declared scalar.

    Presentation-only: a literal array on a STRING input is shown read-only and
    submitted byte-for-byte rather than being rewritten as a link or dropped.
    """
    warnings.append(
        ErrorDetail(
            field=f"{node_id}.inputs.{name}",
            code="literal_shape_unexpected",
            message=(
                f"{class_type}.{name} is declared {declared} but holds a "
                f"{type(value).__name__}. The imported value is preserved exactly and "
                "submitted unchanged; it is shown read-only."
            ),
        )
    )
    return _encode_opaque(value)


def _encode_opaque(value: Any) -> str | bool | float | None:
    """Render a non-scalar literal for display without altering the stored graph."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)  # exact: never a JSON number on the wire
    if isinstance(value, float):
        return value
    return json.dumps(value, separators=(",", ":"))


def _raw_metadata(input_spec: dict[str, Any]) -> dict[str, Any] | None:
    """Preserved upstream hints, shown as diagnostics and never executed."""
    kept = {
        key: input_spec[key]
        for key in ("force_input_hint", "dynamic_prompts_hint", "managed", "seed_like", "media_kind")
        if key in input_spec
    }
    return kept or None
