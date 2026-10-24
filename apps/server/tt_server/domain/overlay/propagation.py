"""Propagation (object model §6): priority down the hierarchy, inheritance on creation, roll-ups.

Rules run inline, in the transaction of the triggering change, and write `Event{source=propagation}` with the
trigger's correlation id. They are idempotent: re-running compares before writing, so a second run is a no-op.
Overrides win: `priority_source=manual` on an item, `manual` classifications on projects/products.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import EventSource, Priority, ProductLinkRole, TargetType, ValueSource

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.overlay import risks as risk_svc
from tt_server.domain.overlay import weights
from tt_server.domain.overlay.classify import classify
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value, sync_item_mirrors
from tt_server.domain.overlay.products import delivering_product


@dataclass
class PropagationResult:
    item_keys: list[str] = field(default_factory=list)
    kept: set[str] = field(default_factory=set)

    @property
    def changed(self) -> int:
        return len(set(self.item_keys))

    @property
    def kept_manual(self) -> int:
        return len(self.kept)


async def _children(uow: UnitOfWork, item_id: str) -> list[m.WorkItem]:
    return list(
        (
            await uow.session.scalars(
                select(m.WorkItem).where(m.WorkItem.parent_id == item_id, m.WorkItem.deleted_at.is_(None))
            )
        ).all()
    )


async def _apply_priority(
    uow: UnitOfWork,
    ws: m.Workspace,
    item: m.WorkItem,
    priority: Priority,
    actor: str,
    correlation_id: str | None,
    res: PropagationResult,
) -> bool:
    """Set an inherited priority on one item. Returns False when the item's manual override blocks it."""
    if item.priority_source == ValueSource.MANUAL:
        res.kept.add(item.key)
        return False
    if item.priority == priority and item.priority_source == ValueSource.INHERITED:
        return True
    before = item.priority
    item.priority, item.priority_source = priority, ValueSource.INHERITED
    await sync_item_mirrors(uow, ws, item, set_by=actor)
    await weights.recompute_item(uow, ws, item, actor_id=actor)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.priority_changed",
        payload={
            "before": {"priority": before.value},
            "after": {"priority": priority.value, "source": "inherited"},
        },
        source=EventSource.PROPAGATION,
        correlation_id=correlation_id,
    )
    res.item_keys.append(item.key)
    return True


async def push_priority_down(
    uow: UnitOfWork,
    ws: m.Workspace,
    root: m.WorkItem,
    actor: str,
    correlation_id: str | None,
    res: PropagationResult | None = None,
) -> PropagationResult:
    """Subtree propagation: each child takes its parent's priority unless it carries a manual override."""
    res = res or PropagationResult()
    for child in await _children(uow, root.id):
        await _apply_priority(uow, ws, child, root.priority, actor, correlation_id, res)
        await push_priority_down(
            uow, ws, child, actor, correlation_id, res
        )  # a manual child still seeds its own subtree
    return res


async def set_item_priority(
    uow: UnitOfWork,
    ws: m.Workspace,
    item: m.WorkItem,
    priority: Priority,
    *,
    actor: str,
    manual: bool = True,
    correlation_id: str | None = None,
) -> PropagationResult:
    corr = correlation_id or new_id()
    before = item.priority
    item.priority = priority
    item.priority_source = ValueSource.MANUAL if manual else ValueSource.INHERITED
    await sync_item_mirrors(uow, ws, item, set_by=actor)
    await weights.recompute_item(uow, ws, item, actor_id=actor)
    trigger = emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.priority_changed",
        payload={
            "before": {"priority": before.value},
            "after": {"priority": priority.value, "source": item.priority_source.value},
        },
        source=EventSource.OVERLAY,
        correlation_id=corr,
    )
    return await push_priority_down(uow, ws, item, actor, trigger.correlation_id)


