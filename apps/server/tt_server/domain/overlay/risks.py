"""Risks, explicit and inherited links, actions (object model §7 additions, UX §7.2)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from tt_schema.base import new_id
from tt_schema.enums import EventSource, RiskCategory, RiskResponse, RiskStatus, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid

OPEN_STATUSES = [s for s in RiskStatus if s not in (RiskStatus.CLOSED, RiskStatus.OCCURRED)]


async def next_risk_key(uow: UnitOfWork, project: m.Project) -> str:
    n = await uow.session.scalar(
        select(func.count()).select_from(m.Risk).where(m.Risk.project_id == project.id)
    )
    taken = set((await uow.session.scalars(select(m.Risk.key).where(m.Risk.project_id == project.id))).all())
    i = int(n or 0) + 1
    while f"{project.key}-R{i}" in taken:
        i += 1
    return f"{project.key}-R{i}"


async def create_risk(
    uow: UnitOfWork,
    ws: m.Workspace,
    project: m.Project,
    *,
    title: str,
    owner_id: str,
    actor_id: str,
    target_type: TargetType = TargetType.PROJECT,
    target_id: str | None = None,
    category: RiskCategory = RiskCategory.TECHNICAL,
    probability: int = 3,
    impact: int = 3,
    response_strategy: RiskResponse = RiskResponse.MITIGATE,
    status: RiskStatus = RiskStatus.IDENTIFIED,
    description: str | None = None,
    key: str | None = None,
    review_date: date | None = None,
    spread_links: bool = True,
) -> m.Risk:
    if not (1 <= probability <= 5 and 1 <= impact <= 5):
        raise Invalid("invalid_score", "probability and impact must be 1-5", pointer="/probability")
    if target_type not in (TargetType.PROJECT, TargetType.WORK_ITEM, TargetType.PRODUCT):
        raise Invalid(
            "invalid_target",
            "a risk attaches to a project, work item or product",
            pointer="/target_type",
        )
    target_id = target_id or (project.id if target_type == TargetType.PROJECT else None)
    if target_id is None:
        raise Invalid("invalid_target", "target_id is required", pointer="/target_id")
    risk = m.Risk(
        id=new_id(),
        project_id=project.id,
        key=key or await next_risk_key(uow, project),
        title=title,
        owner_id=owner_id,
        target_type=target_type,
        target_id=target_id,
        description=description,
        category=category,
        probability=probability,
        impact=impact,
        status=status,
        response_strategy=response_strategy,
        review_date=review_date,
    )
    uow.session.add(risk)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.RISK,
        entity_id=risk.id,
        event_type="risk.created",
        payload={"after": {"key": risk.key, "score": risk.probability * risk.impact}},
        source=EventSource.OVERLAY,
    )
    if spread_links:
        await spread(uow, ws, risk, actor_id=actor_id)
    return risk


async def scope_item_ids(uow: UnitOfWork, risk: m.Risk) -> list[str]:
    """Items the risk shows on: a project's items, a product's delivered projects' items, or a subtree."""
    s = uow.session
    if risk.target_type == TargetType.PROJECT and risk.target_id:
        return list(
            (
                await s.scalars(
                    select(m.WorkItem.id).where(
                        m.WorkItem.project_id == risk.target_id, m.WorkItem.deleted_at.is_(None)
                    )
                )
            ).all()
        )
    if risk.target_type == TargetType.PRODUCT and risk.target_id:
        project_ids = select(m.ProductLink.target_id).where(
            m.ProductLink.product_id == risk.target_id,
            m.ProductLink.target_type == TargetType.PROJECT,
            m.ProductLink.role == "delivers",
            m.ProductLink.deleted_at.is_(None),
        )
        return list(
            (
                await s.scalars(
                    select(m.WorkItem.id).where(
                        m.WorkItem.project_id.in_(project_ids), m.WorkItem.deleted_at.is_(None)
                    )
                )
            ).all()
        )
    if risk.target_type == TargetType.WORK_ITEM and risk.target_id:
        return await subtree_ids(uow, risk.target_id, include_self=True)
    return []


async def subtree_ids(uow: UnitOfWork, item_id: str, *, include_self: bool = False) -> list[str]:
    s = uow.session
    path = await s.scalar(select(m.WorkItem.path).where(m.WorkItem.id == item_id))
    if path is None:
        return []
    stmt = select(m.WorkItem.id).where(m.WorkItem.path.op("<@")(path), m.WorkItem.deleted_at.is_(None))
    if not include_self:
        stmt = stmt.where(m.WorkItem.id != item_id)
    return list((await s.scalars(stmt)).all())


