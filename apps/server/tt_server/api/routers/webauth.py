"""Interactive sign-in: OIDC, LDAP/AD, session cookie, browser-based CLI token minting (§4.5, §4.6)."""

from __future__ import annotations

import secrets
import time
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, MemberStatus, TargetType

from tt_server.api.deps import Uow, _authenticate, hash_token, new_token
from tt_server.api.errors import ApiError
from tt_server.api.session import (
    SESSION_COOKIE,
    clear_session,
    load_state,
    read_session,
    set_session,
    signed_state,
)
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.integrations import idp

router = APIRouter(prefix="/auth", tags=["auth"])
api_router = APIRouter()

_FAILURES: dict[str, list[float]] = {}
MAX_FAILURES, WINDOW = 5, 300.0


def _throttle(key: str) -> None:
    now = time.monotonic()
    recent = [t for t in _FAILURES.get(key.lower(), []) if now - t < WINDOW]
    _FAILURES[key.lower()] = recent
    if len(recent) >= MAX_FAILURES:
        raise ApiError(429, "too_many_attempts", "too many failed sign-ins; try again in a few minutes")


def _failed(key: str) -> None:
    _FAILURES.setdefault(key.lower(), []).append(time.monotonic())


def _secure(request: Request) -> bool:
    s = request.app.state.settings
    proto = request.headers.get("x-forwarded-proto") if s.trust_forwarded_proto else None
    return (proto or request.url.scheme) == "https"


def _safe_next(request: Request, nxt: str | None) -> str:
    """Only same-site paths or the configured web origin; anything else falls back to `/`."""
    if not nxt:
        return "/"
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    origin = request.app.state.settings.web_origin
    if (
        origin
        and urlparse(nxt).netloc == urlparse(origin).netloc
        and urlparse(nxt).scheme in {"http", "https"}
    ):
        return nxt
    return "/"


async def _workspace(uow: Uow, slug: str) -> m.Workspace:
    ws = await uow.session.scalar(
        select(m.Workspace).where(m.Workspace.slug == slug, m.Workspace.deleted_at.is_(None))
    )
    if ws is None:
        raise ApiError(404, "not_found", "workspace not found")
    return ws


@router.get("/providers")
async def providers(request: Request) -> dict[str, bool]:
    return {"oidc": request.app.state.oidc is not None, "ldap": request.app.state.ldap is not None}


# ---------------------------------------------------------------- OIDC
@router.get("/login")
async def login(request: Request, workspace: str, uow: Uow, next: str | None = None) -> Response:  # noqa: A002
    oidc = request.app.state.oidc
    if oidc is None:
        raise ApiError(404, "not_found", "OIDC sign-in is not configured")
    await _workspace(uow, workspace)
    from tt_server.integrations.oidc import pkce_pair

    verifier, challenge = pkce_pair()
    nonce, state = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    url = await oidc.authorization_url(state=state, nonce=nonce, challenge=challenge)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        "tt_oidc",
        signed_state(
            request.app.state.settings,
            {"s": state, "n": nonce, "v": verifier, "w": workspace, "next": _safe_next(request, next)},
        ),
        max_age=600,
        httponly=True,
        secure=_secure(request),
        samesite="lax",
        path="/auth",
    )
    return resp


@router.get("/callback")
async def callback(request: Request, uow: Uow, code: str = "", state: str = "") -> Response:
    settings, oidc = request.app.state.settings, request.app.state.oidc
    raw = request.cookies.get("tt_oidc", "")
    data = load_state(settings, raw) if raw else None
    if oidc is None or data is None or not secrets.compare_digest(data["s"], state):
        raise ApiError(400, "invalid_state", "sign-in state is missing, expired or does not match")
    ident = await oidc.identity(code=code, verifier=data["v"], nonce=data["n"])
    ws = await _workspace(uow, data["w"])
    member = await idp.provision_login(
        uow,
        ws,
        provider="oidc",
        email=ident.email,
        display_name=ident.display_name,
        handle_hint=ident.handle_hint,
        groups=ident.groups,
    )
    resp = RedirectResponse(data["next"], status_code=302)
    set_session(resp, settings, member.person_id, secure=_secure(request))
    resp.delete_cookie("tt_oidc", path="/auth")
    return resp


# ---------------------------------------------------------------- LDAP / Active Directory
class LdapLogin(BaseModel):
    workspace: str
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


async def _ldap_member(request: Request, uow: Uow, body: LdapLogin) -> m.WorkspaceMember:
    ldap = request.app.state.ldap
    if ldap is None:
        raise ApiError(404, "not_found", "LDAP sign-in is not configured")
    _throttle(body.username)
    user = await ldap.authenticate(body.username, body.password)
    if user is None:
        _failed(body.username)
        raise ApiError(401, "invalid_credentials", "invalid username or password")
    ws = await _workspace(uow, body.workspace)
    if not user.email:
        raise ApiError(401, "invalid_credentials", "the directory account has no e-mail address")
    return await idp.provision_login(
        uow,
        ws,
        provider="ldap",
        email=user.email,
        display_name=user.display_name,
        handle_hint=user.sam,
        groups=user.groups,
    )