async def set_container_priority(
    uow: UnitOfWork,
    ws: m.Workspace,
    target_type: TargetType,
    target_id: str,
    priority: Priority,
    *,
    actor: str,
    manual: bool = True,
    correlation_id: str | None = None,
    _res: PropagationResult | None = None,
) -> PropagationResult:
    """Project / product priority is a classification of the container in the `priority` dimension."""
    s = uow.session
    res = _res or PropagationResult()
    corr = correlation_id or new_id()
    dims = await ensure_builtin_dimensions(s, ws.id)
    value = await s.scalar(
        select(m.DimensionValue).where(
            m.DimensionValue.dimension_id == dims["priority"].id, m.DimensionValue.key == priority.value
        )
    )
    assert value is not None  # noqa: S101
    cls = await classify(
        uow,
        ws,
        target_type=target_type,
        target_id=target_id,
        dimension=dims["priority"],
        value=value,
        source=ValueSource.MANUAL if manual else ValueSource.INHERITED,
        set_by=actor,
        emit_event=False,
    )
    if cls is None:  # a manual container priority blocks inheritance from above
        res.kept.add(f"{target_type.value}:{target_id}")
        return res
    et = "project.priority_changed" if target_type == TargetType.PROJECT else "product.priority_changed"
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor,
        entity_type=target_type,
        entity_id=target_id,
        event_type=et,
        payload={"after": {"priority": priority.value}},
        source=EventSource.OVERLAY if manual else EventSource.PROPAGATION,
        correlation_id=corr,
    )
    if target_type == TargetType.PROJECT:
        items = (
            await s.scalars(
                select(m.WorkItem).where(
                    m.WorkItem.project_id == target_id,
                    m.WorkItem.parent_id.is_(None),
                    m.WorkItem.deleted_at.is_(None),
                )
            )
        ).all()
        # roots take the project's priority; deeper levels follow their parents (child override wins)
        for root in items:
            if (
                await _apply_priority(uow, ws, root, priority, actor, corr, res)
                or root.priority_source == ValueSource.MANUAL
            ):
                await push_priority_down(uow, ws, root, actor, corr, res)
    else:
        links = (
            await s.scalars(
                select(m.ProductLink).where(
                    m.ProductLink.product_id == target_id,
                    m.ProductLink.target_type == TargetType.PROJECT,
                    m.ProductLink.role == ProductLinkRole.DELIVERS,
                    m.ProductLink.deleted_at.is_(None),
                )
            )
        ).all()
        for link in links:
            await set_container_priority(
                uow,
                ws,
                TargetType.PROJECT,
                link.target_id,
                priority,
                actor=actor,
                manual=False,
                correlation_id=corr,
                _res=res,
            )
    return res


async def project_priority(session: object, project_id: str) -> Priority | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)  # noqa: S101
    key = await session.scalar(
        select(m.DimensionValue.key)
        .join(m.Classification, m.Classification.value_id == m.DimensionValue.id)
        .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
        .where(
            m.Dimension.key == "priority",
            m.Classification.target_type == TargetType.PROJECT,
            m.Classification.target_id == project_id,
            m.Classification.deleted_at.is_(None),
        )
    )
    return Priority(key) if key else None


async def inherit_on_create(
    uow: UnitOfWork,
    ws: m.Workspace,
    item: m.WorkItem,
    *,
    parent: m.WorkItem | None = None,
    actor: str | None = None,
    default_stage: bool = True,
) -> None:
    """Tail of object model §12: mirrors, product/objective inheritance, priority, stage, risks, weight."""
    from tt_server.domain.overlay import process as proc

    s = uow.session
    actor = actor or uow.context.get("actor_id") or item.reporter_id
    project = await s.get(m.Project, item.project_id)
    assert project is not None  # noqa: S101
    await sync_item_mirrors(uow, ws, item, set_by=actor)
    dims = await ensure_builtin_dimensions(s, ws.id)
    if project.kind.value != "inbox":
        product = await delivering_product(s, project.id)
        if product is not None:
            value = await ensure_value(
                s,
                dims["product"],
                backing_type="product",
                backing_id=product.id,
                key=product.key,
                name=product.name,
                parent_backing_id=product.parent_product_id,
            )
            await classify(
                uow,
                ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item.id,
                dimension=dims["product"],
                value=value,
                source=ValueSource.INHERITED,
                set_by=actor,
                inherited_from=(TargetType.PRODUCT, product.id),
                emit_event=False,
            )
        for obj in (
            await s.scalars(
                select(m.Classification).where(
                    m.Classification.target_type == TargetType.PROJECT,
                    m.Classification.target_id == project.id,
                    m.Classification.dimension_id == dims["objective"].id,
                    m.Classification.deleted_at.is_(None),
                )
            )
        ).all():
            value_row = await s.get(m.DimensionValue, obj.value_id)
            if value_row:
                await classify(
                    uow,
                    ws,
                    target_type=TargetType.WORK_ITEM,
                    target_id=item.id,
                    dimension=dims["objective"],
                    value=value_row,
                    source=ValueSource.INHERITED,
                    set_by=actor,
                    inherited_from=(TargetType.PROJECT, project.id),
                    emit_event=False,
                )
    if (
        item.priority_source == ValueSource.INHERITED
    ):  # explicit (manual / rule) priorities are never replaced
        inherited = parent.priority if parent is not None else await project_priority(s, project.id)
        if inherited is not None and inherited != item.priority:
            item.priority, item.priority_source = inherited, ValueSource.INHERITED
            await sync_item_mirrors(uow, ws, item, set_by=actor)
    if default_stage and project.kind.value != "inbox":
        process = (
            await proc.get_process(s, ws.id, project.default_process_id)
            if project.default_process_id
            else await proc.get_process(s, ws.id)
        )
        key = (project.settings or {}).get("default_stage")
        stages = await proc.stages_of(s, process.id)
        stage = next((st for st in stages if st.key == key), stages[0] if stages else None)
        if (
            stage is not None
            and await proc.current_placement(s, TargetType.WORK_ITEM, item.id, process.id) is None
        ):
            await proc.place(
                uow,
                ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item.id,
                process=process,
                stage=stage,
                moved_by=actor,
                reason="default stage",
                source=EventSource.RULE,
                sync_state=False,
                strict=False,
            )
    await risk_svc.inherit_for_new_item(uow, item, parent, project)
    await weights.recompute_item(uow, ws, item, actor_id=actor)


