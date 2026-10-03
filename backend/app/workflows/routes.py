"""Authenticated workflow import, listing, and control-schema projection.

The import body is the ComfyUI API JSON *as sent*: it is handed to
``parse_graph`` as raw bytes rather than being decoded by Pydantic first, so the
importer's own rules (size limit, duplicate object keys, non-finite constants,
exact large integers) apply to the real file instead of to a re-serialized copy.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field, StringConstraints

from ..auth.routes import CurrentPrincipal, Mutation
from ..contracts import ControlSchema, ExactInt, Id, Model, Page
from ..mapping import GraphImportError
from ..mapping.corrections import (
    Correction,
    CorrectionError,
    CorrectionScope,
    SaveCorrection,
)
from ..mapping.submission import SubmissionError
from ..storage.db import in_thread
from ..storage.repository import LimitExceeded, RevisionConflict
from .layout import BindingId, SaveLayout, WorkflowLayout
from .presets import MAX_PRESETS, Preset, SavePreset, UpdatePreset

router = APIRouter(prefix="/api/workflows")

Name = Annotated[str, Query(min_length=1, max_length=120)]


class WorkflowInfo(Model):
    id: Id
    name: str
    #: The graph revision reads and new generations use (1 until replaced).
    current_revision: int
    #: Last rename or graph replacement, epoch milliseconds, exact.
    updated_ms: ExactInt


def _info(row: dict) -> WorkflowInfo:
    return WorkflowInfo(
        id=row["id"],
        name=row["name"],
        current_revision=int(row["current_revision"]),
        updated_ms=str(row["updated_ms"]),
    )


class RenameWorkflow(Model):
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]


class ReplaceResult(Model):
    workflow: WorkflowInfo
    revision: int
    #: Control binding ids only in the new / only in the old schema.
    added: list[BindingId]
    removed: list[BindingId]


class ReplaceValues(Model):
    edits: dict[BindingId, str | bool | int | float] = Field(min_length=1)
    expected_revision: int = Field(ge=1)


@router.get("", response_model=Page[WorkflowInfo])
async def list_workflows(
    request: Request,
    principal: CurrentPrincipal,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[WorkflowInfo]:
    try:
        page = await in_thread(
            request.app.state.repository.list_workflows,
            principal.owner_id,
            cursor,
            limit,
        )
    except ValueError as exc:  # a cursor the client edited or truncated
        raise HTTPException(400, "Malformed workflow cursor.") from exc
    return Page[WorkflowInfo](
        items=[_info(row) for row in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=WorkflowInfo, status_code=201, dependencies=[Mutation])
async def import_workflow(
    request: Request, principal: CurrentPrincipal, name: Name
) -> WorkflowInfo:
    payload = await request.body()
    try:
        workflow_id, _graph = await in_thread(
            request.app.state.workflows.import_workflow,
            principal.owner_id,
            name,
            payload,
        )
    except GraphImportError as exc:
        # Client-safe by construction; the graph is refused, never repaired.
        raise HTTPException(422, str(exc)) from exc
    row = await in_thread(
        request.app.state.repository.get_workflow, principal.owner_id, workflow_id
    )
    return _info(row)


@router.patch("/{workflow_id}", response_model=WorkflowInfo, dependencies=[Mutation])
async def rename_workflow(
    request: Request,
    workflow_id: str,
    principal: CurrentPrincipal,
    body: RenameWorkflow,
) -> WorkflowInfo:
    row = await in_thread(
        request.app.state.repository.rename_workflow,
        principal.owner_id,
        workflow_id,
        body.name,
    )
    if row is None:
        raise HTTPException(404, "Workflow was not found.")
    return _info(row)


@router.put(
    "/{workflow_id}/graph", response_model=ReplaceResult, dependencies=[Mutation]
)
async def replace_graph(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> ReplaceResult:
    payload = await request.body()  # raw, like import: parse_graph owns the rules
    snapshot = await request.app.state.catalog.snapshot()
    try:
        result = await in_thread(
            request.app.state.workflows.replace_graph,
            principal.owner_id,
            workflow_id,
            payload,
            snapshot,
        )
    except GraphImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Workflow was not found.")
    row, revision, added, removed = result
    return ReplaceResult(
        workflow=_info(row), revision=revision, added=added, removed=removed
    )


@router.put(
    "/{workflow_id}/values", response_model=ReplaceResult, dependencies=[Mutation]
)
async def replace_values(
    request: Request, workflow_id: str, principal: CurrentPrincipal, body: ReplaceValues
) -> ReplaceResult:
    snapshot = await request.app.state.catalog.snapshot()
    try:
        result = await in_thread(
            request.app.state.workflows.replace_values,
            principal.owner_id,
            workflow_id,
            body.edits,
            body.expected_revision,
            snapshot,
        )
    except SubmissionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Workflow was not found.")
    row, revision, added, removed = result
    return ReplaceResult(
        workflow=_info(row), revision=revision, added=added, removed=removed
    )


@router.delete("/{workflow_id}", status_code=204, dependencies=[Mutation])
async def delete_workflow(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> Response:
    deleted = await in_thread(
        request.app.state.repository.delete_workflow, principal.owner_id, workflow_id
    )
    if not deleted:
        raise HTTPException(404, "Workflow was not found.")
    return Response(status_code=204)


@router.get("/{workflow_id}/controls", response_model=ControlSchema)
async def workflow_controls(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> ControlSchema:
    # No upstream call and no cooldown: an unavailable catalog yields a snapshot
    # that marks the schema unvalidated rather than failing the request.
    snapshot = await request.app.state.catalog.snapshot()
    schema = await in_thread(
        request.app.state.workflows.control_schema,
        principal.owner_id,
        workflow_id,
        snapshot,
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


@router.put(
    "/{workflow_id}/corrections", response_model=Correction, dependencies=[Mutation]
)
async def save_correction(
    request: Request,
    workflow_id: str,
    principal: CurrentPrincipal,
    body: SaveCorrection,
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


@router.delete(
    "/{workflow_id}/corrections", response_model=ResetResult, dependencies=[Mutation]
)
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


# --- saved layouts --------------------------------------------------------
#
# Arrangement only (docs/UI_DESIGNER.md). Saving never needs the catalog; the
# snapshot is read solely to report stale bindings.


@router.get("/{workflow_id}/layout", response_model=WorkflowLayout)
async def get_layout(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> WorkflowLayout:
    snapshot = await request.app.state.catalog.snapshot()
    layout = await in_thread(
        request.app.state.workflows.get_layout,
        principal.owner_id,
        workflow_id,
        snapshot,
    )
    if layout is None:
        raise HTTPException(404, "Workflow was not found.")
    return layout


@router.put(
    "/{workflow_id}/layout", response_model=WorkflowLayout, dependencies=[Mutation]
)
async def save_layout(
    request: Request, workflow_id: str, principal: CurrentPrincipal, body: SaveLayout
) -> WorkflowLayout:
    snapshot = await request.app.state.catalog.snapshot()
    try:
        layout = await in_thread(
            request.app.state.workflows.save_layout,
            principal.owner_id,
            workflow_id,
            snapshot,
            body,
        )
    except CorrectionError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if layout is None:
        raise HTTPException(404, "Workflow was not found.")
    return layout


@router.delete("/{workflow_id}/layout", status_code=204, dependencies=[Mutation])
async def delete_layout(
    request: Request,
    workflow_id: str,
    principal: CurrentPrincipal,
    expected_revision: Annotated[int | None, Query(ge=0)] = None,
) -> Response:
    try:
        found = await in_thread(
            request.app.state.workflows.delete_layout,
            principal.owner_id,
            workflow_id,
            expected_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not found:
        raise HTTPException(404, "Workflow was not found.")
    return Response(status_code=204)


# --- value presets --------------------------------------------------------
#
# Named value snapshots (docs/UI_DESIGNER.md "Presets"). Keys are not checked
# against the schema: a preset may outlive graph changes.


def _preset(row: dict) -> Preset:
    return Preset(
        id=row["id"],
        name=row["name"],
        values=json.loads(row["values_json"]),
        revision=int(row["revision"]),
        created_ms=str(row["created_ms"]),
        updated_ms=str(row["updated_ms"]),
    )


@router.get("/{workflow_id}/presets", response_model=list[Preset])
async def list_presets(
    request: Request, workflow_id: str, principal: CurrentPrincipal
) -> list[Preset]:
    repository = request.app.state.repository
    if (
        await in_thread(repository.get_workflow, principal.owner_id, workflow_id)
        is None
    ):
        raise HTTPException(404, "Workflow was not found.")
    rows = await in_thread(repository.list_presets, principal.owner_id, workflow_id)
    return [_preset(row) for row in rows]


@router.post(
    "/{workflow_id}/presets",
    response_model=Preset,
    status_code=201,
    dependencies=[Mutation],
)
async def create_preset(
    request: Request, workflow_id: str, principal: CurrentPrincipal, body: SavePreset
) -> Preset:
    try:
        row = await in_thread(
            request.app.state.repository.create_preset,
            principal.owner_id,
            workflow_id,
            body.name,
            body.values,
            MAX_PRESETS,
        )
    except LimitExceeded as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "Workflow was not found.")
    return _preset(row)


@router.put(
    "/{workflow_id}/presets/{preset_id}", response_model=Preset, dependencies=[Mutation]
)
async def update_preset(
    request: Request,
    workflow_id: str,
    preset_id: str,
    principal: CurrentPrincipal,
    body: UpdatePreset,
) -> Preset:
    try:
        row = await in_thread(
            request.app.state.repository.update_preset,
            principal.owner_id,
            workflow_id,
            preset_id,
            body.name,
            body.values,
            body.expected_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "Preset was not found.")
    return _preset(row)


@router.delete(
    "/{workflow_id}/presets/{preset_id}", status_code=204, dependencies=[Mutation]
)
async def delete_preset(
    request: Request, workflow_id: str, preset_id: str, principal: CurrentPrincipal
) -> Response:
    deleted = await in_thread(
        request.app.state.repository.delete_preset,
        principal.owner_id,
        workflow_id,
        preset_id,
    )
    if not deleted:
        raise HTTPException(404, "Preset was not found.")
    return Response(status_code=204)
