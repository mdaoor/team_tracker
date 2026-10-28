"""Interactive sign-in: OIDC, LDAP/AD, session cookie, browser-based CLI token minting (§4.5, §4.6)."""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from tt_server.api.deps import Uow, _authenticate
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
from tt_server.domain import signin
from tt_server.domain.signin import MAX_FAILURES, WINDOW
from tt_server.domain.tokens import mint_api_token
from tt_server.integrations import idp
from tt_server.integrations.oidc import pkce_pair
from tt_server.ratelimit import client_ip
from tt_server.sso import principal_from

router = APIRouter(prefix="/auth", tags=["auth"])
api_router = APIRouter()


def _attempt_key(request: Request, username: str) -> str:
    host = (
        client_ip(
            request.app.state.settings,
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
        )
        or "?"
    )
    return f"{host}|{username.strip().lower()}"


def _secure(request: Request) -> bool:
    s = request.app.state.settings
    proto = request.headers.get("x-forwarded-proto") if s.trust_forwarded_proto else None
    return (proto or request.url.scheme) == "https"


def _safe_next(request: Request, nxt: str | None) -> str:
    return signin.safe_next(nxt, request.app.state.settings.web_origin)


async def _workspace(uow: Uow, slug: str) -> m.Workspace:
    return await signin.workspace_by_slug(uow, slug)


@router.get("/providers")
async def providers(request: Request) -> dict[str, bool]:
    return {
        "oidc": request.app.state.oidc is not None,
        "ldap": request.app.state.ldap is not None,
        "sso": bool(request.app.state.settings.trusted_principal_header),
        "dev": bool(request.app.state.settings.dev_auth),
    }


# ---------------------------------------------------------------- OIDC
@router.get("/login")
async def login(request: Request, workspace: str, uow: Uow, next: str | None = None) -> Response:  # noqa: A002
    oidc = request.app.state.oidc
    if oidc is None:
        raise ApiError(404, "not_found", "OIDC sign-in is not configured")
    await _workspace(uow, workspace)

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


# ---------------------------------------------------------------- development / UAT sign-in
class DevLogin(BaseModel):
    workspace: str
    handle: str = Field(min_length=1, max_length=64)


@router.post("/dev/login")
async def dev_login(body: DevLogin, request: Request, response: Response, uow: Uow) -> dict[str, Any]:
    """Sign in as an existing member without an identity provider. Exists only while `TT_DEV_AUTH` is on (which production refuses to
    start with), so a local install can be exercised in a browser; it is the cookie equivalent of the `X-Debug-Actor` header."""
    settings = request.app.state.settings
    if not settings.dev_auth:
        raise ApiError(404, "not_found", "dev sign-in is not enabled")
    member = await signin.dev_member(uow, body.workspace, body.handle)
    set_session(response, settings, member.person_id, secure=_secure(request))
    return {"handle": member.handle, "person_id": member.person_id}


# ---------------------------------------------------------------- Kerberos / SPNEGO behind a proxy
@router.get("/sso")
async def sso_login(request: Request, workspace: str, uow: Uow, next: str | None = None) -> Response:  # noqa: A002
    """The proxy has already authenticated the user (Negotiate). Turn the trusted principal into a session, exactly as the other sign-in
    methods do (same provisioning policy: existing member, mapped group, or allowed e-mail domain)."""

    settings = request.app.state.settings
    if not settings.trusted_principal_header:
        raise ApiError(404, "not_found", "SSO is not configured")
    p = principal_from(request, settings)
    if p is None:
        raise ApiError(
            401, "unauthorized", "no trusted principal: the request did not come through the SSO proxy"
        )
    ws = await _workspace(uow, workspace)
    member = await idp.provision_login(
        uow,
        ws,
        provider="kerberos",
        email=p.email,
        display_name=p.handle_hint,
        handle_hint=p.handle_hint,
        groups=p.groups,
    )
    resp = RedirectResponse(_safe_next(request, next), status_code=302)
    set_session(resp, settings, member.person_id, secure=_secure(request))
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
    return await signin.ldap_member(
        uow,
        ldap,
        _attempt_key(request, body.username),
        workspace=body.workspace,
        username=body.username,
        password=body.password,
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
    tok = mint_api_token(uow, member, "cli (ldap)", ["capture", "read"], 90)
    await uow.commit()
    return {**tok, "handle": member.handle}


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
    rows = await signin.active_workspaces(uow, person.id)
    return {
        "person_id": person.id,
        "display_name": person.display_name,
        "workspaces": [{"slug": s, "handle": h} for s, _, h in rows],
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
    rows = await signin.active_workspaces(uow, person.id)
    return {"data": [{"slug": s, "name": n, "handle": h} for s, n, h in rows], "next_cursor": None}


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
    member = await signin.active_member(uow, workspace, sess[0])
    tok = mint_api_token(uow, member, "cli (browser)", ["capture", "read"], 90)
    await uow.commit()
    sep = "&" if "?" in callback else "?"
    return RedirectResponse(
        f"{callback}{sep}{urlencode({'token': tok['token'], 'state': state, 'handle': member.handle})}",
        status_code=302,
    )


__all__ = ["MAX_FAILURES", "SESSION_COOKIE", "WINDOW", "router"]
