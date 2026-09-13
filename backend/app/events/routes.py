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

from types import SimpleNamespace

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth.routes import auth_service
from ..auth.security import SESSION_COOKIE, request_origin_ok
from ..storage.db import in_thread

router = APIRouter()


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
    try:
        while True:
            event = await subscription.receive()
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
