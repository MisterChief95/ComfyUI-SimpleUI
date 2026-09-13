"""Authenticated workflow import, listing, and control-schema projection.

The import body is the ComfyUI API JSON *as sent*: it is handed to
``parse_graph`` as raw bytes rather than being decoded by Pydantic first, so the
importer's own rules (size limit, duplicate object keys, non-finite constants,
exact large integers) apply to the real file instead of to a re-serialized copy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ..auth.routes import CurrentPrincipal, Mutation
from ..contracts import ControlSchema, Id, Model, Page
from ..mapping import GraphImportError
from ..mapping.corrections import Correction, CorrectionError, CorrectionScope, SaveCorrection
from ..storage.db import in_thread

router = APIRouter(prefix="/api/workflows")

Name = Annotated[str, Query(min_length=1, max_length=120)]


class WorkflowInfo(Model):
    id: Id
    name: str


@router.get("", response_model=Page[WorkflowInfo])
async def list_workflows(
    request: Request,
    principal: CurrentPrincipal,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[WorkflowInfo]:
    try:
        page = await in_thread(
            request.app.state.repository.list_workflows, principal.owner_id, cursor, limit
        )
    except ValueError as exc:  # a cursor the client edited or truncated
        raise HTTPException(400, "Malformed workflow cursor.") from exc
    return Page[WorkflowInfo](
        items=[WorkflowInfo(id=row["id"], name=row["name"]) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=WorkflowInfo, status_code=201, dependencies=[Mutation])
async def import_workflow(
    request: Request, principal: CurrentPrincipal, name: Name
) -> WorkflowInfo:
    payload = await request.body()
    try:
        workflow_id, _graph = await in_thread(
            request.app.state.workflows.import_workflow, principal.owner_id, name, payload
        )
    except GraphImportError as exc:
        # Client-safe by construction; the graph is refused, never repaired.
        raise HTTPException(422, str(exc)) from exc
    return WorkflowInfo(id=workflow_id, name=name)


@router.get("/{workflow_id}/controls", response_model=ControlSchema)
async def workflow_controls(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> ControlSchema:
    # No upstream call and no cooldown: an unavailable catalog yields a snapshot
    # that marks the schema unvalidated rather than failing the request.
    snapshot = await request.app.state.catalog.snapshot()
    schema = await in_thread(
        request.app.state.workflows.control_schema, principal.owner_id, workflow_id, snapshot
    )
    if schema is None:
        raise HTTPException(404, "Workflow was not found.")
    return schema


# --- saved corrections ----------------------------------------------------
#
# The schema above already has the profile's corrections replayed over it.
# These three routes are the editor's side: export what is saved (stale entries
# included), save one correction optimistically, and reset back to the derived
# base. Nothing here accepts an owner or a selector from the body.


class ResetResult(Model):
    removed: int


@router.get("/{workflow_id}/corrections", response_model=list[Correction])
async def export_corrections(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> list[Correction]:
    snapshot = await request.app.state.catalog.snapshot()
    corrections = await in_thread(
        request.app.state.workflows.export_corrections,
        principal.owner_id,
        workflow_id,
        snapshot,
    )
    if corrections is None:
        raise HTTPException(404, "Workflow was not found.")
    return corrections


@router.put("/{workflow_id}/corrections", response_model=Correction, dependencies=[Mutation])
async def save_correction(
    request: Request, workflow_id: str, principal: CurrentPrincipal, body: SaveCorrection
) -> Correction:
    snapshot = await request.app.state.catalog.snapshot()
    try:
        return await in_thread(
            request.app.state.workflows.save_correction,
            principal.owner_id,
            workflow_id,
            snapshot,
            body,
        )
    except CorrectionError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.delete("/{workflow_id}/corrections", response_model=ResetResult, dependencies=[Mutation])
async def reset_corrections(
    request: Request,
    workflow_id: str,
    principal: CurrentPrincipal,
    scope: CorrectionScope | None = None,
    selector: Annotated[str | None, Query(max_length=400)] = None,
) -> ResetResult:
    removed = await in_thread(
        request.app.state.workflows.reset_corrections,
        principal.owner_id,
        workflow_id,
        scope,
        selector,
    )
    return ResetResult(removed=removed)
