"""Interactive sign-in logic: failed-attempt throttling, LDAP and dev sign-in, workspace lookups, redirect safety."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, select
from tt_schema.base import new_id
from tt_schema.enums import MemberStatus

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.errors import NotFound, Throttled, Unauthenticated
from tt_server.integrations import idp

MAX_FAILURES, WINDOW = 5, 900.0  # failures per (client address, username) within WINDOW before backoff starts


async def throttle(uow: UnitOfWork, key: str) -> None:
    """Exponential backoff after MAX_FAILURES: 30 s, 1 min, 2 min ... capped at 15 min, per address+username (so one address
    cannot lock a user out for everybody, and the counters live in the database, shared by every worker)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=WINDOW)
    rows = (
        await uow.session.scalars(
            select(m.LoginAttempt.created_at)
            .where(m.LoginAttempt.key == key, m.LoginAttempt.created_at >= cutoff)
            .order_by(m.LoginAttempt.created_at)
        )
    ).all()
    if len(rows) >= MAX_FAILURES:
        wait = min(30 * 2 ** (len(rows) - MAX_FAILURES), WINDOW)
        remaining = wait - (datetime.now(UTC) - rows[-1]).total_seconds()
        if remaining > 0:
            raise Throttled(
                "too_many_attempts",
                "too many failed sign-ins; try again later",
                details={"retry_after": int(remaining) + 1},
            )


async def record_failure(uow: UnitOfWork, key: str) -> None:
    """Count one failed sign-in against the attempt key (bookkeeping: no event)."""
    uow.session.add(m.LoginAttempt(id=new_id(), key=key))
    await uow.commit_bookkeeping()


async def record_success(uow: UnitOfWork, key: str) -> None:
    """Forget the failed attempts of the attempt key after a successful sign-in."""
    await uow.session.execute(delete(m.LoginAttempt).where(m.LoginAttempt.key == key))
    await uow.commit_bookkeeping()


def safe_next(nxt: str | None, web_origin: str | None) -> str:
    """Only same-site paths or the configured web origin; anything else falls back to `/`."""
    if not nxt:
        return "/"
    if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    if (
        web_origin
        and urlparse(nxt).netloc == urlparse(web_origin).netloc
        and urlparse(nxt).scheme in {"http", "https"}
    ):
        return nxt
    return "/"


async def workspace_by_slug(uow: UnitOfWork, slug: str) -> m.Workspace:
    """Load a live workspace by slug or raise NotFound."""
    ws = await uow.session.scalar(
        select(m.Workspace).where(m.Workspace.slug == slug, m.Workspace.deleted_at.is_(None))
    )
    if ws is None:
        raise NotFound("not_found", "workspace not found")
    return ws


async def dev_member(uow: UnitOfWork, workspace: str, handle: str) -> m.WorkspaceMember:
    """The existing member a development sign-in names (no identity provider involved)."""
    ws = await workspace_by_slug(uow, workspace)
    handle = handle if handle.startswith("@") else "@" + handle
    member = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id,
            m.WorkspaceMember.handle == handle,
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )
    if member is None:
        raise Unauthenticated("unauthorized", "unknown member")
    return member


async def ldap_member(
    uow: UnitOfWork, ldap: Any, key: str, *, workspace: str, username: str, password: str
) -> m.WorkspaceMember:
    """Authenticate against the directory (throttled per `key`) and provision/refresh the workspace member."""
    await throttle(uow, key)
    user = await ldap.authenticate(username, password)
    if user is None:
        await record_failure(uow, key)
        raise Unauthenticated("invalid_credentials", "invalid username or password")
    await record_success(uow, key)
    ws = await workspace_by_slug(uow, workspace)
    if not user.email:
        raise Unauthenticated("invalid_credentials", "the directory account has no e-mail address")
    return await idp.provision_login(
        uow,
        ws,
        provider="ldap",
        email=user.email,
        display_name=user.display_name,
        handle_hint=user.sam,
        groups=user.groups,
    )


async def active_workspaces(uow: UnitOfWork, person_id: str) -> list[tuple[str, str, str]]:
    """(slug, name, handle) of every workspace where the person is an active member."""
    rows = (
        await uow.session.execute(
            select(m.Workspace.slug, m.Workspace.name, m.WorkspaceMember.handle)
            .join(m.WorkspaceMember, m.WorkspaceMember.workspace_id == m.Workspace.id)
            .where(m.WorkspaceMember.person_id == person_id, m.WorkspaceMember.status == MemberStatus.ACTIVE)
        )
    ).all()
    return [(s, n, h) for s, n, h in rows]


async def active_member(uow: UnitOfWork, workspace: str, person_id: str) -> m.WorkspaceMember:
    """The person's active membership of the workspace, or NotFound (the workspace's existence is not revealed)."""
    ws = await workspace_by_slug(uow, workspace)
    member = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id,
            m.WorkspaceMember.person_id == person_id,
            m.WorkspaceMember.status == MemberStatus.ACTIVE,
        )
    )
    if member is None:
        raise NotFound("not_found", "workspace not found")
    return member
