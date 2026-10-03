"""Cached ComfyUI node discovery.

    service = CatalogService(CatalogStore(db), ComfyClient(config.comfy_url))
    await service.startup()              # serves cache; one refresh attempt
    snapshot = await service.refresh()   # coalesced, cooldown-enforced

``CatalogSnapshot.nodes`` is the sanitized projection and is the only catalog
shape a profile may receive. The raw payload stays in storage.
"""

from __future__ import annotations

from .contracts import CatalogFreshness, CatalogSnapshot, CatalogState, SelectionIssue
from .normalize import NormalizedCatalog, NormalizeError, evaluate_selections, normalize
from .service import CatalogService
from .store import CatalogStore

__all__ = [
    "CatalogFreshness",
    "CatalogService",
    "CatalogSnapshot",
    "CatalogState",
    "CatalogStore",
    "NormalizeError",
    "NormalizedCatalog",
    "SelectionIssue",
    "evaluate_selections",
    "normalize",
]
