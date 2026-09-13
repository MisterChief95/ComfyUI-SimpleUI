"""/api/settings: effective settings, profile preferences, and the host gate.

Host settings and the multi-user mode switch are reachable only from the
machine running SimpleUI (``require_local``). Profile settings are written
against the session's own ``owner_id``; a body cannot name another profile.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import Field, StringConstraints

from ..auth.routes import CurrentPrincipal, LocalRequest, Mutation, auth_service
from ..auth.security import request_is_local
from ..auth.service import AuthError
from ..contracts import Model
from ..storage.db import in_thread
from .service import HOST_SETTINGS, PROFILE_SETTINGS, SettingsError, SettingsStore

router = APIRouter(prefix="/api/settings")

Key = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Value = bool | int | str


def settings_store(request: Request) -> SettingsStore:
    return request.app.state.settings


class EffectiveSettings(Model):
    host: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    #: True when this request could change host settings (it is local).
    host_writable: bool
    multi_user: bool


class SettingWrite(Model):
    key: Key
    value: Value


class ActivateRequest(Model):
    #: Becomes the Default profile's password; every session is invalidated.
    default_password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


@router.get("", response_model=EffectiveSettings)
async def read_settings(request: Request, principal: CurrentPrincipal) -> EffectiveSettings:
    store = settings_store(request)
    return EffectiveSettings(
        host=await in_thread(store.host),
        profile=await in_thread(store.profile, principal.owner_id),
        host_writable=request_is_local(request),
        multi_user=await in_thread(auth_service(request).multi_user_enabled),
    )


@router.put("/profile", status_code=204, dependencies=[Mutation])
async def write_profile_setting(
    request: Request, principal: CurrentPrincipal, body: SettingWrite
) -> None:
    store = settings_store(request)
    try:
        await in_thread(store.set_profile, principal.owner_id, body.key, body.value)
    except SettingsError as exc:
        raise AuthError(str(exc)) from exc


@router.put("/host", status_code=204, dependencies=[Mutation, LocalRequest])
async def write_host_setting(request: Request, body: SettingWrite) -> None:
    store = settings_store(request)
    try:
        await in_thread(store.set_host, body.key, body.value)
    except SettingsError as exc:
        raise AuthError(str(exc)) from exc


@router.get("/keys")
async def readable_keys() -> dict[str, list[str]]:
    """What the UI may offer, so the client does not hard-code a second list."""
    return {
        "host": [key for key, spec in HOST_SETTINGS.items() if spec.writable],
        "profile": list(PROFILE_SETTINGS),
    }


@router.post("/multi-user/activate", status_code=204, dependencies=[Mutation, LocalRequest])
async def activate_multi_user(request: Request, body: ActivateRequest) -> None:
    """Enable multi-user mode. Fails safely while the media baseline is not ready."""
    await in_thread(auth_service(request).activate_multi_user, body.default_password)


@router.post("/multi-user/deactivate", status_code=204, dependencies=[Mutation, LocalRequest])
async def deactivate_multi_user(request: Request) -> None:
    """Blocked while any profile other than Default exists."""
    await in_thread(auth_service(request).deactivate_multi_user)
