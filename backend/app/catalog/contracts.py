"""Catalog API shapes, built on the frozen conventions in app/contracts.py.

They live here rather than in app/contracts.py because that file is the frozen
cross-feature contract mirrored in frontend/src/lib/contracts.ts; these are the
catalog feature's own shapes and reuse its base ``Model`` (unknown fields
rejected), ``ExactInt`` (exact integers as decimal strings) and ``ErrorDetail``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..contracts import ErrorDetail, ExactInt, Model

#: ``fresh``       - the last refresh attempt succeeded.
#: ``stale``       - a cached catalog is being served, but the last attempt failed.
#: ``unavailable`` - there is no catalog at all: never fetched and ComfyUI is
#:                   unreachable. This is the explicit first-run-offline state;
#:                   it is never reported as an empty but healthy catalog.
CatalogState = Literal["fresh", "stale", "unavailable"]


class CatalogFreshness(Model):
    """Everything the UI needs to describe the catalog's trustworthiness."""

    state: CatalogState
    #: Short content hash. Changes whenever anything in the catalog changed.
    catalog_revision: str | None = None
    #: Changes only on a type/link/constraint change, not when models are added.
    schema_hash: str | None = None
    #: Last *successful* fetch. A failed attempt does not move it.
    fetched_ms: ExactInt | None = None
    #: Last attempt of any kind, successful or not.
    checked_ms: ExactInt | None = None
    #: Server-enforced: a page refresh cannot bypass it.
    next_refresh_allowed_ms: ExactInt | None = None
    cooldown_active: bool = False
    consecutive_failures: int = 0
    #: False while no catalog exists: graphs may be imported and stored, but
    #: their controls are not validated and submission is refused.
    submission_allowed: bool = True
    #: Sanitized last upstream failure. Never carries a URL with credentials.
    error: ErrorDetail | None = None


class VramInfo(Model):
    """Primary device memory from ``/system_stats``; ``None`` when not reported."""

    used_bytes: int | None = None
    total_bytes: int | None = None


class CatalogSnapshot(Model):
    """The sanitized, profile-safe catalog projection plus its freshness.

    ``nodes`` is the projection produced by catalog/normalize.py — shared input
    filenames and internal paths are already withheld. The raw catalog is never
    part of this model.
    """

    freshness: CatalogFreshness
    nodes: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class PackStatus(Model):
    """The optional SimpleUI node pack as seen by the last catalog refresh.

    ``missing``  - no ``SimpleUI*`` class types and no pack route.
    ``present``  - detected by its route with the supported contract, or by
                   its class types when the route is absent (version unknown).
    ``outdated`` - the route reports a contract this app does not support.
    """

    state: Literal["missing", "present", "outdated"]
    #: ``route`` when ``/simpleui/pack`` answered; ``class_types`` on fallback.
    detected_by: Literal["route", "class_types"] | None = None
    version: str | None = None
    contract: int | None = None
    supported_contract: int
    node_classes: list[str] = Field(default_factory=list)
    repo_url: str
    #: The catalog freshness state the status was derived from.
    catalog_state: CatalogState


class SelectionIssue(Model):
    """One saved control that a catalog change has put in doubt.

    An issue never carries a replacement value: the saved value stays exactly as
    imported and the user resolves it. ``uncertain`` still renders; ``blocking``
    stops submission.
    """

    node_id: str | None = None
    class_type: str
    input_name: str | None = None
    #: The value that is being preserved, encoded as it was stored.
    value: str | bool | float | None = None
    severity: Literal["uncertain", "blocking"]
    code: Literal[
        "class_missing",
        "choice_missing",
        "input_missing",
        "type_changed",
    ]
    message: str
