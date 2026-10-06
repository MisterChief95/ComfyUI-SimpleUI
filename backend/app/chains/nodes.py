"""Chain Input/Output node matching (node pack contract 2, done app-side).

The pack's Chain Output nodes are passthroughs: the file a stage hands on is
the one a real output node (SaveImage, PreviewAny, ...) downstream of it
captured. A Chain Input node is filled at submission. Matching order:

1. an explicit link chosen in the chain definition wins;
2. otherwise a *named* input binds to the same-named output of the nearest
   earlier stage;
3. no valid reference (or duplicate output names in one workflow) is an error
   reported before anything is submitted. Names are case-sensitive and
   trimmed; empty names never auto-match, and an unnamed input with no
   explicit link keeps its own widget value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

IMAGE_OUTPUT = "SimpleUIChainOutput"
TEXT_OUTPUT = "SimpleUIChainOutputText"
IMAGE_INPUT = "SimpleUIChainInputImage"
TEXT_INPUT = "SimpleUIChainInputText"


class ChainNodeError(ValueError):
    """A chain-node reference problem, safe to show to the user."""


@dataclass(frozen=True)
class Reference:
    """Where a Chain Input gets its value: a stage's captured output."""

    binding_id: str
    kind: str  # "image" | "text"
    stage: int
    output_node: str  # the real output node downstream of the Chain Output


def _name(node: dict[str, Any]) -> str:
    value = (node.get("inputs") or {}).get("name")
    return value.strip() if isinstance(value, str) else ""


def _nodes(graph: dict[str, Any], *classes: str):
    for node_id in sorted(graph, key=lambda i: (len(i), i)):
        node = graph[node_id]
        if isinstance(node, dict) and node.get("class_type") in classes:
            yield node_id, node


def named_outputs(graph: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """``{name: (node_id, kind)}``; duplicate non-empty names are an error."""
    found: dict[str, tuple[str, str]] = {}
    for node_id, node in _nodes(graph, IMAGE_OUTPUT, TEXT_OUTPUT):
        name = _name(node)
        if not name:
            continue
        if name in found:
            raise ChainNodeError(
                f'Chain Output name "{name}" is used more than once in a workflow.'
            )
        found[name] = (
            node_id,
            "image" if node["class_type"] == IMAGE_OUTPUT else "text",
        )
    return found


def save_node(graph: dict[str, Any], catalog: dict[str, Any], source: str) -> str:
    """The nearest real output node downstream of ``source`` (breadth first)."""
    seen, frontier = {source}, [source]
    while frontier:
        nxt: list[str] = []
        for node_id, node in sorted(graph.items(), key=lambda kv: (len(kv[0]), kv[0])):
            if node_id in seen or not isinstance(node, dict):
                continue
            consumes = any(
                isinstance(v, list) and len(v) == 2 and str(v[0]) in frontier
                for v in (node.get("inputs") or {}).values()
            )
            if not consumes:
                continue
            if (catalog.get(node.get("class_type")) or {}).get("output_node"):
                return node_id
            seen.add(node_id)
            nxt.append(node_id)
        frontier = nxt
    raise ChainNodeError(
        f"Chain Output {source} has no downstream save or preview node to capture."
    )


def plan_stage(
    index: int,
    graphs: list[dict[str, Any]],
    catalog: dict[str, Any],
    explicit: set[str],
) -> list[Reference]:
    """Resolve stage ``index``'s named Chain Inputs; validates every stage's outputs."""
    for graph in graphs[: index + 1]:
        for node_id, _ in named_outputs(graph).values():
            save_node(graph, catalog, node_id)
    references = []
    for node_id, node in _nodes(graphs[index], IMAGE_INPUT, TEXT_INPUT):
        kind = "image" if node["class_type"] == IMAGE_INPUT else "text"
        binding_id = f"{node_id}:{'image' if kind == 'image' else 'text'}"
        name = _name(node)
        if not name or binding_id in explicit:
            continue
        for stage in range(index - 1, -1, -1):
            match = named_outputs(graphs[stage]).get(name)
            if match is None:
                continue
            if match[1] != kind:
                raise ChainNodeError(
                    f'Stage {index + 1}: Chain Input "{name}" is {kind} but stage {stage + 1} '
                    f"outputs {match[1]} under that name."
                )
            references.append(
                Reference(
                    binding_id,
                    kind,
                    stage,
                    save_node(graphs[stage], catalog, match[0]),
                )
            )
            break
        else:
            raise ChainNodeError(
                f'Stage {index + 1}: Chain Input "{name}" has no earlier Chain Output '
                "with that name; add one or choose an explicit source."
            )
    return references
