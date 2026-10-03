"""Parse and validate ComfyUI **API JSON** into an immutable node map.

Everything here is catalog-free: it answers "is this a usable graph at all?"
using only the bytes that were uploaded. Anything that needs node metadata
(missing classes, output-index range, required inputs, reachability) belongs to
controls.py, because docs/WORKFLOW_MAPPING.md requires a graph to be importable
and storable even when no catalog exists.

Refusals raised from here (docs/WORKFLOW_MAPPING.md "Import and normalization"
and tests/fixtures/expectations/rejections.json):

* non-finite numbers -- Python's ``json`` accepts ``NaN``/``Infinity`` by
  default, so ``parse_constant`` is wired to refuse them. Nothing is clamped,
  rounded or defaulted; the offending node and input are named.
* duplicate object keys -- a last-wins parse silently discards user data.
* a regular UI export (``nodes``/``links`` arrays), which gets an actionable
  "export API format" message rather than a generic parse failure.
* broken link targets, malformed node/class/input shapes, and size/depth/count
  bounds.

Integers are parsed by Python and stay exact; a 64-bit seed survives because it
is never turned into a float and never JSON.parsed in a browser
(app/contracts.py).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from ..contracts import ErrorDetail

#: Bounds on a single uploaded graph. Generous for hand-built workflows and
#: small enough that a hostile or corrupt file cannot exhaust memory.
MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 2000
MAX_DEPTH = 32


class GraphImportError(ValueError):
    """The payload is not a usable API graph. ``detail`` is client-safe."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.detail = ErrorDetail(field=field, code=code, message=message)


@dataclass(frozen=True)
class Link:
    """A resolved connection: ``[node_id, output_index]`` in the API JSON."""

    node_id: str
    output_index: int


def _reject_constant(name: str) -> Any:
    raise GraphImportError(
        "non_finite_number",
        f"The graph contains the non-finite JSON constant {name}. Fix the value in "
        "ComfyUI and export again; imported numbers are never clamped, rounded or "
        "replaced with a default.",
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise GraphImportError(
                "duplicate_key",
                f"Duplicate JSON object key {key!r}. Accepting either occurrence would "
                "silently discard the other, so the file is refused.",
                field=key,
            )
        seen.add(key)
    return dict(pairs)


def parse_graph(payload: bytes | str) -> dict[str, Any]:
    """Bytes or text -> a validated API node map. Raises ``GraphImportError``."""
    if isinstance(payload, bytes):
        if len(payload) > MAX_BYTES:
            raise GraphImportError(
                "graph_too_large",
                f"The workflow is larger than the {MAX_BYTES // (1024 * 1024)} MiB import limit.",
            )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GraphImportError(
                "not_utf8", "The workflow file is not valid UTF-8 text."
            ) from exc
    else:
        text = payload
        if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
            raise GraphImportError(
                "graph_too_large",
                f"The workflow is larger than the {MAX_BYTES // (1024 * 1024)} MiB import limit.",
            )

    try:
        document = json.loads(
            text, parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys
        )
    except GraphImportError:
        raise
    except RecursionError as exc:
        raise GraphImportError(
            "graph_too_deep", "The workflow JSON is nested too deeply to import."
        ) from exc
    except ValueError as exc:
        raise GraphImportError(
            "malformed_json", f"The workflow is not valid JSON: {exc}"
        ) from exc

    return _as_node_map(document)


def _as_node_map(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise GraphImportError(
            "not_api_json",
            "A ComfyUI API workflow is a JSON object mapping node IDs to nodes.",
        )

    if isinstance(document.get("nodes"), list) and isinstance(
        document.get("links"), list
    ):
        raise GraphImportError(
            "ui_export_not_api",
            "This is a regular ComfyUI workflow export, not API format. In ComfyUI use "
            "Workflow > Export (API) and import that file instead.",
        )

    # A recognized request envelope: {"prompt": {...}, "client_id": ...}.
    inner = document.get("prompt")
    if _is_node_map(inner):
        document = inner

    if not document:
        raise GraphImportError("empty_graph", "The workflow contains no nodes.")
    if len(document) > MAX_NODES:
        raise GraphImportError(
            "graph_too_large", f"The workflow has more than {MAX_NODES} nodes."
        )

    for node_id, node in document.items():
        if not isinstance(node, dict):
            raise GraphImportError(
                "invalid_node", f"Node {node_id} is not a JSON object.", field=node_id
            )
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            raise GraphImportError(
                "invalid_class_type",
                f"Node {node_id} has no usable class_type.",
                field=f"{node_id}.class_type",
            )
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            raise GraphImportError(
                "invalid_inputs",
                f"The inputs of node {node_id} ({class_type}) are not a JSON object.",
                field=f"{node_id}.inputs",
            )

    _check_depth(document)
    return document


def _is_node_map(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(isinstance(v, dict) and "class_type" in v for v in value.values())
    )


def _check_depth(value: Any, depth: int = 0, field: str = "") -> None:
    if depth > MAX_DEPTH:
        raise GraphImportError(
            "graph_too_deep",
            f"The workflow nests JSON more than {MAX_DEPTH} levels deep.",
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise GraphImportError(
            "non_finite_number",
            "The workflow contains a number outside the finite floating-point range. "
            "Correct the value in ComfyUI and export again.",
            field=field,
        )
    if isinstance(value, dict):
        for key, item in value.items():
            _check_depth(item, depth + 1, f"{field}.{key}" if field else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_depth(item, depth + 1, f"{field}[{index}]")


def looks_like_link(value: Any) -> bool:
    """``["3", 0]`` shape only. Shape alone never settles it.

    A two-element array is not always a link (docs/WORKFLOW_MAPPING.md): callers
    combine this with the declared input type and whether the target node
    actually exists before rewriting anything as a connection.
    """
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
        and value[1] >= 0
    )


def classify_input(
    value: Any, graph: dict[str, Any], *, declared_socket: bool
) -> Link | None:
    """``Link`` when this value is a connection, ``None`` when it is a literal.

    A link-shaped value is a link when it resolves to a node in the graph, or
    when the input is a declared socket -- an unresolved target on a declared
    socket stays a link so it can be reported as a *broken* one rather than
    quietly reinterpreted. A link-shaped value on an undeclared or literal input
    whose target is not a node id stays a literal array; it is never silently
    rewritten as a connection.
    """
    if not looks_like_link(value):
        return None
    target, index = value[0], value[1]
    if target in graph or declared_socket:
        return Link(node_id=target, output_index=index)
    return None
