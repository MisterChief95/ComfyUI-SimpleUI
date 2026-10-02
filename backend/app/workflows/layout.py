"""Saved run-page layouts: models, invariants, and stale-binding detection.

A layout is presentation only (docs/UI_DESIGNER.md "Layout document (v1)"): it
arranges and hides controls and never touches the graph, values, or execution.
Every invariant lives in the models below, so a violating body is a normal 422
validation error before any service code runs. Binding ids that are not in the
current schema are kept and reported as stale, never dropped.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StringConstraints, model_validator

from ..contracts import Id, Model, Revisioned

MAX_SECTIONS = 40
MAX_ENTRIES = 1000

BindingId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Dimension = Annotated[int, Field(strict=True, gt=0, le=16384)]


class ControlItem(Model):
    """One placed control. ``kind`` is the discriminator for later typed composites."""

    kind: Literal["control"]
    binding_id: BindingId
    span: Literal["auto", "full"] = "auto"


class AspectRatioItem(Model):
    kind: Literal["aspect_ratio"]
    width: BindingId
    height: BindingId
    presets: list[tuple[Dimension, Dimension]] | None = Field(default=None, max_length=24)
    span: Literal["auto", "full"] = "auto"


LayoutItem = Annotated[ControlItem | AspectRatioItem, Field(discriminator="kind")]


def item_bindings(item: ControlItem | AspectRatioItem) -> list[str]:
    return [item.binding_id] if isinstance(item, ControlItem) else [item.width, item.height]


class LayoutSection(Model):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    columns: Literal[1, 2, 3]
    #: Default disclosure on the run page; the live open state is per-device.
    collapsed: StrictBool
    items: list[LayoutItem]


class LayoutDoc(Model):
    version: Literal[1]
    sections: list[LayoutSection] = Field(max_length=MAX_SECTIONS)
    hidden: list[BindingId]

    @model_validator(mode="after")
    def _invariants(self) -> LayoutDoc:
        ids = [section.id for section in self.sections]
        if len(set(ids)) != len(ids):
            raise ValueError("section ids must be unique")
        bindings = self.bindings()
        if len(bindings) > MAX_ENTRIES:
            raise ValueError(f"a layout holds at most {MAX_ENTRIES} items and hidden controls")
        if len(set(bindings)) != len(bindings):
            raise ValueError("a control may appear only once across sections and hidden")
        return self

    def bindings(self) -> list[str]:
        return [binding for s in self.sections for item in s.items for binding in item_bindings(item)] + self.hidden


class SaveLayout(Model):
    layout: LayoutDoc
    expected_revision: int = Field(ge=0)


class WorkflowLayout(Revisioned):
    workflow_id: Id
    #: The workflow's *current* structural signature, not the one at save time.
    schema_signature: str
    #: Null with revision 0 means nothing is saved: use the automatic layout.
    layout: LayoutDoc | None
    #: Saved binding ids absent from the current control schema.
    stale_bindings: list[str]


def stale_bindings(layout: LayoutDoc | None, binding_ids: set[str]) -> list[str]:
    return [b for b in layout.bindings() if b not in binding_ids] if layout else []
