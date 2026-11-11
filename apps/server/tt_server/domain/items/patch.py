"""Overlay edits of a work item with optimistic concurrency (UX §3.4): the service behind `PATCH /items/{id}`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.enums import EventSource, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, PreconditionRequired
from tt_server.domain.names import Names
from tt_server.domain.overlay import mirrors, propagation, weights
from tt_server.domain.plan import linkage as plan_linkage


def etag_of(item: m.WorkItem) -> str:
    """The ETag a client sends back as `If-Match`: the item's last-update instant."""
    return f'"{item.updated_at.isoformat()}"'


def _jsonable(v: Any) -> Any:
    return v.value if hasattr(v, "value") else (v.isoformat() if hasattr(v, "isoformat") else v)


async def _refuse_stale(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, item: m.WorkItem, data: dict[str, Any]
) -> Conflict:
    """Record the conflict (and keep it: the caller's write is refused but the event is not) and build the 409."""
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.field_conflict",
        payload={"fields_attempted": sorted(data)},
        source=EventSource.OVERLAY,
    )
    last = (
        await uow.session.execute(
            select(m.Event.actor_id, m.Event.occurred_at)
            .where(
                m.Event.entity_type == TargetType.WORK_ITEM,
                m.Event.entity_id == item.id,
                m.Event.event_type != "item.field_conflict",
            )
            .order_by(m.Event.occurred_at.desc(), m.Event.id.desc())
            .limit(1)
        )
    ).first()
    who = (await Names.load(uow.session, ws.id, [("person", last[0])])).person(last[0]) if last else None
    await uow.commit()  # keep the conflict event even though the write is refused
    return Conflict(
        "conflict",
        "the item changed since you read it",
        details={
            "current": {k: _jsonable(getattr(item, k)) for k in data},
            "etag": etag_of(item),
            "changed_by": who,
            "changed_at": last[1].isoformat() if last else None,
        },
    )


async def patch_overlay(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor_id: str,
    item: m.WorkItem,
    data: dict[str, Any],
    *,
    if_match: str | None,
) -> m.WorkItem:
    """Apply an overlay edit if `if_match` is the item's current ETag; otherwise record a conflict and refuse.

    Raises `PreconditionRequired` without an ETag and `Conflict` (409, with the current values) on a stale one.
    Team and priority changes propagate to mirrors and containers; planning dates re-run plan linkage; any change
    recomputes the item's weight. The caller commits.
    """
    if if_match is None:
        raise PreconditionRequired("precondition_required", "send If-Match with the ETag you read")
    if if_match != etag_of(item):
        raise await _refuse_stale(uow, ws, actor_id, item, data)
    data = dict(data)
    priority = data.pop("priority", None)
    changed = list(data)
    old_team = item.team_id
    for k, v in data.items():
        setattr(item, k, v)
    if "impact" in data:
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=actor_id,
            entity_type=TargetType.WORK_ITEM,
            entity_id=item.id,
            event_type="item.impact_changed",
            payload={"after": {"impact": item.impact}},
            source=EventSource.OVERLAY,
        )
    if "team_id" in data:
        await mirrors.sync_item_mirrors(uow, ws, item, set_by=actor_id)
        if item.team_id != old_team:
            await propagation.item_team_changed(uow, ws, item, old_team, actor=actor_id)
    if priority is not None and priority != item.priority:
        await propagation.set_item_priority(uow, ws, item, priority, actor=actor_id)
        changed.append("priority")
    if "planned_end" in data or "planned_start" in data:
        await plan_linkage.plan_changed(uow, ws, item)
    if changed:
        await weights.recompute_item(uow, ws, item, actor_id=actor_id)
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=actor_id,
            entity_type=TargetType.WORK_ITEM,
            entity_id=item.id,
            event_type="item.updated",
            payload={"fields_changed": sorted(changed)},
            source=EventSource.OVERLAY,
        )
    await uow.session.flush()
    await uow.session.refresh(item)
    return item
