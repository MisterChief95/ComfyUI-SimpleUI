"""/api/session and /api/profiles, plus the dependencies other routers reuse.

Guard order for every state-changing request:

1. ``Origin``/``Referer`` must match the request's own ``Host`` (CSRF).
2. If a session cookie is presented, the ``X-CSRF-Token`` header must match the
   value derived from that session (double submit, so a cross-site page cannot
   supply it even if it somehow reached step 1).
3. Host-scoped operations additionally require a genuinely local request.

Nothing here trusts a request body for identity: ``Principal.owner_id`` is the
only owner any handler may use.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, StringConstraints

from ..contracts import Id, Model
from ..storage.db import in_thread
from .security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_matches,
    csrf_token,
    request_is_local,
    request_origin_ok,
)
from .service import AuthService, Forbidden, Principal, Unauthorized

router = APIRouter(prefix="/api")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Name = Annotated[
    str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)
]
Secret = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


# --- models ---------------------------------------------------------------


class ProfileInfo(Model):
    id: Id
    name: str
    is_default: bool


class SessionInfo(Model):
    authenticated: bool
    multi_user: bool
    #: True when this is the implicit single-user Default session (no cookie).
    anonymous: bool
    profile: ProfileInfo | None = None
    #: Send back as X-CSRF-Token on mutations. Null for the implicit session.
    csrf_token: str | None = None


class LoginRequest(Model):
    name: Name
    password: Secret


class CreateProfileRequest(Model):
    name: Name
    password: Secret


class ChangePasswordRequest(Model):
    current_password: Secret
    new_password: Secret


class CreatedProfile(Model):
    id: Id
    name: str


class ProfileList(Model):
    profiles: list[ProfileInfo] = Field(default_factory=list)


# --- dependencies ---------------------------------------------------------


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth


async def current_principal(request: Request) -> Principal:
    """The caller, or 401. Single-user mode resolves to Default automatically."""
    service = auth_service(request)
    token = request.cookies.get(SESSION_COOKIE)
    principal = await in_thread(service.resolve, token)
    if principal is None:
        raise Unauthorized("Sign in to continue.")
    return principal


async def guard_mutation(request: Request) -> None:
    """CSRF: same-origin check always, double-submit token when a cookie is used."""
    if request.method in SAFE_METHODS:
        return
    if not request_origin_ok(request):
        raise Forbidden("Request origin is not this server; refused as cross-site.")
    token = request.cookies.get(SESSION_COOKIE)
    if token and not csrf_matches(
        request.app.state.session_secret, token, request.headers.get(CSRF_HEADER)
    ):
        raise Forbidden("Missing or invalid CSRF token.")


def require_local(request: Request) -> None:
    """Host-level gate: a real loopback peer addressing the server as loopback.

    Forwarded headers are never consulted, so a LAN client cannot claim to be
    local. This is a host configuration boundary, not an administrator role.
    """
    if not request_is_local(request):
        raise Forbidden(
            "This is a local host setting. Change it in the browser on the computer "
            "running SimpleUI (http://localhost), not from another device."
        )


LocalRequest = Depends(require_local)
Mutation = Depends(guard_mutation)
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


# --- cookies --------------------------------------------------------------


def _set_session_cookies(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https"
    common = {"path": "/", "samesite": "strict", "secure": secure}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    # Readable by the SPA on purpose: it echoes the value in X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token(request.app.state.session_secret, token),
        httponly=False,
        **common,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _session_info(
    request: Request, principal: Principal | None, multi_user: bool
) -> SessionInfo:
    if principal is None:
        return SessionInfo(authenticated=False, multi_user=multi_user, anonymous=False)
    return SessionInfo(
        authenticated=True,
        multi_user=multi_user,
        anonymous=principal.session_token is None,
        profile=ProfileInfo(
            id=principal.profile_id,
            name=principal.name,
            is_default=principal.is_default,
        ),
        csrf_token=(
            None
            if principal.session_token is None
            else csrf_token(request.app.state.session_secret, principal.session_token)
        ),
    )


# --- routes ---------------------------------------------------------------


@router.get("/session", response_model=SessionInfo)
async def read_session(request: Request) -> SessionInfo:
    service = auth_service(request)
    principal = await in_thread(service.resolve, request.cookies.get(SESSION_COOKIE))
    multi_user = await in_thread(service.multi_user_enabled)
    return _session_info(request, principal, multi_user)


@router.post("/session", response_model=SessionInfo, dependencies=[Mutation])
async def login(
    request: Request, response: Response, body: LoginRequest
) -> SessionInfo:
    service = auth_service(request)
    token = await in_thread(service.login, body.name, body.password)
    _set_session_cookies(request, response, token)
    principal = await in_thread(service.resolve, token)
    return _session_info(request, principal, True)


@router.delete("/session", response_model=SessionInfo, dependencies=[Mutation])
async def logout(request: Request, response: Response) -> SessionInfo:
    service = auth_service(request)
    await in_thread(service.logout, request.cookies.get(SESSION_COOKIE))
    _clear_session_cookies(response)
    multi_user = await in_thread(service.multi_user_enabled)
    # In single-user mode the implicit Default session is immediately back; that
    # is the mode working as designed, not a failed logout.
    principal = None if multi_user else await in_thread(service.resolve, None)
    return _session_info(request, principal, multi_user)


@router.get("/profiles", response_model=ProfileList)
async def list_profiles(request: Request) -> ProfileList:
    """Names only, so the sign-in screen can offer a picker."""
    service = auth_service(request)
    rows = await in_thread(service.list_profiles)
    return ProfileList(profiles=[ProfileInfo(**row) for row in rows])


@router.post(
    "/profiles",
    response_model=CreatedProfile,
    status_code=201,
    dependencies=[Mutation, LocalRequest],
)
async def create_profile(
    request: Request, body: CreateProfileRequest
) -> CreatedProfile:
    service = auth_service(request)
    profile_id = await in_thread(service.create_profile, body.name, body.password)
    return CreatedProfile(id=profile_id, name=body.name)


@router.post("/profiles/me/password", status_code=204, dependencies=[Mutation])
async def change_password(
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    body: ChangePasswordRequest,
) -> Response:
    service = auth_service(request)
    await in_thread(
        service.change_own_password, principal, body.current_password, body.new_password
    )
    # Every session for this profile, including this one, was just dropped.
    _clear_session_cookies(response)
    response.status_code = 204
    return response
