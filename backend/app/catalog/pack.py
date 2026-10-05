"""Detection of the optional SimpleUI node pack (comfyui-simpleui-nodes).

The app works identically without the pack. Presence is derived from two
signals, both captured during a successful catalog refresh so that reading the
status never calls ComfyUI:

* ``SimpleUI*`` class types in the cached catalog (always available);
* the pack's own ``GET /simpleui/pack`` route, which adds ``version`` and the
  integer ``contract``. An older pack or a failed probe simply has no route
  record, and detection falls back to the class types.

A ``contract`` other than :data:`SUPPORTED_CONTRACT` is reported as
``outdated``, never guessed at.
"""

from __future__ import annotations

from typing import Any

from .contracts import PackStatus

PACK_NAME = "comfyui-simpleui-nodes"
PACK_REPO_URL = "https://github.com/MisterChief95/ComfyUI-SimpleUI-Nodes"
PACK_ROUTE = "/simpleui/pack"
CLASS_PREFIX = "SimpleUI"
SUPPORTED_CONTRACT = 2

_MAX_VERSION = 40


def probe_record(body: Any) -> dict[str, Any] | None:
    """Named fields from a ``/simpleui/pack`` reply, or None if it is not the pack's.

    Only ``version`` and ``contract`` are kept; nothing else from the body is
    stored or served.
    """
    if not isinstance(body, dict) or body.get("pack") != PACK_NAME:
        return None
    version = body.get("version")
    contract = body.get("contract")
    return {
        "version": version[:_MAX_VERSION]
        if isinstance(version, str) and version
        else None,
        "contract": contract
        if isinstance(contract, int) and not isinstance(contract, bool)
        else None,
    }


def pack_status(
    nodes: dict[str, Any], probe: dict[str, Any] | None, *, catalog_state: str
) -> PackStatus:
    """Combine the cached catalog and the cached probe into one status."""
    classes = sorted(c for c in nodes if c.startswith(CLASS_PREFIX))
    common = {
        "node_classes": classes,
        "supported_contract": SUPPORTED_CONTRACT,
        "repo_url": PACK_REPO_URL,
        "catalog_state": catalog_state,
    }
    if probe is not None:
        contract = probe.get("contract")
        version = probe.get("version")
        if contract != SUPPORTED_CONTRACT:
            return PackStatus(
                state="outdated",
                detected_by="route",
                version=version,
                contract=contract,
                **common,
            )
        return PackStatus(
            state="present",
            detected_by="route",
            version=version,
            contract=contract,
            **common,
        )
    if classes:
        return PackStatus(state="present", detected_by="class_types", **common)
    return PackStatus(state="missing", **common)