# ------------------------------------------------------------------ container / team triggers (review M3)
async def project_visibility_changed(
    uow: UnitOfWork, ws: m.Workspace, project: m.Project, old: str, *, actor: str
) -> None:
    """Items inherit visibility at read time, so nothing is rewritten; what changes is what viewers may have cached."""
    from sqlalchemy import delete

    await uow.session.execute(delete(m.SnapshotCache).where(m.SnapshotCache.workspace_id == ws.id))
    emit(
        uow, workspace_id=ws.id, actor_id=actor, entity_type=TargetType.PROJECT, entity_id=project.id,
        event_type="project.visibility_changed", payload={"before": {"visibility": old}, "after": {"visibility": project.visibility.value}},
        source=EventSource.PROPAGATION, correlation_id=uow.context.get("correlation_id"),
    )  # fmt: skip


async def project_process_changed(
    uow: UnitOfWork, ws: m.Workspace, project: m.Project, old: str | None, *, actor: str
) -> int:
    """Open items with no placement in the new process enter its default stage (idempotent: placed items are left alone)."""
    from tt_server.domain.overlay import process as proc

    s = uow.session
    process = await proc.get_process(s, ws.id, project.default_process_id)
    stages = await proc.stages_of(s, process.id)
    key = (project.settings or {}).get("default_stage")
    stage = next((st for st in stages if st.key == key), stages[0] if stages else None)
    placed = 0
    if stage is not None:
        ids = (
            await s.scalars(
                select(m.WorkItem.id).where(
                    m.WorkItem.project_id == project.id,
                    m.WorkItem.deleted_at.is_(None),
                    m.WorkItem.closed_at.is_(None),
                )
            )
        ).all()
        for iid in ids:
            if await proc.current_placement(s, TargetType.WORK_ITEM, iid, process.id) is None:
                await proc.place(
                    uow, ws, target_type=TargetType.WORK_ITEM, target_id=iid, process=process, stage=stage, moved_by=actor,
                    reason="project process changed", source=EventSource.PROPAGATION, sync_state=False, strict=False,
                )  # fmt: skip
                placed += 1
    emit(
        uow, workspace_id=ws.id, actor_id=actor, entity_type=TargetType.PROJECT, entity_id=project.id,
        event_type="project.process_changed", payload={"before": {"process_id": old}, "after": {"process_id": project.default_process_id, "items_placed": placed}},
        source=EventSource.PROPAGATION, correlation_id=uow.context.get("correlation_id"),
    )  # fmt: skip
    return placed


async def item_team_changed(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, old_team: str | None, *, actor: str
) -> int:
    """Descendants that carried the old team (inherited, not chosen) follow the new one; explicit different teams stay."""
    s = uow.session
    kids = (
        await s.scalars(
            select(m.WorkItem).where(
                m.WorkItem.path.op("<@")(item.path),
                m.WorkItem.id != item.id,
                m.WorkItem.deleted_at.is_(None),
                m.WorkItem.team_id.is_not_distinct_from(old_team),
            )
        )
    ).all()
    for kid in kids:
        kid.team_id = item.team_id
        await sync_item_mirrors(uow, ws, kid, set_by=actor)
    emit(
        uow, workspace_id=ws.id, actor_id=actor, entity_type=TargetType.WORK_ITEM, entity_id=item.id,
        event_type="item.team_changed", payload={"before": {"team_id": old_team}, "after": {"team_id": item.team_id, "descendants_updated": len(kids)}},
        source=EventSource.PROPAGATION, correlation_id=uow.context.get("correlation_id"),
    )  # fmt: skip
    return len(kids)
