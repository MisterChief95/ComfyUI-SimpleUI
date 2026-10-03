"""Authenticated generation submission, listing, detail, and cancellation.

Wiring (app/main.py)::

    events = EventBroker()
    store = GenerationStore(database)
    generations = GenerationService(store, comfy, media=media, events=events, ...)
    app.state.generations = generations
    app.include_router(generations_router)   # BEFORE the /api/{path} catch-all

The submit route is the only place that resolves a graph for execution: it
fetches the caller's own stored workflow and control schema, then hands
``GenerationService.submit`` a pure closure that deep-copies the graph and
resolves any ``seed`` control's policy exactly once, inside the same SQLite
transaction that reserves the idempotency key (app/generations/store.py).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..comfy_client import ComfyUnavailable
from ..auth.routes import CurrentPrincipal, Mutation
from ..contracts import Id, Model, Page
from ..mapping import SeedPolicy, SubmissionError, build_submission_graph, resolve_seed
from ..mapping.input_adapters import InputAdapterError, bind_upload
from ..mapping.submission import DEFAULT_SEED_MAX
from ..storage.db import in_thread
from .service import (
    CancellationUnavailable,
    GenerationBusy,
    GenerationConflict,
    GenerationService,
)

router = APIRouter(prefix="/api/generations")


class SubmitRequest(Model):
    workflow_id: Id
    #: Client-generated idempotency key. A retry with the same key and the
    #: same edits returns the original generation instead of resubmitting.
    request_key: Id
    #: ``{binding_id: encoded value}`` for whatever the user actually edited;
    #: everything else is carried through from the stored graph untouched.
    #: Encoded like ``ControlDescriptor.value``: a JSON boolean for boolean
    #: controls, a number for floats, an ExactInt string for ints (a JSON
    #: integer is accepted too), a string for the rest. Smart-mode unions keep
    #: ``true`` a bool and ``5`` an int rather than coercing either to a string.
    edits: dict[str, str | bool | int | float] = {}
    seed_policy: SeedPolicy | None = None


class GenerationInfo(Model):
    id: Id
    workflow_id: Id | None = None
    status: str
    output_state: str
    error: dict[str, Any] | None = None
    created_ms: str
    updated_ms: str
    can_retry: bool = False


class GenerationDetail(GenerationInfo):
    effective_values: dict[str, Any] | None = None


class RecentPrompt(Model):
    text: str
    created_ms: str


def _error(row: dict[str, Any]) -> dict[str, Any] | None:
    value = row.get("error")
    if value is not None:
        return value
    raw = row.get("error_json")
    return json.loads(raw) if raw else None


def _public(row: dict[str, Any]) -> GenerationInfo:
    return GenerationInfo(
        id=row["id"],
        workflow_id=row.get("workflow_id"),
        status=row["status"],
        output_state=row["output_state"],
        error=_error(row),
        created_ms=str(row["created_ms"]),
        updated_ms=str(row["updated_ms"]),
        can_retry=_can_retry(row),
    )


def _can_retry(row: dict[str, Any]) -> bool:
    return row["status"] in ("succeeded", "failed", "cancelled", "interrupted") and bool(
        row.get("graph_json") or row.get("graph")
    )


def _detail(row: dict[str, Any]) -> GenerationDetail:
    return GenerationDetail(**_public(row).model_dump(), effective_values=row.get("effective_values"))


@router.post("", response_model=GenerationDetail, status_code=201, dependencies=[Mutation])
async def submit_generation(
    request: Request, principal: CurrentPrincipal, body: SubmitRequest
) -> GenerationDetail:
    state = request.app.state
    snapshot = await state.catalog.snapshot()
    schema = await in_thread(state.workflows.control_schema, principal.owner_id, body.workflow_id, snapshot)
    if schema is None:
        raise HTTPException(404, "Workflow was not found.")
    workflow = await in_thread(state.repository.get_workflow, principal.owner_id, body.workflow_id)
    revision = int(workflow["current_revision"])
    graph = await in_thread(state.repository.get_workflow_graph, principal.owner_id, body.workflow_id, revision)

    profile_settings = await in_thread(state.settings.profile, principal.owner_id)
    seed_policy: SeedPolicy = body.seed_policy or profile_settings["seed_policy"]

    # Staging an upload is real filesystem I/O, so it happens here -- before
    # the idempotency key is reserved and before resolve() runs inside the
    # store's short write transaction (app/storage/db.py: "Do no blocking I/O
    # inside this block"). ensure_staged is idempotent, so a retry restaging
    # the same upload is harmless.
    staged_edits: dict[str, Any] = dict(body.edits)
    for control in schema.controls:
        # Stored effective values keep the ControlDescriptor.value encoding.
        edit = staged_edits.get(control.binding_id)
        if control.logical_type == "int" and type(edit) is int:
            staged_edits[control.binding_id] = str(edit)
        elif control.logical_type == "boolean" and edit in ("true", "false"):
            staged_edits[control.binding_id] = edit == "true"
        if control.component != "file" or control.binding_id not in staged_edits:
            continue
        try:
            staged_edits[control.binding_id] = await in_thread(
                bind_upload, control, state.uploads, principal.owner_id, staged_edits[control.binding_id]
            )
        except InputAdapterError as exc:
            raise HTTPException(422, str(exc)) from exc

    generations: GenerationService = state.generations

    def resolve() -> tuple[dict[str, Any], dict[str, Any]]:
        edits = dict(staged_edits)
        for control in schema.controls:
            if control.component != "seed" or control.binding_id in edits:
                continue
            current = int(control.value) if control.value not in (None, "") else 0
            if seed_policy == "increment":
                # Advance from what this profile last submitted for this
                # binding (the imported value only seeds the first run).
                last = generations.store.last_effective_value(
                    principal.owner_id, body.workflow_id, control.binding_id
                )
                if last not in (None, ""):
                    current = int(last)
            maximum = int(control.constraints.exact_max) if control.constraints and control.constraints.exact_max else DEFAULT_SEED_MAX
            edits[control.binding_id] = str(resolve_seed(current, seed_policy, maximum=maximum))
        submission_graph = build_submission_graph(graph, schema, edits)
        return submission_graph, edits

    try:
        row = await generations.submit(
            principal.owner_id,
            request_key=body.request_key,
            request_payload={"workflow_id": body.workflow_id, "edits": body.edits, "seed_policy": seed_policy},
            resolve=resolve,
            workflow_id=body.workflow_id,
            workflow_revision=revision,
            mapping_revision=schema.revision,
            store_history=profile_settings["store_history"],
        )
    except GenerationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except GenerationBusy as exc:
        raise HTTPException(429, str(exc)) from exc
    except SubmissionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _detail(row)


@router.get("", response_model=Page[GenerationInfo])
async def list_generations(
    request: Request,
    principal: CurrentPrincipal,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    status: Literal["active"] | None = None,
) -> Page[GenerationInfo]:
    await _reconcile_before_read(request, principal.owner_id)
    try:
        page = await in_thread(
            request.app.state.repository.list_generations, principal.owner_id, cursor, limit, status == "active"
        )
    except ValueError as exc:  # a cursor the client edited or truncated
        raise HTTPException(400, "Malformed generations cursor.") from exc
    return Page[GenerationInfo](items=[_public(row) for row in page.items], next_cursor=page.next_cursor)


class RetryRequest(Model):
    request_key: Id


@router.post("/{generation_id}/retry", response_model=GenerationDetail, status_code=201, dependencies=[Mutation])
async def retry_generation(
    request: Request, generation_id: str, principal: CurrentPrincipal, body: RetryRequest
) -> GenerationDetail:
    state = request.app.state
    await _reconcile_before_read(request, principal.owner_id)
    source = await in_thread(state.generations.store.get, principal.owner_id, generation_id)
    if source is None:
        raise HTTPException(404, "Generation was not found.")
    if not _can_retry(source):
        raise HTTPException(409, "Retry requires a finished generation with a retained execution snapshot. Uncertain executions must be reconciled first.")
    settings = await in_thread(state.settings.profile, principal.owner_id)
    try:
        row = await state.generations.submit(
            principal.owner_id, request_key=body.request_key,
            request_payload={"retry_generation_id": generation_id},
            resolve=lambda: (source["graph"], source["effective_values"] or {}),
            workflow_id=source["workflow_id"], workflow_revision=source["workflow_revision"],
            mapping_revision=source["mapping_revision"], store_history=settings["store_history"],
        )
    except GenerationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except GenerationBusy as exc:
        raise HTTPException(429, str(exc)) from exc
    return _detail(row)


@router.get("/recent-prompts", response_model=Page[RecentPrompt])
async def recent_prompts(
    request: Request, principal: CurrentPrincipal,
    workflow_id: Annotated[str, Query(min_length=1, max_length=128)],
    binding_id: Annotated[str, Query(min_length=1, max_length=256)],
    q: Annotated[str, Query(max_length=500)] = "",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[RecentPrompt]:
    state = request.app.state
    snapshot = await state.catalog.snapshot()
    schema = await in_thread(state.workflows.control_schema, principal.owner_id, workflow_id, snapshot)
    if schema is None:
        raise HTTPException(404, "Workflow was not found.")
    if not any(c.binding_id == binding_id and c.logical_type == "string" for c in schema.controls):
        raise HTTPException(422, "Choose a string control from this workflow.")
    settings = await in_thread(state.settings.profile, principal.owner_id)
    if not settings["store_history"]:
        return Page[RecentPrompt](items=[])
    try:
        page = await in_thread(state.generations.store.recent_prompts,
                               principal.owner_id, workflow_id, binding_id, q, cursor, limit)
    except ValueError as exc:
        raise HTTPException(400, "Malformed prompt cursor.") from exc
    return Page[RecentPrompt](items=[RecentPrompt(text=row["text"], created_ms=str(row["created_ms"]))
                                   for row in page.items], next_cursor=page.next_cursor)


@router.get("/{generation_id}", response_model=GenerationDetail)
async def get_generation(
    request: Request, generation_id: str, principal: CurrentPrincipal
) -> GenerationDetail:
    await _reconcile_before_read(request, principal.owner_id)
    generations: GenerationService = request.app.state.generations
    row = await in_thread(generations.store.get, principal.owner_id, generation_id)
    if row is None:
        raise HTTPException(404, "Generation was not found.")
    return _detail(row)


@router.get("/{generation_id}/graph")
async def get_generation_graph(
    request: Request, generation_id: str, principal: CurrentPrincipal
) -> dict[str, Any]:
    """The exact ComfyUI API graph this generation submitted; gone once history is cleared."""
    generations: GenerationService = request.app.state.generations
    row = await in_thread(generations.store.get, principal.owner_id, generation_id)
    if row is None or not row.get("graph"):
        raise HTTPException(404, "The workflow JSON for this generation is not available.")
    return row["graph"]


async def _reconcile_before_read(request: Request, owner_id: str) -> None:
    """Give this request's own generations a fresh look before reading them.

    Cheap and cooldown-throttled (GenerationService.reconcile_if_due); other
    profiles' rows are reconciled too but with a safe default retention until
    one of their own requests supplies the real setting.
    """
    profile_settings = await in_thread(request.app.state.settings.profile, owner_id)
    generations: GenerationService = request.app.state.generations
    await generations.reconcile_if_due(history_retention={owner_id: profile_settings["store_history"]})


@router.post("/{generation_id}/cancel", status_code=204, dependencies=[Mutation])
async def cancel_generation(
    request: Request, generation_id: str, principal: CurrentPrincipal
) -> Response:
    generations: GenerationService = request.app.state.generations
    try:
        await generations.cancel(principal.owner_id, generation_id)
    except CancellationUnavailable as exc:
        # Deliberately the same error whether the id is foreign, unknown, or
        # merely in a state this ComfyUI cannot safely cancel: this never
        # confirms another profile's generation exists, and there is no
        # global cancel-all route anywhere in this router.
        raise HTTPException(409, str(exc)) from exc
    except ComfyUnavailable as exc:
        raise HTTPException(503, exc.message) from exc
    return Response(status_code=204)
