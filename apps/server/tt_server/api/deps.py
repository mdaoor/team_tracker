"""Request dependencies: unit of work, workspace resolution, actor authentication."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import utcnow

from tt_server.acl.context import AclContext, load_context
from tt_server.acl.permissions import TOKEN_SCOPES
from tt_server.api.errors import ApiError
from tt_server.api.session import SLIDE_AFTER_SECONDS, check_csrf, read_session
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.sso import principal_from

TOKEN_PREFIX = "tt_"  # noqa: S105


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_uow(request: Request) -> AsyncIterator[UnitOfWork]:
    async with UnitOfWork(get_session_factory(request)) as uow:
        yield uow


Uow = Annotated[UnitOfWork, Depends(get_uow)]


@dataclass
class Actor:
    ws: m.Workspace
    member: m.WorkspaceMember
    person: m.Person
    acl: AclContext

    @property
    def handle(self) -> str:
        return self.member.handle


async def _authenticate(
    request: Request,
    uow: UnitOfWork,
    authorization: str | None,
    debug_actor: str | None,
    workspace: m.Workspace | None,
) -> tuple[m.Person, m.ApiToken | None]:
    session = uow.session
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        row = await session.scalar(
            select(m.ApiToken).where(m.ApiToken.hash == hash_token(token), m.ApiToken.deleted_at.is_(None))
        )
        if row is None or (row.expires_at and row.expires_at < utcnow()):
            raise ApiError(401, "invalid_token", "invalid or expired token")
        if workspace is not None and row.workspace_id != workspace.id:
            raise ApiError(401, "invalid_token", "token is not valid for this workspace")
        if row.last_used_at is None or utcnow() - row.last_used_at > timedelta(minutes=1):
            row.last_used_at = utcnow()
            await uow.commit_bookkeeping()  # not a domain fact: no Event, and the guard flag is reset
        person = await session.get(m.Person, row.person_id)
        assert person is not None  # noqa: S101
        return person, row
    if (sso := principal_from(request, request.app.state.settings)) is not None and workspace is not None:
        # proxy-validated Kerberos principal: an existing, active member only (provisioning happens at /auth/sso)
        res = await session.execute(
            select(m.Person)
            .join(m.WorkspaceMember, m.WorkspaceMember.person_id == m.Person.id)
            .where(
                m.WorkspaceMember.workspace_id == workspace.id,
                func.lower(m.Person.email) == sso.email,
                m.WorkspaceMember.deleted_at.is_(None),
            )
        )
        if (found := res.scalar_one_or_none()) is not None and found.deleted_at is None:
            return found, None
        raise ApiError(401, "unauthorized", "the signed-in principal is not a member of this workspace")
    if debug_actor and request.app.state.settings.dev_auth:
        handle = debug_actor if debug_actor.startswith("@") else "@" + debug_actor
        if workspace is None:
            raise ApiError(401, "unauthorized", "X-Debug-Actor needs a workspace")
        member = await session.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == workspace.id, m.WorkspaceMember.handle == handle
            )
        )
        if member is None:
            raise ApiError(401, "unauthorized", "unknown debug actor")
        person = await session.get(m.Person, member.person_id)
        assert person is not None  # noqa: S101
        return person, None
    sess = read_session(request, request.app.state.settings)
    if sess is not None:
        check_csrf(request)
        person = await session.get(m.Person, sess[0])
        if person is not None and person.deleted_at is None:
            if sess[1] > SLIDE_AFTER_SECONDS:
                request.state.refresh_session = person.id
            return person, None
    raise ApiError(401, "unauthorized", "authentication required")


async def current_actor(
    request: Request,
    uow: Uow,
    ws: str,
    authorization: Annotated[str | None, Header()] = None,
    x_debug_actor: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve the workspace from the path and authenticate the caller as one of its members."""
    workspace = await uow.session.scalar(
        select(m.Workspace).where(m.Workspace.slug == ws, m.Workspace.deleted_at.is_(None))
    )
    if workspace is None:
        raise ApiError(404, "not_found", "workspace not found")
    person, token = await _authenticate(request, uow, authorization, x_debug_actor, workspace)
    member = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == workspace.id,
            m.WorkspaceMember.person_id == person.id,
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )
    if member is None:
        raise ApiError(404, "not_found", "workspace not found")  # 404 for non-members (ADR-0008)
    scopes = tuple(token.scopes) if token is not None else None
    acl = await load_context(uow.session, workspace.id, person.id, scopes)
    if scopes is not None and not scope_allows(
        scopes, request.method, request.url.path, request.query_params.get("person")
    ):
        raise ApiError(403, "insufficient_scope", f"token scopes {list(scopes)} do not allow this request")
    await uow.commit_bookkeeping()  # end the read transaction: release the connection / savepoint early
    uow.context["actor_id"] = person.id
    uow.context["acl"] = acl
    return Actor(workspace, member, person, acl)


_CAPTURE_PATHS = ("/capture/", "/context-pack")
_ADMIN_PATHS = ("/role-assignments", "/grants", "/idp/", "/members", "/reporting-lines", "/teams")


def scope_allows(scopes: tuple[str, ...], method: str, path: str, person: str | None = None) -> bool:
    """Token scopes are cumulative: capture < read < manage < admin (Phase 4 §4.5)."""
    level = max((TOKEN_SCOPES.index(s) for s in scopes if s in TOKEN_SCOPES), default=-1)
    rel = path.split("/w/", 1)[-1].split("/", 1)[-1] if "/w/" in path else path
    rel = "/" + rel.lstrip("/")
    if level >= 3:  # admin: everything the owner may do
        return True
    if level == 2:  # manage: ... except administering people, roles, grants and the identity provider
        return not rel.startswith(_ADMIN_PATHS)
    if level == 1 and method in ("GET", "HEAD"):
        return True
    if level >= 0:
        if rel.startswith(_CAPTURE_PATHS):
            return True
        if rel == "/activities" and method == "GET" and person in (None, "me"):
            return True
        if rel.startswith("/daily-logs") and (method == "GET" or rel.endswith("/confirm")):
            return True
        if level == 1 and method == "GET":
            return True
    return False


CurrentActor = Annotated[Actor, Depends(current_actor)]


def page(rows: list[Any], limit: int, key: str = "id") -> dict[str, Any]:
    """Cursor envelope: `rows` must contain `limit + 1` entries when more data exists."""
    more = len(rows) > limit
    data = rows[:limit]
    return {"data": data, "next_cursor": (getattr(data[-1], key) if more else None)}
