"""Authenticated, streamed private uploads staged for ComfyUI loader inputs.

Wiring (app/main.py)::

    uploads = UploadService(database, settings, config.data_dir)
    app.state.uploads = uploads
    app.include_router(uploads_router)   # BEFORE the /api/{path} catch-all
"""

from __future__ import annotations

from typing import Annotated

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..auth.routes import CurrentPrincipal, Mutation
from ..contracts import Model
from ..storage.db import in_thread
from .service import UploadError, UploadService

router = APIRouter(prefix="/api/uploads")

_STATUS = {
    "unsupported_kind": 415,
    "unsupported_type": 415,
    "invalid_image": 415,
    "too_large": 413,
    "input_dir_unavailable": 409,
    "unsupported_loader_adapter": 409,
    "upload_missing": 404,
}


class UploadInfo(Model):
    id: str
    media_kind: str
    media_type: str
    byte_size: str
    created_ms: str


class UploadPage(Model):
    items: list[UploadInfo]
    next_cursor: str | None = None


def _http_error(exc: UploadError) -> HTTPException:
    return HTTPException(_STATUS.get(exc.code, 400), str(exc))


def _public(service: UploadService, row: dict) -> UploadInfo:
    return UploadInfo(
        id=row["id"],
        media_kind=service.classify(row),
        media_type=row["media_type"],
        byte_size=row["byte_size"],
        created_ms=str(row["created_ms"]),
    )


@router.get("", response_model=UploadPage)
async def list_uploads(
    request: Request,
    principal: CurrentPrincipal,
    cursor: str | None = None,
    media_kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UploadPage:
    service: UploadService = request.app.state.uploads
    try:
        page = await in_thread(service.list_uploads, principal.owner_id, cursor, limit, media_kind)
    except ValueError as exc:  # a cursor the client edited or truncated
        raise HTTPException(400, "Malformed uploads cursor.") from exc
    return UploadPage(
        items=[_public(service, row) for row in page.items], next_cursor=page.next_cursor
    )


@router.post("", response_model=UploadInfo, status_code=201, dependencies=[Mutation])
async def create_upload(
    request: Request,
    principal: CurrentPrincipal,
    kind: str,
    filename: str,
) -> UploadInfo:
    """The body is the raw file, streamed -- not ``multipart/form-data``.

    ``kind`` and ``filename`` (used only to read an extension, never as a
    path -- see ``UploadService.validate``) are query parameters instead, so
    this needs no multipart parser: one dependency fewer, and the whole
    request body is one bounded read/write loop either way.
    """
    service: UploadService = request.app.state.uploads
    try:
        ext = await in_thread(service.validate, kind, filename)
    except UploadError as exc:
        raise _http_error(exc) from exc
    limit = await in_thread(service.max_bytes)
    upload_id, temp = await in_thread(service.temp_path, principal.owner_id, ext)

    size = 0
    try:
        with open(temp, "wb") as out:
            # Bounded chunked read straight off the ASGI receive channel: a
            # multi-GB video is never held whole in memory, on either the
            # read or the write side.
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    raise UploadError("too_large", f"Upload exceeds the {limit} byte limit.")
                await anyio.to_thread.run_sync(out.write, chunk)
    except UploadError as exc:
        await in_thread(lambda: temp.unlink(missing_ok=True))
        raise _http_error(exc) from exc
    except BaseException:
        await in_thread(lambda: temp.unlink(missing_ok=True))
        raise

    try:
        row = await in_thread(service.finalize, principal.owner_id, upload_id, ext, kind, temp, size)
    except UploadError as exc:
        raise _http_error(exc) from exc
    return _public(service, row)


@router.delete("/{upload_id}", status_code=204, dependencies=[Mutation])
async def delete_upload(request: Request, upload_id: str, principal: CurrentPrincipal) -> Response:
    service: UploadService = request.app.state.uploads
    removed = await in_thread(service.delete, principal.owner_id, upload_id)
    if not removed:
        raise HTTPException(404, "Upload was not found.")
    return Response(status_code=204)
