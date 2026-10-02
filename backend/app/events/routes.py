"""Authenticated, owner-filtered WebSocket delivery of generation events.

Wiring (app/main.py)::

    app.state.generation_events = events   # the same EventBroker GenerationService uses
    app.include_router(events_router)      # BEFORE the /api/{path} catch-all

Event delivery is advisory only (docs/ARCHITECTURE.md "Event delivery is
advisory"): a client reconnects and re-fetches the owned generation snapshot
over /api/generations rather than depending on replay of every frame. This
socket only forwards whatever EventBroker.subscribe already scoped to the
caller's own profile; it invents no additional filtering and no admin view.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth.routes import auth_service
from ..auth.security import SESSION_COOKIE, request_origin_ok
from ..storage.db import in_thread

router = APIRouter()

#: How often an open socket nudges its owner's generations to reconcile.
#: GenerationService.reconcile_if_due self-throttles below its own cooldown,
#: so polling a little faster than that costs nothing extra upstream.
RECONCILE_POLL_S = 1.0


def _origin_ok(websocket: WebSocket) -> bool:
    # request_origin_ok compares against the Origin header, which a browser
    # sends as http(s) even for a ws(s) connection; translate the scheme
    # rather than touching the shared (and unowned) auth.security helper.
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    proxy = SimpleNamespace(headers=websocket.headers, url=SimpleNamespace(scheme=scheme))
    return request_origin_ok(proxy)


@router.websocket("/api/events")
async def generation_events(websocket: WebSocket) -> None:
    if not _origin_ok(websocket):
        await websocket.close(code=4403)
        return
    service = auth_service(websocket)
    token = websocket.cookies.get(SESSION_COOKIE)
    principal = await in_thread(service.resolve, token)
    if principal is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    broker = websocket.app.state.generation_events
    subscription = broker.subscribe(principal.owner_id)
    generations = websocket.app.state.generations
    settings = websocket.app.state.settings
    try:
        while True:
            try:
                event = await asyncio.wait_for(subscription.receive(), timeout=RECONCILE_POLL_S)
            except asyncio.TimeoutError:
                # GEN-003: this app runs no perpetual background poller (by
                # design -- see GenerationService.reconcile_if_due), and nothing
                # else drives reconciliation for a generation the frontend is
                # only watching over this socket (it re-fetches on an event, it
                # does not itself poll). A generation whose only path to a
                # terminal state was history reconciliation could otherwise sit
                # at output_state 'pending' forever with no event ever
                # published. An open socket means a client is actively
                # watching, so nudge that owner's own generations here instead.
                store_history = await in_thread(settings.profile, principal.owner_id)
                await generations.reconcile_if_due(
                    history_retention={principal.owner_id: store_history["store_history"]}
                )
                continue
            # Logout, password changes, and mode switches invalidate sessions.
            # Recheck immediately before delivery so an already-open socket
            # cannot outlive that boundary and leak its former owner's event.
            current = await in_thread(service.resolve, token)
            if current is None or current.owner_id != principal.owner_id:
                await websocket.close(code=4401)
                return
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        broker.unsubscribe(subscription)
