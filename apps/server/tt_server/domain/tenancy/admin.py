"""Tenancy administration behind `/people`, `/teams`, `/reporting-lines` and `/context-notes`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import ReportingKind, ScopeType, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, team_subtree_ids
from tt_server.domain.errors import Conflict, Invalid, NotFound


async def list_people(uow: UnitOfWork, ws_id: str) -> list[tuple[m.WorkspaceMember, m.Person]]:
    """Live members of the workspace with their person rows, by handle."""
    rows = (
        await uow.session.execute(
            select(m.WorkspaceMember, m.Person)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.deleted_at.is_(None))
            .order_by(m.WorkspaceMember.handle)
        )
    ).all()
    return [(mem, p) for mem, p in rows]


async def create_team(uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any]) -> m.Team:
    """Create a team."""
    team = m.Team(id=new_id(), workspace_id=ws.id, **data)
    uow.session.add(team)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.TEAM,
        entity_id=team.id,
        event_type="team.created",
        payload={"after": {"slug": team.slug}},
    )
    return team


async def list_teams(uow: UnitOfWork, ws_id: str) -> list[m.Team]:
    """Live teams of the workspace."""
    return list(
        (
            await uow.session.scalars(
                select(m.Team).where(m.Team.workspace_id == ws_id, m.Team.deleted_at.is_(None))
            )
        ).all()
    )


async def add_membership(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, team_id: str, data: dict[str, Any]
) -> m.Membership:
    """Put a person on a team with a role and optional allocation."""
    team = await uow.session.scalar(select(m.Team).where(m.Team.id == team_id, m.Team.workspace_id == ws.id))
    if team is None:
        raise NotFound("not_found", "team not found")
    row = m.Membership(id=new_id(), team_id=team.id, joined_at=utcnow(), **data)
    uow.session.add(row)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.TEAM,
        entity_id=team.id,
        event_type="membership.changed",
        payload={"after": {"person_id": data["person_id"], "role": data["role"].value}},
    )
    return row


async def team_memberships(
    uow: UnitOfWork, ws_id: str, team_id: str, *, effective: bool
) -> list[m.Membership]:
    """Current memberships of a team; `effective` includes every descendant team."""
    team_ids = await team_subtree_ids(uow.session, ws_id, {team_id}) if effective else {team_id}
    return list(
        (
            await uow.session.scalars(
                select(m.Membership).where(m.Membership.team_id.in_(team_ids), m.Membership.left_at.is_(None))
            )
        ).all()
    )


async def list_reporting_lines(uow: UnitOfWork, ws_id: str) -> list[m.ReportingLine]:
    """Live reporting lines of the workspace."""
    return list(
        (
            await uow.session.scalars(
                select(m.ReportingLine).where(
                    m.ReportingLine.workspace_id == ws_id, m.ReportingLine.deleted_at.is_(None)
                )
            )
        ).all()
    )


async def add_reporting_line(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any]
) -> m.ReportingLine:
    """Add a reporting line; scope must match its type, no self-reporting, one primary line manager per person."""
    scope_type, scope_id = data["scope_type"], data["scope_id"]
    if (scope_type == ScopeType.NONE) != (scope_id is None):
        raise Invalid(
            "invalid_scope", "scope_id is required exactly when scope_type is not 'none'", pointer="/scope_id"
        )
    if data["person_id"] == data["manager_id"]:
        raise Invalid("invalid_line", "a person cannot report to themselves", pointer="/manager_id")
    if data["is_primary"] and data["kind"] == ReportingKind.LINE:
        taken = await uow.session.scalar(
            select(m.ReportingLine.id).where(
                m.ReportingLine.person_id == data["person_id"],
                m.ReportingLine.is_primary,
                m.ReportingLine.kind == ReportingKind.LINE,
                m.ReportingLine.deleted_at.is_(None),
            )
        )
        if taken:
            raise Conflict(
                "primary_exists", "person already has a primary line manager", pointer="/is_primary"
            )
    row = m.ReportingLine(id=new_id(), workspace_id=ws.id, **data)
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=data["person_id"],
        event_type="reporting_line.changed",
        payload={"after": {k: getattr(v, "value", v) for k, v in data.items()}},
    )
    return row


async def create_note(uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any]) -> m.ContextNote:
    """Create a context note authored by the actor."""
    row = m.ContextNote(id=new_id(), workspace_id=ws.id, author_id=actor_id, **data)
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.CONTEXT_NOTE,
        entity_id=row.id,
        event_type="context_note.created",
        payload={"after": {"title": row.title}},
    )
    return row


async def list_notes(
    uow: UnitOfWork, ws_id: str, *, scope_type: ScopeType | None, scope_id: str | None
) -> list[m.ContextNote]:
    """Live context notes, pinned first then newest, optionally for one scope."""
    stmt = select(m.ContextNote).where(
        m.ContextNote.workspace_id == ws_id, m.ContextNote.deleted_at.is_(None)
    )
    if scope_type:
        stmt = stmt.where(m.ContextNote.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(m.ContextNote.scope_id == scope_id)
    return list(
        (
            await uow.session.scalars(
                stmt.order_by(m.ContextNote.pinned.desc(), m.ContextNote.created_at.desc())
            )
        ).all()
    )
