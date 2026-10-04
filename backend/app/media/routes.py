"""Authenticated gallery, stream, thumbnail, and local import endpoints."""

from __future__ import annotations

from pathlib import PurePosixPath
from sqlite3 import IntegrityError
from typing import Annotated, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import Field, StringConstraints, ValidationError

from ..auth.routes import CurrentPrincipal, LocalRequest, Mutation, guard_mutation
from ..auth.security import csrf_matches, request_origin_ok
from ..contracts import Model
from ..storage.db import in_thread
from .service import ZIP_MAX_ITEMS, MediaError

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
    sort: Literal["newest", "oldest", "random"] = "newest",
    path: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    media_kind: Literal["image", "video", "other"] | None = None,
    favorite: bool | None = None,
    workflow_id: str | None = None,
    collection_id: str | None = None,
    generation_id: str | None = None,
    created_after: Annotated[
        int | None, Query(ge=0, le=9_223_372_036_854_775_807)
    ] = None,
    created_before: Annotated[
        int | None, Query(ge=0, le=9_223_372_036_854_775_807)
    ] = None,
    prompt: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    search_field: Literal[
        "prompt", "model", "seed", "workflow", "filename", "any"
    ] = "any",
):
    try:
        return await in_thread(
            lambda: request.app.state.media.list_media(
                principal.owner_id,
                cursor,
                limit,
                sort=sort,
                path=path,
                media_kind=media_kind,
                favorite=favorite,
                workflow_id=workflow_id,
                collection_id=collection_id,
                generation_id=generation_id,
                created_after=created_after,
                created_before=created_before,
                prompt=prompt,
                search_field=search_field,
            )
        )
    except (ValueError, OverflowError) as exc:
        raise HTTPException(
            400, "Malformed gallery cursor or virtual folder path."
        ) from exc


@router.get("/suggestions")
async def filter_suggestions(
    request: Request,
    principal: CurrentPrincipal,
    q: Annotated[str, Query(max_length=500)] = "",
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
    search_field: Literal[
        "prompt", "model", "seed", "workflow", "filename", "any"
    ] = "any",
):
    return await in_thread(
        request.app.state.media.filter_suggestions,
        principal.owner_id,
        q,
        limit,
        search_field,
    )


@router.get("/duplicates")
async def duplicate_groups(
    request: Request, principal: CurrentPrincipal, media_id: str
):
    result = await in_thread(
        request.app.state.media.duplicate_groups, principal.owner_id, media_id
    )
    if result is None:
        raise _missing()
    return result


@router.get("/tree")
async def media_tree(
    request: Request,
    principal: CurrentPrincipal,
    path: Annotated[str, Query(max_length=200)] = "",
):
    try:
        return await in_thread(
            request.app.state.media.media_tree, principal.owner_id, path
        )
    except (ValueError, OverflowError) as exc:
        raise HTTPException(400, "Invalid virtual folder path.") from exc


@router.post("/clear-history", dependencies=[Mutation])
async def clear_history(request: Request, principal: CurrentPrincipal):
    return await in_thread(request.app.state.media.clear_history, principal.owner_id)


class DeleteRequest(Model):
    ids: Annotated[list[str], Field(min_length=1, max_length=500)]


class ZipRequest(Model):
    ids: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=100)]],
        Field(min_length=1, max_length=ZIP_MAX_ITEMS),
    ]


async def _zip_plan(request: Request, principal, body: ZipRequest):
    try:
        return await in_thread(
            request.app.state.media.zip_plan, principal.owner_id, body.ids
        )
    except MediaError as exc:
        raise HTTPException(413, str(exc)) from exc


@router.post("/download-zip/preview", dependencies=[Mutation])
async def preview_zip(request: Request, principal: CurrentPrincipal, body: ZipRequest):
    return await _zip_plan(request, principal, body)


