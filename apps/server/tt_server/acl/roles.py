"""Builtin roles and role assignments."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import ScopeType

from tt_server.acl.permissions import ROLES
from tt_server.db import models as m
from tt_server.domain.errors import NotFound


async def ensure_builtin_roles(session: AsyncSession) -> dict[str, m.Role]:
    """Idempotent (and race-safe via the partial unique index): global `Role` rows for every builtin role."""
    for spec in ROLES.values():  # ON CONFLICT keeps this safe when two workspaces are created at once
        await session.execute(
            pg_insert(m.Role)
            .values(
                id=new_id(),
                key=spec.key,
                name=spec.name,
                permissions=sorted(spec.permissions),
                workspace_id=None,
            )
            .on_conflict_do_nothing(index_elements=["key"], index_where=m.Role.workspace_id.is_(None))
        )
    await session.flush()
    return {
        r.key: r for r in (await session.scalars(select(m.Role).where(m.Role.workspace_id.is_(None)))).all()
    }


async def get_role(session: AsyncSession, key: str, ws_id: str | None = None) -> m.Role:
    roles = await ensure_builtin_roles(session)
    if key in roles:
        return roles[key]
    if ws_id:
        custom = await session.scalar(select(m.Role).where(m.Role.workspace_id == ws_id, m.Role.key == key))
        if custom:
            return custom
    raise NotFound("not_found", f"role {key!r} not found")


async def assign(
    session: AsyncSession,
    ws_id: str,
    person_id: str,
    role_key: str,
    scope_type: ScopeType = ScopeType.WORKSPACE,
    scope_id: str | None = None,
    *,
    source: str = "manual",
) -> m.RoleAssignment:
    """Assign a role; workspace scope uses the workspace id as `scope_id`. Idempotent."""
    role = await get_role(session, role_key, ws_id)
    if scope_type == ScopeType.WORKSPACE:
        scope_id = ws_id
    existing = await session.scalar(
        select(m.RoleAssignment).where(
            m.RoleAssignment.workspace_id == ws_id,
            m.RoleAssignment.person_id == person_id,
            m.RoleAssignment.role_id == role.id,
            m.RoleAssignment.scope_type == scope_type,
            m.RoleAssignment.scope_id == scope_id,
            m.RoleAssignment.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return existing
    row = m.RoleAssignment(
        id=new_id(),
        workspace_id=ws_id,
        person_id=person_id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
        source=source,
    )
    session.add(row)
    await session.flush()
    return row
