"""Risk register queries and edits behind `/risks`: lookup, listing, patching, turning an action into a work item."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from tt_schema.enums import EventSource, TargetType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, NotFound
from tt_server.domain.items import service as items
from tt_server.domain.overlay import risks


async def get_risk(uow: UnitOfWork, ws_id: str, ref: str) -> m.Risk:
    """A visible risk by id or (upper-cased) key; invisible or absent is a 404."""
    stmt = (
        select(m.Risk)
        .join(m.Project, m.Project.id == m.Risk.project_id)
        .where(
            m.Project.workspace_id == ws_id,
            m.Risk.deleted_at.is_(None),
            (m.Risk.id == ref) | (m.Risk.key == ref.upper()),
        )
    )
    if (acl := uow.context.get("acl")) is not None:
        stmt = visible(stmt, acl, m.Risk)
    r = await uow.session.scalar(stmt)
    if r is None:
        raise NotFound("not_found", "risk not found")
    return r


async def project_of(uow: UnitOfWork, risk: m.Risk) -> m.Project:
    """The project a risk belongs to."""
    p = await uow.session.get(m.Project, risk.project_id)
    if p is None:
        raise NotFound("not_found", "project not found")
    return p


async def list_risks(
    uow: UnitOfWork, ws_id: str, acl: AclContext, *, project: str | None, open_only: bool
) -> list[tuple[m.Risk, str]]:
    """Visible risks as (risk, project key), optionally for one project and/or only the open ones."""
    stmt = (
        select(m.Risk, m.Project.key)
        .join(m.Project, m.Project.id == m.Risk.project_id)
        .where(m.Project.workspace_id == ws_id, m.Risk.deleted_at.is_(None))
    )
    stmt = visible(stmt, acl, m.Risk)
    if project:
        stmt = stmt.where(func.lower(m.Project.key) == project.lower())
    if open_only:
        stmt = stmt.where(m.Risk.status.in_(risks.OPEN_STATUSES))
    return [(r, k) for r, k in (await uow.session.execute(stmt.order_by(m.Risk.key))).all()]


def patch_risk(uow: UnitOfWork, ws: m.Workspace, actor_id: str, r: m.Risk, data: dict[str, Any]) -> m.Risk:
    """Apply a partial edit to a risk and record it."""
    for k, v in data.items():
        setattr(r, k, v)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.RISK,
        entity_id=r.id,
        event_type="risk.updated",
        payload={"fields_changed": list(data)},
    )
    return r


async def action_to_item(uow: UnitOfWork, ws: m.Workspace, actor_id: str, r: m.Risk, aid: str) -> m.WorkItem:
    """Create a work item (assigned to the actor) from a risk action and link the two."""
    a = await uow.session.scalar(
        select(m.RiskAction).where(m.RiskAction.id == aid, m.RiskAction.risk_id == r.id)
    )
    if a is None:
        raise NotFound("not_found", "action not found")
    if a.work_item_id:
        raise Conflict("already_linked", "this action already has a work item")
    project = await project_of(uow, r)
    item = await items.create_item(
        uow,
        ws,
        project,
        title=a.description,
        reporter_id=actor_id,
        assignee_id=actor_id,
        due_date=a.due_date,
    )
    a.work_item_id = item.id
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.created",
        payload={"after": {"key": item.key, "from_risk_action": a.id}},
        source=EventSource.OVERLAY,
    )
    return item
