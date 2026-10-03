"""One reusable async HTTP client for ComfyUI.

Every upstream call in this application goes through here, so the rules below
hold in one place instead of at each call site:

* **Credentials never escape.** The configured URL may carry userinfo
  (``http://user:pass@host:8188``) and the operator may configure headers for a
  reverse proxy. Neither is ever put in an exception message, a stored error
  record, or a log line: :func:`redact_url` strips userinfo and headers are
  never rendered at all. Upstream response bodies are not echoed either — a
  body can contain host paths.
* **Failures are one exception type** (:class:`ComfyUnavailable`) carrying a
  machine-readable code and a message already safe to show a user.
* **Retries are bounded.** Transient transport errors and 5xx get a few quick
  attempts with exponential backoff. The longer "keep trying while ComfyUI is
  down" schedule is the caller's cooldown, not a loop in here (see
  app/catalog/service.py).

Only GET is needed today (``/object_info``, ``/system_stats``). GEN-001 adds
POST on top of this same client rather than opening a second one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Self
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

LOGGER = logging.getLogger("simpleui.comfy")

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 0.25


def redact_url(url: str) -> str:
    """Return ``url`` with any embedded username/password removed.

    ``http://user:secret@127.0.0.1:8188/object_info`` becomes
    ``http://127.0.0.1:8188/object_info``. Used for every message and log line.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<malformed url>"
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class ComfyUnavailable(Exception):
    """ComfyUI could not be reached, or answered unusably.

    ``message`` is already sanitized: safe for an API response and a log.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        """The shape stored in ``catalogs.error_json`` and returned to clients."""
        return {"code": self.code, "message": self.message}


class ComfyClient:
    """Async ComfyUI HTTP client. One instance per process; close it at shutdown."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_s: float = DEFAULT_BACKOFF_S,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        #: The only form of the URL that may be shown or logged.
        self.safe_url = redact_url(self.base_url)
        self.headers = dict(headers or {})
        self._attempts = max(1, attempts)
        self._backoff_s = backoff_s
        # httpx keeps userinfo in the URL for the Authorization header it
        # derives; `headers` stays inside the client and is never rendered.
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_s,
            headers=headers or {},
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def ws_url(self, client_id: str) -> str:
        """The event-socket URL for ``client_id`` (http->ws, https->wss).

        May carry userinfo like the base URL; never log it, use ``safe_url``.
        """
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        path = parts.path.rstrip("/") + "/ws"
        return urlunsplit(
            (scheme, parts.netloc, path, urlencode({"clientId": client_id}), "")
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        """POST ``path`` once. Never retried.

        A retry here could re-execute a side effect ComfyUI already received;
        GenerationService owns the at-most-once decision (``submission_unknown``
        on any exception), so this makes exactly one attempt.
        """
        safe_path = redact_url(path) if "//" in path else path
        try:
            response = await self._client.post(path, json=payload)
        except httpx.TimeoutException:
            raise ComfyUnavailable(
                "upstream_timeout",
                f"ComfyUI did not respond within the timeout ({self.safe_url})",
            ) from None
        except httpx.HTTPError:
            raise ComfyUnavailable(
                "upstream_unreachable", f"ComfyUI is not reachable at {self.safe_url}"
            ) from None
        if response.status_code >= 400:
            raise ComfyUnavailable(
                "upstream_error",
                f"ComfyUI returned HTTP {response.status_code} for {safe_path}",
            )
        return response

    async def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """POST ``path`` once and parse the JSON reply."""
        response = await self._post(path, payload)
        try:
            return response.json()
        except ValueError:
            safe_path = redact_url(path) if "//" in path else path
            raise ComfyUnavailable(
                "upstream_malformed",
                f"ComfyUI returned a non-JSON response for {safe_path}",
            ) from None

    async def submit_prompt(
        self,
        graph: dict[str, Any],
        *,
        client_id: str,
        extra_data: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"prompt": graph, "client_id": client_id}
        if extra_data:
            payload["extra_data"] = extra_data
        return await self.post_json("/prompt", payload)

    async def get_queue(self) -> Any:
        return await self.get_json("/queue")

    async def get_history(self) -> Any:
        # ponytail: fetches the whole history every reconcile tick; add
        # ?max_items or a since-cursor if a long-running install makes this
        # slow.
        return await self.get_json("/history")

    async def cancel_pending(self, prompt_id: str) -> None:
        """Remove a not-yet-started prompt from ComfyUI's own queue."""
        # ComfyUI answers /queue and /interrupt with an empty 200: no JSON.
        await self._post("/queue", {"delete": [prompt_id]})

    async def cancel_job(self, prompt_id: str) -> None:
        """Interrupt ``prompt_id`` only if it is the running prompt.

        ComfyUI ignores a targeted interrupt for any other prompt, so this can
        never stop someone else's job. (Without ``prompt_id`` it would be a
        global interrupt; that form is never sent.)
        """
        await self._post("/interrupt", {"prompt_id": prompt_id})

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def get_json(self, path: str) -> Any:
        """GET ``path`` and parse JSON, retrying transient failures.

        Raises :class:`ComfyUnavailable` with a sanitized message; the caller
        decides whether that degrades a cached catalog or fails a request.
        """
        safe_path = redact_url(path) if "//" in path else path
        last: ComfyUnavailable | None = None
        for attempt in range(self._attempts):
            try:
                response = await self._client.get(path)
            except httpx.TimeoutException:
                last = ComfyUnavailable(
                    "upstream_timeout",
                    f"ComfyUI did not respond within the timeout ({self.safe_url})",
                )
            except httpx.HTTPError:
                # Never interpolate the exception: httpx puts the full request
                # URL, userinfo included, in its message.
                last = ComfyUnavailable(
                    "upstream_unreachable",
                    f"ComfyUI is not reachable at {self.safe_url}",
                )
            else:
                if response.status_code >= 500:
                    last = ComfyUnavailable(
                        "upstream_error",
                        f"ComfyUI returned HTTP {response.status_code} for {safe_path}",
                    )
                elif response.status_code >= 400:
                    # A 4xx is a contract problem, not a transient one: no retry.
                    raise ComfyUnavailable(
                        "upstream_error",
                        f"ComfyUI returned HTTP {response.status_code} for {safe_path}",
                    )
                else:
                    try:
                        return response.json()
                    except ValueError:
                        # Body is deliberately not included; it may carry paths.
                        raise ComfyUnavailable(
                            "upstream_malformed",
                            f"ComfyUI returned a non-JSON response for {safe_path}",
                        ) from None
            LOGGER.warning(
                "ComfyUI request failed (attempt %d/%d): %s",
                attempt + 1,
                self._attempts,
                last.message,
            )
            if attempt + 1 < self._attempts and self._backoff_s:
                await asyncio.sleep(self._backoff_s * (2**attempt))
        assert last is not None
        raise last
