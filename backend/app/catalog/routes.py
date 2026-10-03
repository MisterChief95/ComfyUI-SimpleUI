"""Authenticated catalog projection; raw upstream metadata stays server-side."""

from fastapi import APIRouter, Request

from ..auth.routes import CurrentPrincipal, Mutation
from .contracts import CatalogSnapshot

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
