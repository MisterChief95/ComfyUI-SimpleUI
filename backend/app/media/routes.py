"""Authenticated gallery, stream, thumbnail, and local import endpoints."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..auth.routes import CurrentPrincipal, LocalRequest, Mutation
from ..contracts import Model
from ..storage.db import in_thread
from .service import MediaError

router = APIRouter(prefix="/api/media")


class Flags(Model):
    hidden: bool | None = None
    favorite: bool | None = None


def _missing() -> HTTPException:
    return HTTPException(404, "Media item was not found.")


@router.get("")
async def gallery(
    request: Request,
    principal: CurrentPrincipal,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    media_kind: Literal["image", "video", "other"] | None = None,
    favorite: bool | None = None,
    workflow_id: str | None = None,
    generation_id: str | None = None,
    created_after: Annotated[
        int | None, Query(ge=0, le=9_223_372_036_854_775_807)
    ] = None,
    created_before: Annotated[
        int | None, Query(ge=0, le=9_223_372_036_854_775_807)
    ] = None,
    prompt: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
):
    try:
        return await in_thread(
            lambda: request.app.state.media.list_media(
                principal.owner_id,
                cursor,
                limit,
                media_kind=media_kind,
                favorite=favorite,
                workflow_id=workflow_id,
                generation_id=generation_id,
                created_after=created_after,
                created_before=created_before,
                prompt=prompt,
            )
        )
    except ValueError as exc:  # a cursor the client edited or truncated
        raise HTTPException(400, "Malformed gallery cursor.") from exc


@router.post("/clear-history", dependencies=[Mutation])
async def clear_history(request: Request, principal: CurrentPrincipal):
    return await in_thread(request.app.state.media.clear_history, principal.owner_id)


@router.post("/import", dependencies=[Mutation, LocalRequest])
async def import_media(
    request: Request, principal: CurrentPrincipal, explicit: bool = False
):
    try:
        return await in_thread(
            lambda: request.app.state.media.import_baseline(explicit=explicit)
        )
    except MediaError as exc:
        raise HTTPException(409, str(exc)) from exc


# HEAD is listed explicitly: FastAPI's APIRoute, unlike Starlette's Route, does
# not add it to a GET route, and a video player HEADs before it seeks.
@router.api_route("/{media_id}/file", methods=["GET", "HEAD"])
async def stream(request: Request, media_id: str, principal: CurrentPrincipal):
    located = await in_thread(
        request.app.state.media.locate, principal.owner_id, media_id
    )
    if located is None:
        raise _missing()
    # Starlette's FileResponse does Content-Length, HEAD, Range and 416 itself.
    return FileResponse(located.path, media_type=located.row["media_type"])


@router.api_route("/{media_id}/download", methods=["GET", "HEAD"])
async def download(request: Request, media_id: str, principal: CurrentPrincipal):
    located = await in_thread(
        request.app.state.media.locate, principal.owner_id, media_id
    )
    if located is None:
        raise _missing()
    # The original file, under its original name: no transcoding, ever.
    return FileResponse(
        located.path,
        media_type=located.row["media_type"],
        filename=PurePosixPath(located.row["storage_path"]).name,
    )


@router.get("/{media_id}/thumbnail")
async def thumbnail(request: Request, media_id: str, principal: CurrentPrincipal):
    path = await in_thread(
        request.app.state.media.thumbnail, principal.owner_id, media_id
    )
    if path is None:
        raise _missing()
    return FileResponse(path, media_type="image/jpeg")


@router.put("/{media_id}", dependencies=[Mutation])
async def update_flags(
    request: Request, media_id: str, flags: Flags, principal: CurrentPrincipal
):
    changed = await in_thread(
        lambda: request.app.state.media.set_flags(
            principal.owner_id, media_id, hidden=flags.hidden, favorite=flags.favorite
        )
    )
    if not changed:
        raise _missing()
    return {"ok": True}
