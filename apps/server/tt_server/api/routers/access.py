"""Roles, assignments, grants, project membership, scopes and the event feed (Phase 4).

Thin HTTP layer: parse, authorise, call `domain.access`, commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel
from tt_schema.enums import (
    GrantLevel,
    ProjectRole,
    ScopeType,
    SubjectType,
    TargetType,
)

from tt_server.acl import grants as grants_svc
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.domain import access as svc
from tt_server.domain.common import row_dict

router = APIRouter(prefix="/api/v1/w/{ws}")


@router.get("/roles", tags=["access"])
async def list_roles(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    data = await svc.list_roles(uow, actor.ws.id)
    await uow.commit()
    return {"data": data, "next_cursor": None}


class RoleAssignmentIn(BaseModel):
    handle: str
    role: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None


@router.post("/role-assignments", status_code=201, tags=["access"], dependencies=[needs("role.admin")])
async def assign_role(body: RoleAssignmentIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await svc.assign_role(
        uow,
        actor.ws.id,
        actor.person.id,
        handle=body.handle,
        role=body.role,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )
    await uow.commit()
    return row_dict(row)


@router.get("/role-assignments", tags=["access"], dependencies=[needs("role.admin")])
async def list_assignments(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {"data": await svc.list_assignments(uow, actor.ws.id), "next_cursor": None}


@router.delete(
    "/role-assignments/{aid}", status_code=204, tags=["access"], dependencies=[needs("role.admin")]
)
async def revoke_role(aid: str, actor: CurrentActor, uow: Uow) -> Response:
    await svc.revoke_assignment(uow, actor.ws.id, actor.person.id, aid)
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
    return {"data": await svc.list_grants(uow, actor.ws.id), "next_cursor": None}


@router.delete("/grants/{gid}", status_code=204, tags=["access"], dependencies=[needs("grant.write")])
async def delete_grant(gid: str, actor: CurrentActor, uow: Uow) -> Response:
    await svc.revoke_grant(uow, actor.ws.id, actor.person.id, gid)
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
    row = await svc.add_project_member(
        uow,
        actor.ws,
        actor.person.id,
        project_key=key,
        handle=body.handle,
        role=body.role,
        allocation_pct=body.allocation_pct,
    )
    await uow.commit()
    return row_dict(row)


@router.delete(
    "/projects/{key}/members/{handle}",
    status_code=204,
    tags=["access"],
    dependencies=[needs("project_member.write", project="key")],
)
async def remove_project_member(key: str, handle: str, actor: CurrentActor, uow: Uow) -> Response:
    await svc.remove_project_member(uow, actor.ws, actor.person.id, project_key=key, handle=handle)
    await uow.commit()
    return Response(status_code=204)


@router.get("/projects/{key}/members", tags=["access"])
async def list_project_members(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {"data": await svc.list_project_members(uow, actor.ws.id, key), "next_cursor": None}


@router.get("/me/scopes", tags=["access"])
async def my_scopes(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Scopes with any readable object: the scope selector only lists these (UX §6)."""
    data = await svc.readable_scopes(uow, actor.ws, actor.person, actor.member, actor.acl)
    return {"data": data, "next_cursor": None}


@router.get("/events", tags=["access"])
async def list_events(
    actor: CurrentActor,
    uow: Uow,
    entity_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """The audit feed, filtered by visibility; `raw_input` only reaches the actor and their line managers."""
    data = await svc.event_feed(
        uow, actor.ws.id, actor.acl, entity_id=entity_id, event_type=event_type, limit=limit
    )
    return {"data": data, "next_cursor": None}
