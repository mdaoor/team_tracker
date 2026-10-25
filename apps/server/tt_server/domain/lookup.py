"""Lookups shared by the services: a visible project or item, a member by handle, a workspace by slug.

Invisible rows are reported exactly like missing ones (404, never 403; ADR-0008).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from tt_schema.enums import ScopeType

from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.errors import NotFound


async def visible_project(uow: UnitOfWork, ws_id: str, key: str) -> m.Project:
    """Load a project by key, restricted to what the request's ACL context (if any) may see."""
    stmt = select(m.Project).where(
        m.Project.workspace_id == ws_id,
        func.lower(m.Project.key) == key.lower(),
        m.Project.deleted_at.is_(None),
    )
    if (acl := uow.context.get("acl")) is not None:
        stmt = visible(stmt, acl, m.Project)  # invisible => 404, never 403 (ADR-0008)
    p = await uow.session.scalar(stmt)
    if p is None:
        raise NotFound("not_found", "project not found")
    return p


async def visible_item(uow: UnitOfWork, ws_id: str, id_or_key: str) -> m.WorkItem:
    """Load a work item by id or key, restricted to what the request's ACL context (if any) may see."""
    stmt = (
        select(m.WorkItem)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .where(
            m.Project.workspace_id == ws_id,
            m.WorkItem.deleted_at.is_(None),
            (m.WorkItem.id == id_or_key) | (func.lower(m.WorkItem.key) == id_or_key.lower()),
        )
    )
    if (acl := uow.context.get("acl")) is not None:
        stmt = visible(stmt, acl, m.WorkItem)
    it = await uow.session.scalar(stmt)
    if it is None:
        raise NotFound("not_found", "item not found")
    return it


async def member_person_id(uow: UnitOfWork, ws_id: str, handle: str) -> str:
    """Resolve a member handle (with or without the leading @) to its person id, or raise NotFound."""
    pid = await uow.session.scalar(
        select(m.WorkspaceMember.person_id).where(
            m.WorkspaceMember.workspace_id == ws_id,
            m.WorkspaceMember.handle == (handle if handle.startswith("@") else "@" + handle),
        )
    )
    if pid is None:
        raise NotFound("not_found", f"unknown member {handle}", pointer="/handle")
    return str(pid)


async def resolve_scope_id(uow: UnitOfWork, ws_id: str, scope_type: ScopeType, ref: str) -> str:
    """Resolve a project, product or team reference (id or key/slug) to its id for a scoped role or mapping."""
    if scope_type == ScopeType.PROJECT:
        return (await visible_project(uow, ws_id, ref)).id
    model: Any = m.Product if scope_type == ScopeType.PRODUCT else m.Team
    key_col: Any = m.Product.key if scope_type == ScopeType.PRODUCT else m.Team.slug
    row = await uow.session.scalar(
        select(model).where(model.workspace_id == ws_id, (model.id == ref) | (key_col == ref))
    )
    if row is None:
        raise NotFound("not_found", f"{scope_type.value} {ref!r} not found", pointer="/scope_id")
    return str(row.id)
