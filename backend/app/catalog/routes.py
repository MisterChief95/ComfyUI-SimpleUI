"""Authenticated catalog projection; raw upstream metadata stays server-side."""

from fastapi import APIRouter, HTTPException, Request, Response

from ..auth.routes import CurrentPrincipal, Mutation
from ..comfy_client import ComfyUnavailable
from .contracts import CatalogSnapshot, PackStatus, VramInfo

router = APIRouter(prefix="/api/catalog")


@router.get("", response_model=CatalogSnapshot)
async def read_catalog(
    request: Request, principal: CurrentPrincipal
) -> CatalogSnapshot:
    return await request.app.state.catalog.snapshot()


@router.post("/refresh", response_model=CatalogSnapshot, dependencies=[Mutation])
async def refresh_catalog(
    request: Request, principal: CurrentPrincipal
) -> CatalogSnapshot:
    # Even a local browser uses the global cooldown; no client can force it.
    return await request.app.state.catalog.refresh()


@router.get("/pack", response_model=PackStatus)
async def read_pack_status(request: Request, principal: CurrentPrincipal) -> PackStatus:
    # Derived from the cached catalog; reading it never calls ComfyUI.
    return await request.app.state.catalog.pack_status()


@router.get("/completions")
async def read_completions(
    request: Request, principal: CurrentPrincipal
) -> dict[str, list[str]]:
    return await request.app.state.catalog.completions()


@router.get("/models/{folder}/info")
async def read_model_info(
    request: Request, folder: str, filename: str, principal: CurrentPrincipal
) -> dict:
    try:
        return await request.app.state.catalog.model_info(folder, filename)
    except KeyError:
        raise HTTPException(404, "Unknown model.") from None


@router.get("/models/{folder}/preview")
async def read_model_preview(
    request: Request, folder: str, filename: str, principal: CurrentPrincipal
) -> Response:
    try:
        body, kind = await request.app.state.catalog.model_preview(folder, filename)
    except (KeyError, ComfyUnavailable):
        raise HTTPException(404, "No preview available.") from None
    return Response(
        body, media_type=kind, headers={"cache-control": "private, max-age=3600"}
    )


@router.get("/vram", response_model=VramInfo)
async def read_vram(request: Request, principal: CurrentPrincipal) -> dict:
    try:
        return await request.app.state.catalog.vram()
    except ComfyUnavailable as exc:
        raise HTTPException(503, exc.message) from exc


@router.post("/free", status_code=204, dependencies=[Mutation])
async def free_memory(request: Request, principal: CurrentPrincipal) -> Response:
    # Affects the shared ComfyUI for everyone; nothing here is profile-specific.
    try:
        await request.app.state.catalog.free_memory()
    except ComfyUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    return Response(status_code=204)
