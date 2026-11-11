"""Identity-provider group mappings: validation and writes behind the `/idp/mappings` endpoints."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import ScopeType, TargetType

from tt_server.acl.permissions import ROLES
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, row_dict
from tt_server.domain.errors import Invalid, NotFound
from tt_server.domain.lookup import resolve_scope_id


async def list_mappings(uow: UnitOfWork, ws_id: str) -> list[dict[str, Any]]:
    """Live group mappings of the workspace."""
    rows = (
        await uow.session.scalars(
            select(m.IdpGroupMapping).where(
                m.IdpGroupMapping.workspace_id == ws_id, m.IdpGroupMapping.deleted_at.is_(None)
            )
        )
    ).all()
    return [row_dict(r) for r in rows]


async def create_mapping(
    uow: UnitOfWork,
    ws_id: str,
    actor_id: str,
    *,
    provider: str,
    group: str,
    role: str | None,
    scope_type: ScopeType,
    scope_id: str | None,
    team: str | None,
) -> m.IdpGroupMapping:
    """Validate a group -> role/team mapping (known role, scopable, resolvable scope and team) and store it."""
    if not role and not team:
        raise Invalid("empty_mapping", "a mapping needs a role and/or a team", pointer="/role")
    spec = ROLES.get(role) if role else None
    if role and spec is None:
        custom = await uow.session.scalar(
            select(m.Role.id).where(m.Role.workspace_id == ws_id, m.Role.key == role)
        )
        if custom is None:
            raise NotFound("not_found", f"role {role!r} not found", pointer="/role")
    if spec is not None and scope_type != ScopeType.WORKSPACE and not spec.scoped:
        raise Invalid("not_scopable", f"role {role!r} is workspace-wide only", pointer="/scope_type")
    resolved_scope: str | None = None
    if role and scope_type != ScopeType.WORKSPACE:
        if not scope_id:
            raise Invalid("scope_required", "scope_id is required for scoped roles", pointer="/scope_id")
        resolved_scope = await resolve_scope_id(uow, ws_id, scope_type, scope_id)
    team_id: str | None = None
    if team:
        team_id = await uow.session.scalar(
            select(m.Team.id).where(m.Team.workspace_id == ws_id, (m.Team.id == team) | (m.Team.slug == team))
        )
        if team_id is None:
            raise NotFound("not_found", f"team {team!r} not found", pointer="/team")
    row = m.IdpGroupMapping(
        id=new_id(),
        workspace_id=ws_id,
        provider=provider,
        group=group,
        role_key=role,
        scope_type=scope_type.value,
        scope_id=resolved_scope,
        team_id=team_id,
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=actor_id,
        event_type="idp_mapping.created",
        payload={"after": {"provider": provider, "group": group, "role": role, "team": team_id}},
    )
    return row


async def delete_mapping(uow: UnitOfWork, ws_id: str, actor_id: str, mapping_id: str) -> None:
    """Soft-delete a group mapping of the workspace and record the event."""
    row = await uow.session.scalar(
        select(m.IdpGroupMapping).where(
            m.IdpGroupMapping.id == mapping_id,
            m.IdpGroupMapping.workspace_id == ws_id,
            m.IdpGroupMapping.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFound("not_found", "mapping not found")
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=actor_id,
        event_type="idp_mapping.deleted",
        payload={"before": {"group": row.group}},
    )
