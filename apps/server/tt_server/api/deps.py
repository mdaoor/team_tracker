"""Request dependencies: unit of work, workspace resolution, actor authentication."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import utcnow

from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork

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
            await session.commit()
        person = await session.get(m.Person, row.person_id)
        assert person is not None  # noqa: S101
        return person, row
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
    person, _ = await _authenticate(request, uow, authorization, x_debug_actor, workspace)
    member = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == workspace.id,
            m.WorkspaceMember.person_id == person.id,
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )
    if member is None:
        raise ApiError(404, "not_found", "workspace not found")  # 404 for non-members (ADR-0008)
    await uow.session.commit()  # end the read transaction: release the connection / savepoint early
    uow.context["actor_id"] = person.id
    return Actor(workspace, member, person)  # TODO(P4-ACL): permission checks beyond membership


CurrentActor = Annotated[Actor, Depends(current_actor)]


def page(rows: list[Any], limit: int, key: str = "id") -> dict[str, Any]:
    """Cursor envelope: `rows` must contain `limit + 1` entries when more data exists."""
    more = len(rows) > limit
    data = rows[:limit]
    return {"data": data, "next_cursor": (getattr(data[-1], key) if more else None)}
