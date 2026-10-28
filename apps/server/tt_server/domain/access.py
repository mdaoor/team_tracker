"""Access administration: role assignments, grants, project membership, readable scopes and the event feed.

The router parses and authorises; these functions validate, write and emit. They never commit: the caller owns the
unit of work (ADR-0047).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, ProjectRole, ScopeType, TargetType

from tt_server.acl import grants as grants_svc
from tt_server.acl.context import AclContext
from tt_server.acl.permissions import ROLES
from tt_server.acl.roles import assign, ensure_builtin_roles
from tt_server.acl.visibility import redact_event, visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, row_dict
from tt_server.domain.errors import Conflict, Invalid, NotFound
from tt_server.domain.lookup import member_person_id, resolve_scope_id, visible_project


async def list_roles(uow: UnitOfWork, ws_id: str) -> list[dict[str, Any]]:
    """Built-in and workspace roles, flagged with whether they can be scoped below the workspace."""
    await ensure_builtin_roles(uow.session)
    rows = (
        await uow.session.scalars(
            select(m.Role)
            .where((m.Role.workspace_id.is_(None)) | (m.Role.workspace_id == ws_id))
            .order_by(m.Role.key)
        )
    ).all()
    return [{**row_dict(r), "scoped": ROLES[r.key].scoped if r.key in ROLES else True} for r in rows]


async def assign_role(
    uow: UnitOfWork,
    ws_id: str,
    actor_id: str,
    *,
    handle: str,
    role: str,
    scope_type: ScopeType,
    scope_id: str | None,
) -> m.RoleAssignment:
    """Give a member a role (optionally scoped to a project, product or team) and record the event."""
    uow.context["actor_id"] = actor_id
    spec = ROLES.get(role)
    if spec is not None and scope_type != ScopeType.WORKSPACE and not spec.scoped:
        raise Invalid("not_scopable", f"role {role!r} is workspace-wide only", pointer="/scope_type")
    resolved: str | None
    if scope_type in (ScopeType.PROJECT, ScopeType.PRODUCT, ScopeType.TEAM):
        if not scope_id:
            raise Invalid("scope_required", "scope_id is required for scoped roles", pointer="/scope_id")
        resolved = await resolve_scope_id(uow, ws_id, scope_type, scope_id)
    else:
        resolved = scope_id
    pid = await member_person_id(uow, ws_id, handle)
    row = await assign(uow.session, ws_id, pid, role, scope_type, resolved)
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=pid,
        event_type="role_assignment.created",
        payload={"after": {"role": role, "scope": scope_type.value, "scope_id": resolved}},
        source=EventSource.OVERLAY,
    )
    return row


async def list_assignments(uow: UnitOfWork, ws_id: str) -> list[dict[str, Any]]:
    """Live role assignments of the workspace with the role key and member handle."""
    rows = (
        await uow.session.execute(
            select(m.RoleAssignment, m.Role.key, m.WorkspaceMember.handle)
            .join(m.Role, m.Role.id == m.RoleAssignment.role_id)
            .join(
                m.WorkspaceMember,
                (m.WorkspaceMember.person_id == m.RoleAssignment.person_id)
                & (m.WorkspaceMember.workspace_id == ws_id),
            )
            .where(m.RoleAssignment.workspace_id == ws_id, m.RoleAssignment.deleted_at.is_(None))
        )
    ).all()
    return [{**row_dict(a), "role": rk, "handle": h} for a, rk, h in rows]


async def revoke_assignment(uow: UnitOfWork, ws_id: str, actor_id: str, assignment_id: str) -> None:
    """Soft-delete a role assignment of the workspace and record the event."""
    row = await uow.session.get(m.RoleAssignment, assignment_id)
    if row is None or row.deleted_at is not None or row.workspace_id != ws_id:
        raise NotFound("not_found", "assignment not found")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=row.person_id,
        event_type="role_assignment.deleted",
        payload={"before": {"assignment_id": row.id}},
        source=EventSource.OVERLAY,
    )


async def list_grants(uow: UnitOfWork, ws_id: str) -> list[dict[str, Any]]:
    """Live manual access grants of the workspace."""
    rows = (
        await uow.session.scalars(
            select(m.AccessGrant).where(
                m.AccessGrant.workspace_id == ws_id, m.AccessGrant.deleted_at.is_(None)
            )
        )
    ).all()
    return [row_dict(r) for r in rows]


async def revoke_grant(uow: UnitOfWork, ws_id: str, actor_id: str, grant_id: str) -> None:
    """Soft-delete an access grant of the workspace and record the event."""
    row = await uow.session.scalar(
        select(m.AccessGrant).where(
            m.AccessGrant.id == grant_id,
            m.AccessGrant.workspace_id == ws_id,
            m.AccessGrant.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFound("not_found", "grant not found")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=row.object_type,
        entity_id=row.object_id,
        event_type="grant.deleted",
        payload={"before": {"grant_id": row.id}},
        source=EventSource.OVERLAY,
    )


async def _live_membership(uow: UnitOfWork, project_id: str, person_id: str) -> m.ProjectMember | None:
    return await uow.session.scalar(
        select(m.ProjectMember).where(
            m.ProjectMember.project_id == project_id,
            m.ProjectMember.person_id == person_id,
            m.ProjectMember.deleted_at.is_(None),
        )
    )


async def add_project_member(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor_id: str,
    *,
    project_key: str,
    handle: str,
    role: ProjectRole,
    allocation_pct: int | None,
) -> m.ProjectMember:
    """Add a member to a project, emit the event and let the grants engine react to the new membership."""
    project = await visible_project(uow, ws.id, project_key)
    pid = await member_person_id(uow, ws.id, handle)
    if await _live_membership(uow, project.id, pid) is not None:
        raise Conflict("member_exists", "already a project member", pointer="/handle")
    row = m.ProjectMember(
        id=new_id(), project_id=project.id, person_id=pid, role=role, allocation_pct=allocation_pct
    )
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PROJECT,
        entity_id=project.id,
        event_type="project_member.added",
        payload={"after": {"person_id": pid, "role": role.value}},
        source=EventSource.OVERLAY,
    )
    await grants_svc.on_project_member_added(uow, ws, project, pid)
    return row


async def remove_project_member(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, *, project_key: str, handle: str
) -> None:
    """Remove a member from a project, emit the event and let the grants engine react."""
    project = await visible_project(uow, ws.id, project_key)
    pid = await member_person_id(uow, ws.id, handle)
    row = await _live_membership(uow, project.id, pid)
    if row is None:
        raise NotFound("not_found", "not a project member")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PROJECT,
        entity_id=project.id,
        event_type="project_member.removed",
        payload={"before": {"person_id": pid}},
        source=EventSource.OVERLAY,
    )
    await grants_svc.on_project_member_removed(uow, ws, project, pid)


async def list_project_members(uow: UnitOfWork, ws_id: str, project_key: str) -> list[dict[str, Any]]:
    """Live members of a visible project with their handles."""
    project = await visible_project(uow, ws_id, project_key)
    rows = (
        await uow.session.execute(
            select(m.ProjectMember, m.WorkspaceMember.handle)
            .join(
                m.WorkspaceMember,
                (m.WorkspaceMember.person_id == m.ProjectMember.person_id)
                & (m.WorkspaceMember.workspace_id == ws_id),
            )
            .where(m.ProjectMember.project_id == project.id, m.ProjectMember.deleted_at.is_(None))
        )
    ).all()
    return [{**row_dict(r), "handle": h} for r, h in rows]


async def readable_scopes(
    uow: UnitOfWork, ws: m.Workspace, person: m.Person, member: m.WorkspaceMember, acl: AclContext
) -> list[dict[str, Any]]:
    """Scopes with any readable object: the scope selector only lists these (UX §6)."""
    s = uow.session
    out: list[dict[str, Any]] = [{"type": "workspace", "id": ws.id, "key": ws.slug, "name": ws.name}]
    for p in (
        await s.scalars(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == ws.id,
                    m.Project.deleted_at.is_(None),
                    m.Project.kind != "inbox",
                ),
                acl,
                m.Project,
            ).order_by(m.Project.key)
        )
    ).all():
        out.append({"type": "project", "id": p.id, "key": p.key, "name": p.name})
    for pr in (
        await s.scalars(
            visible(
                select(m.Product).where(m.Product.workspace_id == ws.id, m.Product.deleted_at.is_(None)),
                acl,
                m.Product,
            ).order_by(m.Product.key)
        )
    ).all():
        out.append({"type": "product", "id": pr.id, "key": pr.key, "name": pr.name})
    teams = (
        await s.scalars(select(m.Team).where(m.Team.workspace_id == ws.id, m.Team.deleted_at.is_(None)))
    ).all()
    for t in teams:
        if acl.is_admin or acl.manager_all or t.id in acl.team_ancestor_ids:
            out.append({"type": "team", "id": t.id, "key": t.slug, "name": t.name})
    if acl.is_admin or acl.manager_all or acl.managed_ids:
        for pid in [person.id, *acl.managed_ids]:
            handle = await s.scalar(
                select(m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.person_id == pid
                )
            )
            out.append({"type": "person", "id": pid, "key": handle, "name": handle})
    else:
        out.append({"type": "person", "id": person.id, "key": member.handle, "name": member.handle})
    return out


async def event_feed(
    uow: UnitOfWork,
    ws_id: str,
    acl: AclContext,
    *,
    entity_id: str | None,
    event_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """The audit feed, filtered by visibility; `raw_input` only reaches the actor and their line managers."""
    stmt = visible(select(m.Event).where(m.Event.workspace_id == ws_id), acl, m.Event)
    if entity_id:
        stmt = stmt.where(m.Event.entity_id == entity_id)
    if event_type:
        stmt = stmt.where(m.Event.event_type == event_type)
    rows = (
        await uow.session.scalars(
            stmt.order_by(m.Event.occurred_at.desc(), m.Event.id.desc()).limit(min(limit, 500))
        )
    ).all()
    return [redact_event(acl, row_dict(e)) for e in rows]
