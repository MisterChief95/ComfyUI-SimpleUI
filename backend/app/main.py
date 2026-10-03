"""FastAPI application: health, structured errors, and the built SPA.

Production layout: this process is the only server. It answers /api/* and
serves frontend/build, including browser deep links. Development layout: the
Vite dev server proxies /api to this process (see frontend/vite.config.ts).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from .auth.routes import router as auth_router
from .auth.security import load_or_create_secret
from .auth.service import AuthError, AuthService
from .catalog.routes import router as catalog_router
from .catalog.service import CatalogService
from .catalog.store import CatalogStore
from .comfy_client import ComfyClient
from .config import Config
from .contracts import ApiError, ErrorCode, ErrorDetail, ErrorEnvelope, Health
from .events import EventBroker
from .events.routes import router as events_router
from .generations.routes import router as generations_router
from .generations.listener import ComfyListener
from .generations.service import GenerationService, version_supports_targeted_interrupt
from .generations.store import GenerationStore
from .media import MediaService
from .media import router as media_router
from .settings.routes import router as settings_router
from .settings.service import SettingsStore
from .storage.db import Database, in_thread
from .storage.repository import Repository
from .uploads import UploadService
from .uploads import router as uploads_router
from .workflows.routes import router as workflows_router
from .workflows.service import WorkflowService

VERSION = "0.1.0"

_STATUS_CODES: dict[int, ErrorCode] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "unprocessable",
    429: "rate_limited",
    502: "upstream_unavailable",
    503: "upstream_unavailable",
}


def error_response(
    status: int, message: str, request_id: str, details: list[ErrorDetail] | None = None
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ApiError(
            code=_STATUS_CODES.get(status, "internal"),
            message=message,
            request_id=request_id,
            details=details or [],
        )
    )
    return JSONResponse(status_code=status, content=envelope.model_dump())


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    database = Database(config.db_path)
    settings = SettingsStore(database)
    auth = AuthService(database, settings)
    # Saved host configuration takes effect on restart; the environment supplies
    # the initial URL when the host has not saved an override.
    saved_url = database.query_one("SELECT value_json FROM settings WHERE scope = 'host' AND key = 'comfy_url'")
    comfy = ComfyClient(settings.host_value("comfy_url") if saved_url else config.comfy_url)
    catalog = CatalogService(
        CatalogStore(database), comfy,
        cooldown_s=max(30, settings.host_value("catalog_refresh_min_seconds")),
    )
    repository = Repository(database)
    workflows = WorkflowService(repository)
    media = MediaService(database, settings, config.data_dir)
    uploads = UploadService(database, settings, config.data_dir)
    # The real baseline gate, replacing the placeholder in auth/service.py:
    # multi-user mode cannot be enabled over an un-indexed output folder.
    auth.baseline_gate = media.baseline_status
    generation_events = EventBroker()
    async def targeted_interrupt_supported() -> bool:
        installation = (await catalog.snapshot()).capabilities.get("installation") or {}
        return version_supports_targeted_interrupt(installation.get("comfyui_version"))

    generations = GenerationService(
        GenerationStore(database), comfy, media=media, events=generation_events,
        global_pending_cap=settings.host_value("pending_cap"),
        profile_pending_cap=settings.host_value("pending_cap"),
        # /interrupt {"prompt_id"} is only targeted on ComfyUI >= 0.38.0 (docs/API.md);
        # gated on the catalog's recorded version, read at cancel time.
        targeted_interrupt_supported=targeted_interrupt_supported,
    )

    def retention() -> dict[str, bool]:
        return {
            profile["id"]: bool(settings.profile(profile["id"])["store_history"])
            for profile in auth.list_profiles()
        }

    async def resync() -> None:
        """Recover terminal states missed while the event socket was down."""
        await generations.reconcile(history_retention=await in_thread(retention))

    listener = ComfyListener(
        generations, comfy,
        previews_enabled=lambda owner_id: bool(settings.profile(owner_id)["live_previews"]),
        on_connect=resync,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        startup = asyncio.create_task(catalog.startup())
        # Restart/disconnect recovery: pick up wherever ComfyUI's own queue and
        # history say each still-active generation actually is. Best-effort --
        # an unreachable ComfyUI must not block the app from serving.
        reconciled = asyncio.create_task(
            generations.reconcile_if_due(history_retention=retention())
        )
        listener.start()
        try:
            yield
        finally:
            await listener.stop()
            startup.cancel()
            reconciled.cancel()
            await asyncio.gather(startup, reconciled, return_exceptions=True)
            await catalog.aclose()
            await comfy.aclose()
            database.close()

    app = FastAPI(title="ComfyUI SimpleUI", version=VERSION, lifespan=lifespan)
    app.state.config = config
    app.state.db = database
    app.state.settings = settings
    app.state.auth = auth
    app.state.catalog = catalog
    app.state.repository = repository
    app.state.workflows = workflows
    app.state.media = media
    app.state.uploads = uploads
    app.state.generations = generations
    app.state.generation_events = generation_events
    # Kept in a file rather than the settings tables so no API response can
    # ever contain it.
    app.state.session_secret = load_or_create_secret(config.session_secret_path)

    @app.middleware("http")
    async def tag_request(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            response.headers["cache-control"] = "private, no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(exc.status_code, str(exc.detail), _request_id(request))

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(p) for p in err["loc"][1:]) or None,
                code=err["type"],
                message=err["msg"],
            )
            for err in exc.errors()
        ]
        return error_response(
            422, "Request validation failed", _request_id(request), details
        )

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, exc: AuthError) -> JSONResponse:
        return error_response(exc.status, str(exc), _request_id(request))

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Message stays generic; the request id correlates with the server log.
        return error_response(500, "Internal server error", _request_id(request))

    @app.get("/api/health", response_model=Health)
    async def health() -> Health:
        return Health(status="ok", version=VERSION, time_ms=str(time.time_ns() // 1_000_000))

    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(catalog_router)
    app.include_router(workflows_router)
    app.include_router(media_router)
    app.include_router(uploads_router)
    app.include_router(generations_router)
    app.include_router(events_router)
    # Everything above is registered before the catch-all on purpose: FastAPI
    # matches in registration order, so a router added after it never runs.

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
    async def unknown_api(request: Request, path: str) -> JSONResponse:
        # An unmatched API route is always a 404, never the SPA shell.
        return error_response(404, f"No API route /api/{path}", _request_id(request))

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def spa(request: Request, path: str) -> Response:
        if config.dev_mode:
            return error_response(
                404, "Static files disabled (SIMPLEUI_DEV)", _request_id(request)
            )
        target = _resolve_static(config.static_dir, path)
        if target is not None:
            return FileResponse(target)
        # Deep link: only extension-less paths fall back to the SPA shell, so a
        # missing asset stays a real 404 instead of returning HTML.
        if Path(path).suffix:
            return error_response(404, f"No such file /{path}", _request_id(request))
        return FileResponse(config.spa_index)

    return app


def _resolve_static(static_dir: Path, path: str) -> Path | None:
    """Resolve a URL path inside static_dir, or None if it is not a file there."""
    if not path:
        return static_dir / "index.html"
    try:
        target = (static_dir / path).resolve()
    except OSError:
        return None
    if not target.is_relative_to(static_dir) or not target.is_file():
        return None
    return target
