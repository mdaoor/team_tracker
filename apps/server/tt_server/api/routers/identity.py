"""Identity-provider settings: group -> role / team mappings and on-demand directory sync (Phase 4 §4.6)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import ScopeType, TargetType

from tt_server.acl.permissions import ROLES
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.access import _resolve_scope
from tt_server.api.routers.core import row_dict
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.integrations import idp

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["identity"])


class MappingIn(BaseModel):
    provider: Literal["ldap", "oidc"] = "ldap"
    group: str = Field(min_length=1, description="AD group DN or OIDC group id")
    role: str | None = None
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None
    team: str | None = Field(default=None, description="team slug or id to add members to")


@router.get("/idp/mappings", dependencies=[needs("role.admin")])
async def list_mappings(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.IdpGroupMapping).where(
                m.IdpGroupMapping.workspace_id == actor.ws.id, m.IdpGroupMapping.deleted_at.is_(None)
            )
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/idp/mappings", status_code=201, dependencies=[needs("role.admin")])
async def create_mapping(body: MappingIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    if not body.role and not body.team:
        raise ApiError(400, "empty_mapping", "a mapping needs a role and/or a team", pointer="/role")
    spec = ROLES.get(body.role) if body.role else None
    if body.role and spec is None:
        custom = await uow.session.scalar(
            select(m.Role.id).where(m.Role.workspace_id == actor.ws.id, m.Role.key == body.role)
        )
        if custom is None:
            raise ApiError(404, "not_found", f"role {body.role!r} not found", pointer="/role")
    if spec is not None and body.scope_type != ScopeType.WORKSPACE and not spec.scoped:
        raise ApiError(
            400, "not_scopable", f"role {body.role!r} is workspace-wide only", pointer="/scope_type"
        )
    scope_id: str | None = None
    if body.role and body.scope_type != ScopeType.WORKSPACE:
        if not body.scope_id:
            raise ApiError(
                400, "scope_required", "scope_id is required for scoped roles", pointer="/scope_id"
            )
        scope_id = await _resolve_scope(uow, actor.ws.id, body.scope_type, body.scope_id)
    team_id: str | None = None
    if body.team:
        team_id = await uow.session.scalar(
            select(m.Team.id).where(
                m.Team.workspace_id == actor.ws.id, (m.Team.id == body.team) | (m.Team.slug == body.team)
            )
        )
        if team_id is None:
            raise ApiError(404, "not_found", f"team {body.team!r} not found", pointer="/team")
    row = m.IdpGroupMapping(
        id=new_id(),
        workspace_id=actor.ws.id,
        provider=body.provider,
        group=body.group,
        role_key=body.role,
        scope_type=body.scope_type.value,
        scope_id=scope_id,
        team_id=team_id,
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="idp_mapping.created",
        payload={
            "after": {"provider": body.provider, "group": body.group, "role": body.role, "team": team_id}
        },
    )
    await uow.commit()
    return row_dict(row)


@router.delete("/idp/mappings/{mapping_id}", status_code=204, dependencies=[needs("role.admin")])
async def delete_mapping(mapping_id: str, actor: CurrentActor, uow: Uow) -> Response:
    row = await uow.session.scalar(
        select(m.IdpGroupMapping).where(
            m.IdpGroupMapping.id == mapping_id,
            m.IdpGroupMapping.workspace_id == actor.ws.id,
            m.IdpGroupMapping.deleted_at.is_(None),
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "mapping not found")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="idp_mapping.deleted",
        payload={"before": {"group": row.group}},
    )
    await uow.commit()
    return Response(status_code=204)


@router.post("/idp/sync", dependencies=[needs("role.admin")])
async def sync_now(request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Run the directory sync for this workspace now (the nightly job does the same for every workspace)."""
    ldap = request.app.state.ldap
    if ldap is None:
        raise ApiError(409, "not_configured", "LDAP is not configured on this server")
    res = await idp.sync_workspace(uow, actor.ws, ldap.lookup_email)
    return {"checked": res.checked, "changed": res.changed, "missing": res.missing, "log": res.log}