async def spread(uow: UnitOfWork, ws: m.Workspace, risk: m.Risk, *, actor_id: str) -> int:
    """Create inherited RiskLinks on every item in scope that lacks one (idempotent)."""
    s = uow.session
    have = set(
        (
            await s.scalars(
                select(m.RiskLink.work_item_id).where(
                    m.RiskLink.risk_id == risk.id, m.RiskLink.deleted_at.is_(None)
                )
            )
        ).all()
    )
    created = 0
    for iid in await scope_item_ids(uow, risk):
        if iid not in have:
            s.add(m.RiskLink(id=new_id(), risk_id=risk.id, work_item_id=iid, inherited=True))
            created += 1
    if created:
        await s.flush()
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=actor_id,
            entity_type=TargetType.RISK,
            entity_id=risk.id,
            event_type="product.risk_linked" if risk.target_type == TargetType.PRODUCT else "risk.linked",
            payload={"after": {"inherited_links": created, "scope": risk.target_type.value}},
            source=EventSource.PROPAGATION,
            correlation_id=uow.context.get("correlation_id"),
        )
    return created


async def link_item(
    uow: UnitOfWork, ws: m.Workspace, risk: m.Risk, item: m.WorkItem, *, actor_id: str
) -> int:
    """Explicit link on `item` plus inherited links on its subtree; returns how many were created."""
    s = uow.session
    link = await s.scalar(
        select(m.RiskLink).where(
            m.RiskLink.risk_id == risk.id, m.RiskLink.work_item_id == item.id, m.RiskLink.deleted_at.is_(None)
        )
    )
    if link is None:
        s.add(m.RiskLink(id=new_id(), risk_id=risk.id, work_item_id=item.id, inherited=False))
    elif link.inherited:
        link.inherited = False  # an explicit decision outlives the parent's link
    await s.flush()
    created = 0
    have = set(
        (
            await s.scalars(
                select(m.RiskLink.work_item_id).where(
                    m.RiskLink.risk_id == risk.id, m.RiskLink.deleted_at.is_(None)
                )
            )
        ).all()
    )
    for iid in await subtree_ids(uow, item.id):
        if iid not in have:
            s.add(m.RiskLink(id=new_id(), risk_id=risk.id, work_item_id=iid, inherited=True))
            created += 1
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="risk.linked",
        payload={"after": {"risk": risk.key, "inherited_links": created}},
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    return created


async def unlink_item(
    uow: UnitOfWork, ws: m.Workspace, risk: m.Risk, item: m.WorkItem, *, actor_id: str
) -> int:
    """Remove the explicit link on `item` and the *inherited* links below it; explicit child links stay."""
    s = uow.session
    ids = await subtree_ids(uow, item.id)
    res = await s.execute(
        delete(m.RiskLink).where(
            m.RiskLink.risk_id == risk.id, m.RiskLink.inherited.is_(True), m.RiskLink.work_item_id.in_(ids)
        )
    )
    removed = int(res.rowcount or 0)  # type: ignore[attr-defined]
    await s.execute(
        delete(m.RiskLink).where(m.RiskLink.risk_id == risk.id, m.RiskLink.work_item_id == item.id)
    )
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="risk.unlinked",
        payload={"before": {"risk": risk.key, "inherited_links_removed": removed}},
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    return removed


async def inherit_for_new_item(
    uow: UnitOfWork, item: m.WorkItem, parent: m.WorkItem | None, project: m.Project
) -> int:
    """New item: inherited links from project-/product-level risks and from its parent's links."""
    s = uow.session
    risk_ids: set[str] = set()
    for r in (
        await s.scalars(
            select(m.Risk).where(
                m.Risk.target_type == TargetType.PROJECT,
                m.Risk.target_id == project.id,
                m.Risk.deleted_at.is_(None),
            )
        )
    ).all():
        risk_ids.add(r.id)
    if parent is not None:
        risk_ids |= set(
            (
                await s.scalars(
                    select(m.RiskLink.risk_id).where(
                        m.RiskLink.work_item_id == parent.id, m.RiskLink.deleted_at.is_(None)
                    )
                )
            ).all()
        )
    existing = set(
        (await s.scalars(select(m.RiskLink.risk_id).where(m.RiskLink.work_item_id == item.id))).all()
    )
    n = 0
    for rid in risk_ids - existing:
        s.add(m.RiskLink(id=new_id(), risk_id=rid, work_item_id=item.id, inherited=True))
        n += 1
    return n


async def open_risk_score(session: object, item_ids: list[str]) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)  # noqa: S101
    if not item_ids:
        return 0
    rows = (
        await session.execute(
            select(m.Risk.id, m.Risk.probability, m.Risk.impact)
            .join(m.RiskLink, m.RiskLink.risk_id == m.Risk.id)
            .where(
                m.RiskLink.work_item_id.in_(item_ids),
                m.RiskLink.deleted_at.is_(None),
                m.Risk.status.in_(OPEN_STATUSES),
                m.Risk.deleted_at.is_(None),
            )
        )
    ).all()
    distinct = {rid: (p, i) for rid, p, i in rows}  # each risk counts once however many items carry it
    return sum(p * i for p, i in distinct.values())


async def add_action(
    uow: UnitOfWork,
    ws: m.Workspace,
    risk: m.Risk,
    *,
    description: str,
    actor_id: str,
    due_date: date | None = None,
) -> m.RiskAction:
    row = m.RiskAction(id=new_id(), risk_id=risk.id, description=description, due_date=due_date)
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.RISK,
        entity_id=risk.id,
        event_type="risk_action.created",
        payload={"after": {"action_id": row.id}},
        source=EventSource.OVERLAY,
    )
    return row