@router.post("/download-zip")
async def download_zip(request: Request, principal: CurrentPrincipal):
    # Native forms cannot send a CSRF header. Accept the same per-session token
    # in a bounded form body, retaining the existing strict origin check.
    if not request_origin_ok(request):
        raise HTTPException(403, "Request origin is not this server.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise HTTPException(413, "ZIP request exceeds 64 KiB.")
    content_type = request.headers.get("content-type", "").split(";")[0]
    if content_type == "application/json":
        await guard_mutation(request)
        payload = bytes(raw)
    elif content_type == "application/x-www-form-urlencoded":
        try:
            form = parse_qs(raw.decode("utf-8"), max_num_fields=2)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, "Invalid ZIP form.") from exc
        if principal.session_token and not csrf_matches(
            request.app.state.session_secret,
            principal.session_token,
            form.get("csrf_token", [None])[0],
        ):
            raise HTTPException(403, "Missing or invalid CSRF token.")
        payload = form.get("selection", [""])[0]
    else:
        raise HTTPException(415, "Use JSON or a URL-encoded form.")
    try:
        body = ZipRequest.model_validate_json(payload)
    except ValidationError as exc:
        raise HTTPException(
            422, "Select 1–200 media IDs, each at most 100 characters."
        ) from exc
    plan = await _zip_plan(request, principal, body)
    return StreamingResponse(
        request.app.state.media.stream_zip(principal.owner_id, plan),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="selected-media.zip"',
            "Cache-Control": "private, no-store",
            "X-Media-Skipped": str(len(plan["skipped"])),
        },
    )


class CollectionName(Model):
    name: Annotated[str, Field(min_length=1, max_length=100)]


@router.get("/collections")
async def collections(request: Request, principal: CurrentPrincipal):
    return await in_thread(request.app.state.media.list_collections, principal.owner_id)


async def _save_collection(
    request: Request, principal, body: CollectionName, collection_id: str | None = None
):
    try:
        result = await in_thread(
            request.app.state.media.save_collection,
            principal.owner_id,
            body.name,
            collection_id,
        )
    except IntegrityError as exc:
        raise HTTPException(409, "A collection with that name already exists.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Collection was not found.")
    return result


@router.post("/collections", dependencies=[Mutation], status_code=201)
async def create_collection(
    request: Request, body: CollectionName, principal: CurrentPrincipal
):
    return await _save_collection(request, principal, body)


@router.put("/collections/{collection_id}", dependencies=[Mutation])
async def rename_collection(
    request: Request,
    collection_id: str,
    body: CollectionName,
    principal: CurrentPrincipal,
):
    return await _save_collection(request, principal, body, collection_id)


@router.delete("/collections/{collection_id}", dependencies=[Mutation])
async def delete_collection(
    request: Request, collection_id: str, principal: CurrentPrincipal
):
    if not await in_thread(
        request.app.state.media.delete_collection, principal.owner_id, collection_id
    ):
        raise HTTPException(404, "Collection was not found.")
    return {"ok": True}


@router.post("/collections/{collection_id}/media", dependencies=[Mutation])
@router.post("/collections/{collection_id}/remove", dependencies=[Mutation])
async def collection_members(
    request: Request,
    collection_id: str,
    body: DeleteRequest,
    principal: CurrentPrincipal,
):
    changed = await in_thread(
        lambda: request.app.state.media.collection_members(
            principal.owner_id,
            collection_id,
            body.ids,
            remove=request.url.path.endswith("/remove"),
        )
    )
    if changed is None:
        raise HTTPException(404, "Collection or media item was not found.")
    return {"changed": changed}


@router.post("/delete", dependencies=[Mutation])
async def delete_media(
    request: Request, body: DeleteRequest, principal: CurrentPrincipal
):
    deleted = await in_thread(
        request.app.state.media.delete_media, principal.owner_id, body.ids
    )
    return {"deleted": deleted}


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


@router.get("/{media_id}/provenance")
async def provenance(request: Request, media_id: str, principal: CurrentPrincipal):
    result = await in_thread(
        request.app.state.media.provenance, principal.owner_id, media_id
    )
    if result is None:
        raise _missing()
    return result


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
