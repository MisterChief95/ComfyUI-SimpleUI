"""Frozen API contracts shared by every feature task.

Rules that the rest of the application must not break:

* Exact integers (seeds, IDs, byte counts) cross the wire as decimal STRINGS
  (`ExactInt`). JavaScript loses precision above 2**53; never send them as JSON
  numbers, and never `JSON.parse` a raw workflow graph in the browser.
* `owner_id` is derived from the session on the server. It is response-only;
  a request that carries one is rejected rather than trusted.
* Anything editable carries a `revision`; writes send `expected_revision` and
  get 409 on mismatch.
* Lists are cursor-paginated (`Page`), never offset-paginated.
* Every non-2xx response is an `ErrorEnvelope`.

The mirror of this file is frontend/src/lib/contracts.ts. Change both together.
"""

from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

T = TypeVar("T")

#: A signed integer of arbitrary magnitude, transported as a decimal string.
ExactInt = Annotated[str, StringConstraints(pattern=r"^-?(0|[1-9][0-9]*)$")]

#: Opaque server-issued identifier (profile, workflow, generation, media, ...).
Id = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class Model(BaseModel):
    """Base: reject unknown request fields instead of silently ignoring them."""

    model_config = ConfigDict(extra="forbid")


# --- errors ---------------------------------------------------------------

ErrorCode = Literal[
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "unprocessable",
    "rate_limited",
    "upstream_unavailable",
    "internal",
]


class ErrorDetail(Model):
    """One machine-readable problem. `field` is a dotted request path if any."""

    field: str | None = None
    code: str
    message: str


class ApiError(Model):
    code: ErrorCode
    message: str
    request_id: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorEnvelope(Model):
    error: ApiError


# --- pagination -----------------------------------------------------------


class Page(Model, Generic[T]):
    items: list[T]
    #: Opaque; pass back as `?cursor=`. Null means the end of the list.
    next_cursor: str | None = None


# --- ownership and revisions ---------------------------------------------


class Owned(Model):
    """Response-only ownership stamp. Never accepted from a request body."""

    owner_id: Id


class Revisioned(Model):
    revision: int = Field(ge=0)


# --- control descriptors --------------------------------------------------

LogicalType = Literal["string", "int", "float", "boolean", "enum", "file", "unknown"]

Component = Literal[
    "text",
    "textarea",
    "number",
    "slider",
    "checkbox",
    "select",
    "seed",
    "file",
    "readonly",
]

Group = Literal[
    "prompts",
    "model",
    "generation",
    "dimensions",
    "inputs",
    "video",
    "advanced",
    "output",
    "inactive",
]


class NumberConstraints(Model):
    """Declared bounds. Exact-int controls use the string fields, not min/max."""

    min: float | None = None
    max: float | None = None
    step: float | None = None
    exact_min: ExactInt | None = None
    exact_max: ExactInt | None = None


class EnumOption(Model):
    #: The literal value sent back to ComfyUI, in the JSON type ComfyUI listed it with;
    #: `label` is presentation only.
    value: str | bool | int | float
    label: str
    available: bool = True


class ControlDescriptor(Model):
    """One editable literal in an imported graph.

    `value` is the encoded current value: string for `string`/`enum`/`file`,
    bool for `boolean`, float for `float`, and an `ExactInt` decimal string for
    `int`. Svelte picks a component from `Component`; it never invents one.
    """

    binding_id: Id
    node_id: str
    class_type: str
    input_name: str
    logical_type: LogicalType
    value: str | bool | int | float | None
    component: Component
    group: Group
    order: int
    label: str
    help_text: str | None = None
    constraints: NumberConstraints | None = None
    options: list[EnumOption] | None = None
    multiline: bool = False
    #: Why this presentation was chosen, for the mapping editor.
    inference_reason: str
    #: Non-empty means the user must resolve something before submission.
    unresolved: list[ErrorDetail] = Field(default_factory=list)
    #: Preserved upstream metadata, shown as diagnostics. Never executed.
    raw_metadata: dict[str, Any] | None = None


class ControlSchema(Owned, Revisioned):
    """The full set of controls for one workflow revision."""

    workflow_id: Id
    controls: list[ControlDescriptor]
    #: Problems that block submission outright (missing nodes, bad links).
    blocking: list[ErrorDetail] = Field(default_factory=list)
    #: Presentation-only warnings; submission is still allowed.
    warnings: list[ErrorDetail] = Field(default_factory=list)


# --- health ---------------------------------------------------------------


class Health(Model):
    status: Literal["ok"]
    version: str
    #: Server time as epoch milliseconds, exact.
    time_ms: ExactInt
