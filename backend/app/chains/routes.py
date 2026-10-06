"""Authenticated chain definitions and runs. Every row is owner-scoped."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ..auth.routes import CurrentPrincipal, Mutation
from ..contracts import Id, Model
from ..storage.db import in_thread
from .service import ChainDefinition, ChainError

router = APIRouter(prefix="/api/chains")


class StartRun(Model):
    #: Client-generated idempotency key; a retry returns the original run.
    request_key: Id


def _service(request: Request) -> Any:
    return request.app.state.chains


def _found(value: Any, what: str) -> Any:
    if value is None:
        raise HTTPException(404, f"{what} was not found.")
    return value


@router.get("")
async def list_chains(request: Request, principal: CurrentPrincipal) -> dict[str, Any]:
    return {"items": await in_thread(_service(request).list, principal.owner_id)}


@router.post("", status_code=201, dependencies=[Mutation])
async def create_chain(
    request: Request, principal: CurrentPrincipal, body: ChainDefinition
) -> dict[str, Any]:
    try:
        return await _service(request).create(principal.owner_id, body)
    except ChainError as exc:
        raise HTTPException(422, str(exc)) from exc


# Registered before ``/{chain_id}`` so "runs" is never read as a chain id.
@router.get("/runs/{run_id}")
async def get_run(
    request: Request, principal: CurrentPrincipal, run_id: Id
) -> dict[str, Any]:
    chains = _service(request)
    await chains.advance(principal.owner_id, run_id)
    return _found(await in_thread(chains.get_run, principal.owner_id, run_id), "Run")


@router.post("/runs/{run_id}/cancel", dependencies=[Mutation])
async def cancel_run(
    request: Request, principal: CurrentPrincipal, run_id: Id
) -> dict[str, Any]:
    try:
        await _service(request).cancel(principal.owner_id, run_id)
    except ChainError as exc:
        raise HTTPException(404, str(exc)) from exc
    return await in_thread(_service(request).get_run, principal.owner_id, run_id)


@router.post("/runs/{run_id}/resume", dependencies=[Mutation])
async def resume_run(
    request: Request, principal: CurrentPrincipal, run_id: Id
) -> dict[str, Any]:
    chains = _service(request)
    try:
        await in_thread(chains.resume, principal.owner_id, run_id)
    except ChainError as exc:
        raise HTTPException(409, str(exc)) from exc
    await chains.advance(principal.owner_id, run_id)
    return await in_thread(chains.get_run, principal.owner_id, run_id)


@router.get("/{chain_id}")
async def get_chain(
    request: Request, principal: CurrentPrincipal, chain_id: Id
) -> dict[str, Any]:
    chains = _service(request)
    chain = _found(await in_thread(chains.get, principal.owner_id, chain_id), "Chain")
    return {
        **chain,
        "runs": await in_thread(chains.list_runs, principal.owner_id, chain_id),
    }


@router.delete("/{chain_id}", status_code=204, dependencies=[Mutation])
async def delete_chain(
    request: Request, principal: CurrentPrincipal, chain_id: Id
) -> Response:
    if not await in_thread(_service(request).delete, principal.owner_id, chain_id):
        raise HTTPException(404, "Chain was not found.")
    return Response(status_code=204)


@router.post("/{chain_id}/runs", status_code=201, dependencies=[Mutation])
async def start_run(
    request: Request, principal: CurrentPrincipal, chain_id: Id, body: StartRun
) -> dict[str, Any]:
    chains = _service(request)
    try:
        run = await in_thread(
            chains.start, principal.owner_id, chain_id, body.request_key
        )
    except ChainError as exc:
        raise HTTPException(404, str(exc)) from exc
    await chains.advance(principal.owner_id, run["id"])
    return await in_thread(chains.get_run, principal.owner_id, run["id"])
