"""Seed policy and the submission copy of a stored graph.

Seed policy is **application state**, deliberately separate from the graph
(docs/WORKFLOW_MAPPING.md): the stored graph keeps the seed exactly as
imported, and a policy decides what a given submission sends.

* Imported seeds default to ``fixed``.
* ``random`` and ``increment`` resolve **once**, when a new local request is
  accepted. GEN-001 persists the resolved value with the generation; a retry of
  the same client request key re-reads it and calls nothing here, so a retry, a
  re-render, a reconnect or a resubmission after ``submission_unknown`` never
  re-randomizes or re-advances a counter.
* Overflow past the declared maximum is reported, never wrapped to zero.

``build_submission_graph`` is the only place a graph is copied for execution. It
deep-copies the stored graph and writes back only the values the user actually
edited: topology, omitted optionals, unknown keys, opaque literals and literal
arrays are carried through untouched. One Generate action submits one graph --
a latent ``batch_size`` stays a node input and is never expanded into N
requests.
"""

from __future__ import annotations

import copy
import random
from typing import Any, Literal

from ..catalog.normalize import same_choice
from ..contracts import ControlDescriptor, ControlSchema, ErrorDetail

SeedPolicy = Literal["fixed", "random", "increment"]

#: ComfyUI's own ceiling for a seed input when metadata declares no maximum.
DEFAULT_SEED_MAX = 2**64 - 1


class SubmissionError(ValueError):
    """A submission cannot be built. ``detail`` is client-safe."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.detail = ErrorDetail(field=field, code=code, message=message)


def resolve_seed(
    current: int,
    policy: SeedPolicy,
    *,
    minimum: int = 0,
    maximum: int = DEFAULT_SEED_MAX,
    rng: random.Random | None = None,
) -> int:
    """The seed for **one newly accepted** request. Call once per request.

    Raises ``SubmissionError`` on increment overflow rather than wrapping.
    """
    if policy == "fixed":
        return current
    if policy == "random":
        return (rng or random).randint(minimum, maximum)
    if policy == "increment":
        if current >= maximum:
            raise SubmissionError(
                "seed_overflow",
                f"The seed is already at its maximum ({maximum}) and cannot be incremented. "
                "Choose a lower seed or switch the policy; it is not wrapped to zero.",
            )
        return current + 1
    raise SubmissionError("unknown_seed_policy", f"Unknown seed policy {policy!r}.")


def build_submission_graph(
    graph: dict[str, Any],
    schema: ControlSchema,
    edits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deep-copy ``graph`` and apply ``{binding_id: encoded value}``.

    Values arrive encoded exactly as ``ControlDescriptor.value`` is: integers as
    decimal strings, floats as numbers, booleans as JSON booleans, enums and files
    as strings. They are decoded back to the JSON types ComfyUI expects. Anything
    not named in ``edits`` is left byte-identical to the stored graph.
    """
    if schema.blocking:
        raise SubmissionError(
            "workflow_not_submittable",
            "This workflow has unresolved execution errors: "
            + "; ".join(detail.message for detail in schema.blocking),
        )
    unresolved = [c for c in schema.controls if c.unresolved]
    if unresolved:
        raise SubmissionError(
            "mapping_unresolved",
            "These controls still need a choice: "
            + "; ".join(f"{c.label} ({c.binding_id})" for c in unresolved),
            field=unresolved[0].binding_id,
        )

    return apply_edits(graph, schema, edits or {})


def apply_edits(
    graph: dict[str, Any], schema: ControlSchema, edits: dict[str, Any]
) -> dict[str, Any]:
    """Deep-copy ``graph`` and decode only the bindings named in ``edits``."""
    updated = copy.deepcopy(graph)
    if not edits:
        return updated

    by_id = {control.binding_id: control for control in schema.controls}
    for binding_id, raw in edits.items():
        control = by_id.get(binding_id)
        if control is None:
            raise SubmissionError(
                "unknown_binding",
                f"No control named {binding_id!r} in this workflow.",
                binding_id,
            )
        if control.component == "readonly":
            raise SubmissionError(
                "control_not_editable",
                f"{control.label} is preserved as imported and cannot be edited without a "
                "typed adapter.",
                binding_id,
            )
        node = updated.get(control.node_id)
        if node is None:  # pragma: no cover - schema is built from this graph
            raise SubmissionError(
                "unknown_binding",
                f"Node {control.node_id} is not in the graph.",
                binding_id,
            )
        node["inputs"][control.input_name] = _decode(control, raw)
    return updated


