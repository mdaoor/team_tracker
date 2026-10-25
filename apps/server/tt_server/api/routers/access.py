"""Roles, assignments, grants, project membership, scopes and the event feed (Phase 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    EventSource,
    GrantLevel,
    ProjectRole,
    ScopeType,
    SubjectType,
    TargetType,
)

from tt_server.acl import grants as grants_svc
from tt_server.acl.permissions import ROLES
from tt_server.acl.roles import assign, ensure_builtin_roles
from tt_server.acl.visibility import redact_event, visible
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _project
from tt_server.db import models as m
from tt_server.domain.common import emit, row_dict

router = APIRouter(prefix="/api/v1/w/{ws}")


@router.get("/roles", tags=["access"])
async def list_roles(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    await ensure_builtin_roles(uow.session)
    rows = (
        await uow.session.scalars(
            select(m.Role)
            .where((m.Role.workspace_id.is_(None)) | (m.Role.workspace_id == actor.ws.id))
            .order_by(m.Role.key)
        )
    ).all()
    await uow.commit()
    return {
        "data": [{**row_dict(r), "scoped": ROLES[r.key].scoped if r.key in ROLES else True} for r in rows],
        "next_cursor": None,
    }


class RoleAssignmentIn(BaseModel):
    handle: str
    role: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None


async def _member_person(uow: Uow, ws_id: str, handle: str) -> str:
    pid = await uow.session.scalar(
        select(m.WorkspaceMember.person_id).where(
            m.WorkspaceMember.workspace_id == ws_id,
            m.WorkspaceMember.handle == (handle if handle.startswith("@") else "@" + handle),
        )
    )
    if pid is None:
        raise ApiError(404, "not_found", f"unknown member {handle}", pointer="/handle")
    return str(pid)


@router.post("/role-assignments", status_code=201, tags=["access"], dependencies=[needs("role.admin")])
async def assign_role(body: RoleAssignmentIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    uow.context["actor_id"] = actor.person.id
    spec = ROLES.get(body.role)
    if spec is not None and body.scope_type != ScopeType.WORKSPACE and not spec.scoped:
        raise ApiError(
            400, "not_scopable", f"role {body.role!r} is workspace-wide only", pointer="/scope_type"
        )
    scope_id: str | None
    if body.scope_type in (ScopeType.PROJECT, ScopeType.PRODUCT, ScopeType.TEAM):
        if not body.scope_id:
            raise ApiError(
                400, "scope_required", "scope_id is required for scoped roles", pointer="/scope_id"
            )
        scope_id = await _resolve_scope(uow, actor.ws.id, body.scope_type, body.scope_id)
    else:
        scope_id = body.scope_id
    pid = await _member_person(uow, actor.ws.id, body.handle)
    row = await assign(uow.session, actor.ws.id, pid, body.role, body.scope_type, scope_id)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=pid,
        event_type="role_assignment.created",
        payload={"after": {"role": body.role, "scope": body.scope_type.value, "scope_id": scope_id}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return row_dict(row)


async def _resolve_scope(uow: Uow, ws_id: str, scope_type: ScopeType, ref: str) -> str:
    if scope_type == ScopeType.PROJECT:
        return (await _project(uow, ws_id, ref)).id
    model: Any = m.Product if scope_type == ScopeType.PRODUCT else m.Team
    key_col = model.key if scope_type == ScopeType.PRODUCT else model.slug
    row = await uow.session.scalar(
        select(model).where(model.workspace_id == ws_id, (model.id == ref) | (key_col == ref))
    )
    if row is None:
        raise ApiError(404, "not_found", f"{scope_type.value} {ref!r} not found", pointer="/scope_id")
    return str(row.id)


@router.get("/role-assignments", tags=["access"], dependencies=[needs("role.admin")])
async def list_assignments(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.execute(
            select(m.RoleAssignment, m.Role.key, m.WorkspaceMember.handle)
            .join(m.Role, m.Role.id == m.RoleAssignment.role_id)
            .join(
                m.WorkspaceMember,
                (m.WorkspaceMember.person_id == m.RoleAssignment.person_id)
                & (m.WorkspaceMember.workspace_id == actor.ws.id),
            )
            .where(m.RoleAssignment.workspace_id == actor.ws.id, m.RoleAssignment.deleted_at.is_(None))
        )
    ).all()
    return {"data": [{**row_dict(a), "role": rk, "handle": h} for a, rk, h in rows], "next_cursor": None}


@router.delete(
    "/role-assignments/{aid}", status_code=204, tags=["access"], dependencies=[needs("role.admin")]
)
async def revoke_role(aid: str, actor: CurrentActor, uow: Uow) -> Response:
    row = await uow.session.get(m.RoleAssignment, aid)
    if row is None or row.deleted_at is not None or row.workspace_id != actor.ws.id:
        raise ApiError(404, "not_found", "assignment not found")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=row.person_id,
        event_type="role_assignment.deleted",
        payload={"before": {"assignment_id": row.id}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return Response(status_code=204)


class GrantIn(BaseModel):
    subject_type: SubjectType
    subject: str  # handle, team slug or role key
    object_type: TargetType
    object_id: str  # id or key
    level: GrantLevel = GrantLevel.READ
    inherit_to_children: bool = True
    expires_at: datetime | None = None


@router.post("/grants", status_code=201, tags=["access"], dependencies=[needs("grant.write")])
async def create_grant(body: GrantIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await grants_svc.create_manual(uow, actor.ws, body, granted_by=actor.person.id)
    await uow.commit()
    return row_dict(row)


@router.get("/grants", tags=["access"], dependencies=[needs("grant.write")])
async def list_grants(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.AccessGrant).where(
                m.AccessGrant.workspace_id == actor.ws.id, m.AccessGrant.deleted_at.is_(None)
            )
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.delete("/grants/{gid}", status_code=204, tags=["access"], dependencies=[needs("grant.write")])
async def delete_grant(gid: str, actor: CurrentActor, uow: Uow) -> Response:
    row = await uow.session.scalar(
        select(m.AccessGrant).where(
            m.AccessGrant.id == gid,
            m.AccessGrant.workspace_id == actor.ws.id,
            m.AccessGrant.deleted_at.is_(None),
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "grant not found")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=row.object_type,
        entity_id=row.object_id,
        event_type="grant.deleted",
        payload={"before": {"grant_id": row.id}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return Response(status_code=204)


class ProjectMemberIn(BaseModel):
    handle: str
    role: ProjectRole = ProjectRole.CONTRIBUTOR
    allocation_pct: int | None = None


@router.post(
    "/projects/{key}/members",
    status_code=201,
    tags=["access"],
    dependencies=[needs("project_member.write", project="key")],
)
async def add_project_member(
    key: str, body: ProjectMemberIn, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    project = await _project(uow, actor.ws.id, key)
    pid = await _member_person(uow, actor.ws.id, body.handle)
    existing = await uow.session.scalar(
        select(m.ProjectMember).where(
            m.ProjectMember.project_id == project.id,
            m.ProjectMember.person_id == pid,
            m.ProjectMember.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise ApiError(409, "member_exists", "already a project member", pointer="/handle")
    row = m.ProjectMember(
        id=new_id(), project_id=project.id, person_id=pid, role=body.role, allocation_pct=body.allocation_pct
    )
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PROJECT,
        entity_id=project.id,
        event_type="project_member.added",
        payload={"after": {"person_id": pid, "role": body.role.value}},
        source=EventSource.OVERLAY,
    )
    await grants_svc.on_project_member_added(uow, actor.ws, project, pid)
    await uow.commit()
    return row_dict(row)


@router.delete(
    "/projects/{key}/members/{handle}",
    status_code=204,
    tags=["access"],
    dependencies=[needs("project_member.write", project="key")],
)
async def remove_project_member(key: str, handle: str, actor: CurrentActor, uow: Uow) -> Response:
    project = await _project(uow, actor.ws.id, key)
    pid = await _member_person(uow, actor.ws.id, handle)
    row = await uow.session.scalar(
        select(m.ProjectMember).where(
            m.ProjectMember.project_id == project.id,
            m.ProjectMember.person_id == pid,
            m.ProjectMember.deleted_at.is_(None),
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "not a project member")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PROJECT,
        entity_id=project.id,
        event_type="project_member.removed",
        payload={"before": {"person_id": pid}},
        source=EventSource.OVERLAY,
    )
    await grants_svc.on_project_member_removed(uow, actor.ws, project, pid)
    await uow.commit()
    return Response(status_code=204)


@router.get("/projects/{key}/members", tags=["access"])
async def list_project_members(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    project = await _project(uow, actor.ws.id, key)
    rows = (
        await uow.session.execute(
            select(m.ProjectMember, m.WorkspaceMember.handle)
            .join(
                m.WorkspaceMember,
                (m.WorkspaceMember.person_id == m.ProjectMember.person_id)
                & (m.WorkspaceMember.workspace_id == actor.ws.id),
            )
            .where(m.ProjectMember.project_id == project.id, m.ProjectMember.deleted_at.is_(None))
        )
    ).all()
    return {"data": [{**row_dict(r), "handle": h} for r, h in rows], "next_cursor": None}


@router.get("/me/scopes", tags=["access"])
async def my_scopes(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Scopes with any readable object: the scope selector only lists these (UX §6)."""
    out: list[dict[str, Any]] = [
        {"type": "workspace", "id": actor.ws.id, "key": actor.ws.slug, "name": actor.ws.name}
    ]
    for p in (
        await uow.session.scalars(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == actor.ws.id,
                    m.Project.deleted_at.is_(None),
                    m.Project.kind != "inbox",
                ),
                actor.acl,
                m.Project,
            ).order_by(m.Project.key)
        )
    ).all():
        out.append({"type": "project", "id": p.id, "key": p.key, "name": p.name})
    for pr in (
        await uow.session.scalars(
            visible(
                select(m.Product).where(
                    m.Product.workspace_id == actor.ws.id, m.Product.deleted_at.is_(None)
                ),
                actor.acl,
                m.Product,
            ).order_by(m.Product.key)
        )
    ).all():
        out.append({"type": "product", "id": pr.id, "key": pr.key, "name": pr.name})
    teams = (
        await uow.session.scalars(
            select(m.Team).where(m.Team.workspace_id == actor.ws.id, m.Team.deleted_at.is_(None))
        )
    ).all()
    for t in teams:
        if actor.acl.is_admin or actor.acl.manager_all or t.id in actor.acl.team_ancestor_ids:
            out.append({"type": "team", "id": t.id, "key": t.slug, "name": t.name})
    if actor.acl.is_admin or actor.acl.manager_all or actor.acl.managed_ids:
        for pid in [actor.person.id, *actor.acl.managed_ids]:
            handle = await uow.session.scalar(
                select(m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id, m.WorkspaceMember.person_id == pid
                )
            )
            out.append({"type": "person", "id": pid, "key": handle, "name": handle})
    else:
        out.append(
            {"type": "person", "id": actor.person.id, "key": actor.member.handle, "name": actor.member.handle}
        )
    return {"data": out, "next_cursor": None}


@router.get("/events", tags=["access"])
async def list_events(
    actor: CurrentActor,
    uow: Uow,
    entity_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """The audit feed, filtered by visibility; `raw_input` only reaches the actor and their line managers."""
    stmt = visible(select(m.Event).where(m.Event.workspace_id == actor.ws.id), actor.acl, m.Event)
    if entity_id:
        stmt = stmt.where(m.Event.entity_id == entity_id)
    if event_type:
        stmt = stmt.where(m.Event.event_type == event_type)
    rows = (
        await uow.session.scalars(
            stmt.order_by(m.Event.occurred_at.desc(), m.Event.id.desc()).limit(min(limit, 500))
        )
    ).all()
    return {"data": [redact_event(actor.acl, row_dict(e)) for e in rows], "next_cursor": None}
