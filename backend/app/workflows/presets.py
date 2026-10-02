"""Saved value presets: models and normalization.

A preset is a named snapshot of control values for one profile's workflow
(docs/UI_DESIGNER.md "Presets"). Values are keyed by binding id and are not
checked against the current schema -- a preset may outlive graph changes and the
client filters on apply. Integers are never lossy: a JSON integer beyond 2**53
is stored and returned as an ExactInt decimal string.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..contracts import ExactInt, Id, Model, Revisioned

MAX_PRESETS = 100
MAX_VALUES = 500
MAX_VALUES_BYTES = 256 * 1024
_SAFE_INT = 2**53

PresetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
BindingId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Scalar = str | bool | int | Annotated[float, Field(allow_inf_nan=False)]
PresetValues = Annotated[dict[BindingId, Scalar], Field(max_length=MAX_VALUES)]


def normalize_values(values: dict[str, Scalar]) -> dict[str, Scalar]:
    """Big integers become ExactInt strings; the serialized form must fit the cap."""
    out = {
        key: str(v) if type(v) is int and abs(v) >= _SAFE_INT else v for key, v in values.items()
    }
    if len(json.dumps(out, allow_nan=False).encode()) > MAX_VALUES_BYTES:
        raise ValueError(f"preset values may serialize to at most {MAX_VALUES_BYTES} bytes")
    return out


class _Values(Model):
    @field_validator("values", check_fields=False)
    @classmethod
    def _normalize(cls, values: dict | None) -> dict | None:
        return None if values is None else normalize_values(values)


class SavePreset(_Values):
    name: PresetName
    values: PresetValues


class UpdatePreset(_Values):
    name: PresetName | None = None
    values: PresetValues | None = None
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def _something_to_change(self) -> UpdatePreset:
        if self.name is None and self.values is None:
            raise ValueError("send a name, values, or both")
        return self


class Preset(Revisioned):
    id: Id
    name: str
    values: dict[str, str | bool | int | float]
    created_ms: ExactInt
    updated_ms: ExactInt
