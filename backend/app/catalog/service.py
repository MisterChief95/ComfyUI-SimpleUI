"""Catalog lifecycle: cached startup, coalesced refresh, cooldown, freshness.

The behaviours this exists to guarantee (docs/WORKFLOW_MAPPING.md "Catalog
lifecycle"):

* **Cold start does not need ComfyUI.** ``startup()`` serves the last good
  catalog from storage immediately and then attempts one refresh. If that
  fails, the cached catalog is still served, marked stale.
* **Simultaneous refreshes produce exactly one upstream fetch.** Concurrent
  callers await the same in-flight task.
* **The cooldown is server-enforced.** Reloading the page cannot bypass it.
  Consecutive failures back off exponentially to a ceiling, so a ComfyUI that
  is down is not hammered.
* **A failed refresh never blanks the catalog.** Only a successfully normalized
  payload replaces the snapshot.
* **No catalog is an explicit state.** First run with no cache and no ComfyUI
  reports ``unavailable`` with the upstream error, not an empty catalog.

ponytail: the in-flight task and cooldown clock live in this process's memory,
which is correct because one backend process owns catalog refresh
(docs/ARCHITECTURE.md). A second backend worker would need the cooldown moved
into the database.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..comfy_client import ComfyClient, ComfyUnavailable
from ..contracts import ErrorDetail
from ..storage.db import in_thread
from ..storage.repository import now_ms
from .contracts import CatalogFreshness, CatalogSnapshot
from .normalize import NormalizeError, normalize
from .store import CatalogStore

LOGGER = logging.getLogger("simpleui.catalog")

#: Minimum seconds between upstream fetches, globally.
DEFAULT_COOLDOWN_S = 30.0
#: Ceiling for the failure backoff.
DEFAULT_MAX_BACKOFF_S = 300.0


class CatalogService:
    """Owns the cached catalog for the process. Create one; share it."""

    def __init__(
        self,
        store: CatalogStore,
        client: ComfyClient,
        *,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
    ) -> None:
        self._store = store
        self._client = client
        self._cooldown_s = cooldown_s
        self._max_backoff_s = max_backoff_s
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[CatalogSnapshot] | None = None
        self._cache: dict[str, Any] | None = None
        self._loaded = False
        self._failures = 0
        self._last_attempt_ms: int | None = None
        self._next_allowed_ms: int | None = None

    # --- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        """Stop a shielded refresh before closing its HTTP client/database."""
        if self._inflight is not None:
            self._inflight.cancel()
            await asyncio.gather(self._inflight, return_exceptions=True)

    async def startup(self) -> CatalogSnapshot:
        """Load the cached catalog, then attempt exactly one refresh.

        Returns the snapshot after that attempt. Never raises because ComfyUI
        is down: the application must start and serve regardless.
        """
        await self._load_cache()
        return await self.refresh(force=True)

    async def _load_cache(self) -> None:
        self._cache = await in_thread(self._store.load)
        self._loaded = True
        if self._cache and self._cache.get("error") and self._cache.get("normalized"):
            # A previous run ended on a failed attempt: still stale, not fresh.
            self._failures = max(self._failures, 1)

    async def snapshot(self) -> CatalogSnapshot:
        """The current sanitized snapshot. No upstream call, no cooldown effect."""
        if not self._loaded:
            await self._load_cache()
        return self._build_snapshot()

    async def raw_catalog(self) -> Any | None:
        """Server-side only: the raw upstream payload, for diagnostics.

        Never return this from a profile-scoped route — it contains shared
        input filenames and host paths.
        """
        return await in_thread(self._store.load_raw)

    # --- refresh ----------------------------------------------------------

    async def refresh(self, *, force: bool = False) -> CatalogSnapshot:
        """Refresh the catalog, coalescing concurrent callers into one fetch.

        ``force`` skips the cooldown (startup, and an explicit local operation);
        it does not skip coalescing. Upstream failure is reported in the
        returned snapshot, not raised.
        """
        if not self._loaded:
            await self._load_cache()

        async with self._lock:
            inflight = self._inflight
            if inflight is not None and not inflight.done():
                task = inflight          # join the fetch already in progress
            elif not force and self._cooldown_remaining_ms() > 0:
                return self._build_snapshot(cooldown_active=True)
            else:
                task = self._inflight = asyncio.create_task(self._fetch_and_store())

        # shield: one caller giving up (client disconnect) must not cancel the
        # shared fetch that the other waiters are relying on.
        return await asyncio.shield(task)

    def _cooldown_remaining_ms(self) -> int:
        if self._next_allowed_ms is None:
            return 0
        return max(0, self._next_allowed_ms - now_ms())

    def _schedule_next_attempt(self) -> None:
        if self._failures == 0:
            delay = self._cooldown_s
        else:
            # Bounded exponential backoff while ComfyUI is unreachable.
            delay = min(self._cooldown_s * (2 ** (self._failures - 1)), self._max_backoff_s)
        self._next_allowed_ms = now_ms() + int(delay * 1000)

    async def _fetch_and_store(self) -> CatalogSnapshot:
        """One upstream attempt. Converts every failure into a stored state."""
        self._last_attempt_ms = now_ms()
        try:
            raw = await self._client.get_json("/object_info")
            normalized = normalize(raw)
        except ComfyUnavailable as exc:
            return await self._record_failure(exc.as_detail())
        except NormalizeError as exc:
            # A malformed payload must not replace a good snapshot either.
            return await self._record_failure(
                {"code": "upstream_malformed", "message": str(exc)}
            )
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Unexpected catalog refresh failure")
            return await self._record_failure(
                {"code": "internal", "message": "Catalog refresh failed unexpectedly."}
            )

        capabilities = await self._capability_record(normalized.node_packs)
        document = {
            "nodes": normalized.nodes,
            "withheld": normalized.withheld,
            "schema_hash": normalized.schema_hash,
            "content_hash": normalized.content_hash,
            "catalog_revision": normalized.revision,
            "node_packs": normalized.node_packs,
            "capabilities": capabilities,
        }
        await in_thread(
            _save,
            self._store,
            self._client.safe_url,
            raw,
            document,
            normalized.content_hash,
        )
        self._failures = 0
        self._schedule_next_attempt()
        await self._load_cache()
        LOGGER.info("Catalog refreshed: %d classes, revision %s", len(normalized.nodes), normalized.revision)
        return self._build_snapshot()

    async def _record_failure(self, error: dict[str, Any]) -> CatalogSnapshot:
        self._failures += 1
        self._schedule_next_attempt()
        await in_thread(_record, self._store, self._client.safe_url, error)
        # Reload so the stored state ('stale' with data, 'error' without) is
        # what we report; the cached catalog itself is untouched.
        await self._load_cache()
        LOGGER.warning("Catalog refresh failed (%s): %s", error["code"], error["message"])
        return self._build_snapshot()

    # --- capability record ------------------------------------------------

    async def _capability_record(self, node_packs: list[str]) -> dict[str, Any]:
        """Fill the installation block described in docs/COMPATIBILITY.md.

        ``/system_stats`` is best effort: the catalog is usable without it, so a
        failure here degrades the record rather than the refresh. Only named
        fields are copied — never the whole payload, which can carry host paths
        and command lines.
        """
        record: dict[str, Any] = {
            "provenance": "live",
            "captured_at_ms": str(now_ms()),
            "installation": {
                "comfyui_version": None,
                "comfyui_revision": None,
                "python_version": None,
                "torch_version": None,
                "device": None,
                "node_packs": node_packs,
            },
            "routes": {"object_info": "ok", "system_stats": "unavailable"},
        }
        try:
            stats = await self._client.get_json("/system_stats")
        except ComfyUnavailable as exc:
            record["system_stats_error"] = exc.as_detail()
            return record
        if not isinstance(stats, dict):
            return record

        record["routes"]["system_stats"] = "ok"
        system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
        installation = record["installation"]
        installation["comfyui_version"] = _text(system.get("comfyui_version"))
        installation["comfyui_revision"] = _text(system.get("comfyui_revision"))
        # Version strings can carry a build path; keep the version token only.
        python_version = _text(system.get("python_version"))
        installation["python_version"] = python_version.split()[0] if python_version else None
        installation["torch_version"] = _text(system.get("pytorch_version") or system.get("torch_version"))

        devices = stats.get("devices")
        if isinstance(devices, list) and devices and isinstance(devices[0], dict):
            installation["device"] = {
                "name": _text(devices[0].get("name")),
                "type": _text(devices[0].get("type")),
            }
        return record

    # --- projection -------------------------------------------------------

    def _build_snapshot(self, *, cooldown_active: bool = False) -> CatalogSnapshot:
        cache = self._cache or {}
        document = cache.get("normalized") or {}
        nodes = document.get("nodes") or {}
        stored_error = cache.get("error")

        if not nodes:
            state = "unavailable"
        elif stored_error or self._failures:
            state = "stale"
        else:
            state = "fresh"

        error: ErrorDetail | None = None
        if stored_error:
            error = ErrorDetail(
                field=None,
                code=str(stored_error.get("code", "upstream_unavailable")),
                message=str(stored_error.get("message", "ComfyUI is unreachable.")),
            )
        elif state == "unavailable":
            error = ErrorDetail(
                field=None,
                code="catalog_unavailable",
                message=(
                    "No node catalog has been fetched yet and ComfyUI has not been "
                    "reached. Workflows can be imported and stored, but their controls "
                    "cannot be validated and generation is unavailable."
                ),
            )

        fetched_ms = cache.get("fetched_ms") or 0
        remaining = self._cooldown_remaining_ms()
        return CatalogSnapshot(
            freshness=CatalogFreshness(
                state=state,
                catalog_revision=document.get("catalog_revision"),
                schema_hash=document.get("schema_hash"),
                fetched_ms=str(fetched_ms) if fetched_ms else None,
                checked_ms=str(self._last_attempt_ms) if self._last_attempt_ms else None,
                next_refresh_allowed_ms=(
                    str(self._next_allowed_ms) if self._next_allowed_ms is not None else None
                ),
                cooldown_active=cooldown_active or remaining > 0,
                consecutive_failures=self._failures,
                submission_allowed=state != "unavailable",
                error=error,
            ),
            nodes=nodes,
            capabilities=document.get("capabilities") or {},
        )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


# Module-level so in_thread() gets a plain callable with positional arguments.


def _save(
    store: CatalogStore, source_url: str, raw: Any, document: dict[str, Any], content_hash: str
) -> None:
    store.save_success(
        source_url=source_url, raw=raw, normalized=document, content_hash=content_hash
    )


def _record(store: CatalogStore, source_url: str, error: dict[str, Any]) -> None:
    store.record_failure(source_url=source_url, error=error)