def _decode(control: ControlDescriptor, raw: Any) -> Any:
    """Encoded control value -> the JSON type ComfyUI expects. Never clamped."""
    binding = control.binding_id
    if control.logical_type == "int":
        value = _as_exact_int(raw, binding)
        _check_exact_bounds(control, value, binding)
        return value
    if control.logical_type == "float":
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise SubmissionError(
                "invalid_value", f"{control.label} must be a number.", binding
            )
        try:
            value = float(raw)
        except ValueError as exc:
            raise SubmissionError(
                "invalid_value", f"{control.label} must be a number.", binding
            ) from exc
        if value != value or value in (float("inf"), float("-inf")):
            raise SubmissionError(
                "non_finite_number",
                f"{control.label} must be a finite number; it is not clamped or rounded.",
                binding,
            )
        _check_bounds(control, value, binding)
        return value
    if control.logical_type == "boolean":
        # A real JSON boolean is the contract; the exact strings "true"/"false"
        # are accepted too because earlier clients sent checkboxes that way.
        if isinstance(raw, str) and raw in ("true", "false"):
            return raw == "true"
        if not isinstance(raw, bool):
            raise SubmissionError(
                "invalid_value", f"{control.label} must be true or false.", binding
            )
        return raw
    if control.logical_type == "enum":
        # The option itself is returned, so ComfyUI gets the type it listed.
        for option in control.options or []:
            if option.available and same_choice(raw, option.value):
                return option.value
        raise SubmissionError(
            "invalid_value",
            f"{raw!r} is not an installed choice for {control.label}.",
            binding,
        )
    if control.logical_type in ("string", "file"):
        if not isinstance(raw, str):
            raise SubmissionError(
                "invalid_value", f"{control.label} must be text.", binding
            )
        return raw
    raise SubmissionError(
        "control_not_editable",
        f"{control.label} has no editable value contract.",
        binding,
    )


def _as_exact_int(raw: Any, binding: str) -> int:
    """Exact integers travel as decimal strings; a float is refused outright."""
    if isinstance(raw, bool):
        raise SubmissionError(
            "invalid_value", f"{binding} must be a whole number.", binding
        )
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.lstrip("-").isdigit():
        return int(raw)
    raise SubmissionError(
        "invalid_value",
        f"{binding} must be a whole number sent as a decimal string, so that a 64-bit "
        "value is not rounded in transit.",
        binding,
    )


def _check_exact_bounds(control: ControlDescriptor, value: int, binding: str) -> None:
    constraints = control.constraints
    if constraints is None:
        return
    # Exact controls carry string bounds; ordinary INT controls carry numbers.
    low = (
        int(constraints.exact_min)
        if constraints.exact_min is not None
        else _floor(constraints.min)
    )
    high = (
        int(constraints.exact_max)
        if constraints.exact_max is not None
        else _floor(constraints.max)
    )
    if (low is not None and value < low) or (high is not None and value > high):
        raise SubmissionError(
            "value_out_of_range",
            f"{control.label} is outside the range this node accepts ({low}..{high}). "
            "Correct the value; it is not clamped or wrapped.",
            binding,
        )


def _floor(value: float | None) -> int | None:
    return None if value is None else int(value)


def _check_bounds(control: ControlDescriptor, value: float, binding: str) -> None:
    constraints = control.constraints
    if constraints is None:
        return
    low, high = constraints.min, constraints.max
    if (low is not None and value < low) or (high is not None and value > high):
        raise SubmissionError(
            "value_out_of_range",
            f"{control.label} is outside the range this node accepts ({low}..{high}). "
            "Correct the value; it is not clamped or rounded.",
            binding,
        )
