"""Saved run-page layouts: models, invariants, and stale-binding detection.

A layout is presentation only (docs/UI_DESIGNER.md "Layout document (v2)"): it
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
MAX_ROWS = 40  # Per panel section; empty rows/columns still count.
MAX_COLUMNS = 3  # Per row, equal widths.

StructuralId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]

BindingId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Dimension = Annotated[int, Field(strict=True, gt=0, le=16384)]
#: Optional binding reference, omitted from output when unset.
OptionalBinding = Annotated[
    BindingId | None, Field(default=None, exclude_if=lambda v: v is None)
]


class ControlItem(Model):
    """One placed control. ``kind`` is the discriminator for later typed composites."""

    kind: Literal["control"]
    binding_id: BindingId
    span: Literal["auto", "full"] = "auto"
    #: Boolean binding; the item shows on the run page only while it is true.
    #: A reference, not a placement: several items may share one condition.
    when: OptionalBinding = None


class AspectRatioItem(Model):
    kind: Literal["aspect_ratio"]
    width: BindingId
    height: BindingId
    presets: list[tuple[Dimension, Dimension]] | None = Field(
        default=None, max_length=24
    )
    span: Literal["auto", "full"] = "auto"
    when: OptionalBinding = None


LayoutItem = Annotated[ControlItem | AspectRatioItem, Field(discriminator="kind")]


def item_bindings(item: ControlItem | AspectRatioItem) -> list[str]:
    return (
        [item.binding_id]
        if isinstance(item, ControlItem)
        else [item.width, item.height]
    )


class SectionBase(Model):
    id: StructuralId
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    #: Default disclosure on the run page; the live open state is per-device.
    collapsed: StrictBool
    #: Boolean binding rendered as the header switch; the body folds away while
    #: it is false. It places that control, so it counts toward uniqueness.
    toggle: OptionalBinding = None


class AutoSection(SectionBase):
    mode: Literal["auto"] = "auto"
    columns: Literal[1, 2, 3]
    items: list[LayoutItem] = Field(max_length=MAX_ENTRIES)


class LayoutColumn(Model):
    id: StructuralId
    items: list[LayoutItem] = Field(max_length=MAX_ENTRIES)


class LayoutRow(Model):
    id: StructuralId
    columns: list[LayoutColumn] = Field(min_length=1, max_length=MAX_COLUMNS)


class PanelSection(SectionBase):
    mode: Literal["panels"]
    rows: list[LayoutRow] = Field(max_length=MAX_ROWS)


LayoutSection = Annotated[AutoSection | PanelSection, Field(discriminator="mode")]


def section_items(section: LayoutSection) -> list[ControlItem | AspectRatioItem]:
    return (
        section.items
        if isinstance(section, AutoSection)
        else [
            item
            for row in section.rows
            for column in row.columns
            for item in column.items
        ]
    )


class LayoutDoc(Model):
    version: Literal[2]
    sections: list[LayoutSection] = Field(max_length=MAX_SECTIONS)
    hidden: list[BindingId] = Field(max_length=MAX_ENTRIES)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value):
        if isinstance(value, dict):
            version = value.get("version")
            # Reject bool/float versions rather than accepting Python equality/coercion.
            if type(version) is not int or version not in (1, 2):
                raise ValueError("version must be 1 or 2")
            sections = value.get("sections")
            if isinstance(sections, list):
                if version == 1 and any(
                    isinstance(s, dict) and ("mode" in s or "rows" in s)
                    for s in sections
                ):
                    raise ValueError("v1 sections must use columns and items")
                sections = [
                    dict(s, mode=s.get("mode", "auto")) if isinstance(s, dict) else s
                    for s in sections
                ]
            return {**value, "version": 2, "sections": sections}
        return value

    @model_validator(mode="after")
    def _invariants(self) -> LayoutDoc:
        ids = [section.id for section in self.sections]
        if len(set(ids)) != len(ids):
            raise ValueError("section ids must be unique")
        structural_ids = [
            identifier
            for s in self.sections
            if isinstance(s, PanelSection)
            for row in s.rows
            for identifier in [row.id, *(c.id for c in row.columns)]
        ]
        if len(set(structural_ids)) != len(structural_ids):
            raise ValueError("row and column ids must be unique across the document")
        bindings = self.bindings()
        if len(bindings) > MAX_ENTRIES:
            raise ValueError(
                f"a layout holds at most {MAX_ENTRIES} items and hidden controls"
            )
        if len(set(bindings)) != len(bindings):
            raise ValueError(
                "a control may appear only once across sections and hidden"
            )
        return self

    def bindings(self) -> list[str]:
        toggles = [s.toggle for s in self.sections if s.toggle is not None]
        return (
            [
                binding
                for s in self.sections
                for item in section_items(s)
                for binding in item_bindings(item)
            ]
            + toggles
            + self.hidden
        )


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
