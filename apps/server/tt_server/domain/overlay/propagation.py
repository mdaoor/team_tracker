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
from tt_server.domain.overlay import weights
from tt_server.domain.overlay.classify import classify
from tt_server.domain.overlay.inheritance import inherit_on_create as inherit_on_create
from tt_server.domain.overlay.inheritance import item_team_changed as item_team_changed
from tt_server.domain.overlay.inheritance import project_priority as project_priority
from tt_server.domain.overlay.inheritance import project_process_changed as project_process_changed
from tt_server.domain.overlay.inheritance import project_visibility_changed as project_visibility_changed
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, sync_item_mirrors


@dataclass
class PropagationResult:
    """Outcome of propagating a value down the hierarchy: items touched and values kept as overrides."""

    item_keys: list[str] = field(default_factory=list)
    kept: set[str] = field(default_factory=set)

    @property
    def changed(self) -> int:
        """Number of distinct items whose value changed."""
        return len(set(self.item_keys))

    @property
    def kept_manual(self) -> int:
        """Number of items that kept a manual classification instead of the propagated value."""
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
    """Set an item's priority (manual or inherited), sync mirrors, recompute its weight and record the change."""
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