@router.post("/ldap/login")
async def ldap_login(body: LdapLogin, request: Request, response: Response, uow: Uow) -> dict[str, Any]:
    member = await _ldap_member(request, uow, body)
    set_session(response, request.app.state.settings, member.person_id, secure=_secure(request))
    return {"handle": member.handle, "person_id": member.person_id}


@router.post("/ldap/token", status_code=201)
async def ldap_token(body: LdapLogin, request: Request, uow: Uow) -> dict[str, Any]:
    """Password sign-in for the CLI: returns a capture+read API token (no cookie)."""
    member = await _ldap_member(request, uow, body)
    return {**await _mint(uow, member, "cli (ldap)", ["capture", "read"], 90), "handle": member.handle}


# ---------------------------------------------------------------- session + CLI token
@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    clear_session(response)


@router.get("/me")
async def me(request: Request, uow: Uow) -> dict[str, Any]:
    sess = read_session(request, request.app.state.settings)
    if sess is None:
        raise ApiError(401, "unauthorized", "not signed in")
    person = await uow.session.get(m.Person, sess[0])
    if person is None:
        raise ApiError(401, "unauthorized", "not signed in")
    rows = (
        await uow.session.execute(
            select(m.Workspace.slug, m.WorkspaceMember.handle)
            .join(m.WorkspaceMember, m.WorkspaceMember.workspace_id == m.Workspace.id)
            .where(m.WorkspaceMember.person_id == person.id, m.WorkspaceMember.status == MemberStatus.ACTIVE)
        )
    ).all()
    return {
        "person_id": person.id,
        "display_name": person.display_name,
        "workspaces": [{"slug": s, "handle": h} for s, h in rows],
    }


@api_router.get("/api/v1/me/workspaces", tags=["auth"])
async def my_workspaces(
    request: Request, uow: Uow, authorization: Annotated[str | None, Header()] = None
) -> dict[str, Any]:
    """Workspaces the caller belongs to -- by session cookie or API token (feeds the workspace switcher)."""
    person: m.Person | None = None
    if authorization and authorization.lower().startswith("bearer "):
        person, _ = await _authenticate(request, uow, authorization, None, None)
    else:
        sess = read_session(request, request.app.state.settings)
        person = await uow.session.get(m.Person, sess[0]) if sess else None
    if person is None:
        raise ApiError(401, "unauthorized", "not signed in")
    rows = (
        await uow.session.execute(
            select(m.Workspace.slug, m.Workspace.name, m.WorkspaceMember.handle)
            .join(m.WorkspaceMember, m.WorkspaceMember.workspace_id == m.Workspace.id)
            .where(m.WorkspaceMember.person_id == person.id, m.WorkspaceMember.status == MemberStatus.ACTIVE)
        )
    ).all()
    return {"data": [{"slug": s, "name": n, "handle": h} for s, n, h in rows], "next_cursor": None}


async def _mint(
    uow: Uow, member: m.WorkspaceMember, name: str, scopes: list[str], days: int
) -> dict[str, Any]:
    secret = new_token()
    row = m.ApiToken(
        id=new_id(),
        workspace_id=member.workspace_id,
        person_id=member.person_id,
        name=name,
        hash=hash_token(secret),
        scopes=scopes,
        expires_at=utcnow() + timedelta(days=days),
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=member.workspace_id,
        actor_id=member.person_id,
        entity_type=TargetType.PERSON,
        entity_id=member.person_id,
        event_type="token.created",
        payload={"after": {"token_id": row.id, "name": name}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return {"id": row.id, "token": secret, "expires_at": row.expires_at, "scopes": scopes}


def _loopback(url: str) -> bool:
    u = urlparse(url)
    return u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost", "::1"} and u.port is not None


@router.get("/cli")
async def cli_login(request: Request, workspace: str, callback: str, state: str, uow: Uow) -> Response:
    """`tt login --browser`: the CLI listens on a loopback port, the user signs in here, the token goes
    back to that port only. Without a session the browser is sent through the normal sign-in first."""
    if not _loopback(callback):
        raise ApiError(
            400, "bad_callback", "callback must be http://127.0.0.1:<port>/...", pointer="/callback"
        )
    sess = read_session(request, request.app.state.settings)
    if sess is None:
        here = f"/auth/cli?{urlencode({'workspace': workspace, 'callback': callback, 'state': state})}"
        if request.app.state.oidc is None:
            raise ApiError(401, "login_required", "sign in to the web console first, then retry")
        return RedirectResponse(
            f"/auth/login?workspace={quote(workspace)}&next={quote(here)}", status_code=302
        )
    ws = await _workspace(uow, workspace)
    member = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id,
            m.WorkspaceMember.person_id == sess[0],
            m.WorkspaceMember.status == MemberStatus.ACTIVE,
        )
    )
    if member is None:
        raise ApiError(404, "not_found", "workspace not found")
    tok = await _mint(uow, member, "cli (browser)", ["capture", "read"], 90)
    sep = "&" if "?" in callback else "?"
    return RedirectResponse(
        f"{callback}{sep}{urlencode({'token': tok['token'], 'state': state, 'handle': member.handle})}",
        status_code=302,
    )


__all__ = ["SESSION_COOKIE", "router"]
