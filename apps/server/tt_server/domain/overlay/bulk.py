"""Bulk edit with a propagation preview (plan §3.9). Preview numbers equal apply numbers by construction."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import EventSource, Priority, StateCategory, TargetType, ValueSource

from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import propagation, weights

ALLOWED = {"priority", "impact", "stage", "weight"}


async def resolve_targets(
    uow: UnitOfWork, ws: m.Workspace, *, ids: list[str] | None, filt: dict[str, Any] | None
) -> list[m.WorkItem]:
    s = uow.session
    stmt = (
        select(m.WorkItem)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .where(m.Project.workspace_id == ws.id, m.WorkItem.deleted_at.is_(None))
    )
    if ids:
        stmt = stmt.where((m.WorkItem.id.in_(ids)) | (m.WorkItem.key.in_(ids)))
    elif filt:
        if filt.get("project"):
            stmt = stmt.where(m.Project.key == str(filt["project"]).upper())
        if filt.get("state_category"):
            stmt = stmt.join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id).where(
                m.WorkflowState.category.in_([StateCategory(c) for c in filt["state_category"]])
            )
        if filt.get("assignee"):
            stmt = stmt.join(m.WorkspaceMember, m.WorkspaceMember.person_id == m.WorkItem.assignee_id).where(
                m.WorkspaceMember.handle == filt["assignee"]
            )
    else:
        raise ApiError(400, "no_targets", "give ids or a filter")
    rows = list((await s.scalars(stmt.order_by(m.WorkItem.key))).all())
    if len(rows) > 1000:
        raise ApiError(400, "too_many", "bulk edits are limited to 1000 items")
    return rows


def _validate(changes: dict[str, Any]) -> None:
    if not changes or set(changes) - ALLOWED:
        raise ApiError(400, "invalid_change", f"set may contain only {sorted(ALLOWED)}", pointer="/set")
    if "priority" in changes:
        Priority(changes["priority"])
    if "impact" in changes and not 1 <= int(changes["impact"]) <= 5:
        raise ApiError(400, "invalid_change", "impact must be 1-5", pointer="/set/impact")


async def _descendants(uow: UnitOfWork, item: m.WorkItem) -> list[m.WorkItem]:
    out: list[m.WorkItem] = []
    frontier = [item]
    while frontier:
        kids = list(
            (
                await uow.session.scalars(
                    select(m.WorkItem).where(
                        m.WorkItem.parent_id.in_([f.id for f in frontier]), m.WorkItem.deleted_at.is_(None)
                    )
                )
            ).all()
        )
        out += kids
        frontier = kids
    return out


async def preview(
    uow: UnitOfWork, ws: m.Workspace, targets: list[m.WorkItem], changes: dict[str, Any]
) -> dict[str, Any]:
    _validate(changes)
    target_ids = {t.id for t in targets}
    changed: set[str] = set()
    kept: set[str] = set()
    if "priority" in changes:
        new = Priority(changes["priority"])
        for t in targets:
            stack = [t]
            while stack:  # same walk as propagation.push_priority_down, without writing
                node = stack.pop()
                for kid in (
                    await uow.session.scalars(
                        select(m.WorkItem).where(
                            m.WorkItem.parent_id == node.id, m.WorkItem.deleted_at.is_(None)
                        )
                    )
                ).all():
                    if kid.id not in target_ids:
                        if kid.priority_source == ValueSource.MANUAL:
                            kept.add(kid.key)
                        elif kid.priority != new or kid.priority_source != ValueSource.INHERITED:
                            changed.add(kid.key)
                    stack.append(kid)
    return {
        "affected": len(targets),
        "children_affected": len(changed),
        "overrides_kept": len(kept),
        "sample": [t.key for t in targets[:10]],
    }


async def apply(
    uow: UnitOfWork, ws: m.Workspace, targets: list[m.WorkItem], changes: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    _validate(changes)
    corr = new_id()
    uow.context["correlation_id"] = corr
    res = propagation.PropagationResult()
    default = await weights.ensure_default_scheme(uow.session, ws.id)
    target_ids = {t.id for t in targets}
    for t in targets:
        if "priority" in changes:
            r = await propagation.set_item_priority(
                uow, ws, t, Priority(changes["priority"]), actor=actor, correlation_id=corr
            )
            res.item_keys += [k for k in r.item_keys]
            res.kept |= r.kept
        if "impact" in changes:
            before = t.impact
            t.impact = int(changes["impact"])
            emit(
                uow,
                workspace_id=ws.id,
                actor_id=actor,
                entity_type=TargetType.WORK_ITEM,
                entity_id=t.id,
                event_type="item.impact_changed",
                payload={"before": {"impact": before}, "after": {"impact": t.impact}},
                source=EventSource.OVERLAY,
                correlation_id=corr,
            )
            await weights.recompute_item(uow, ws, t, actor_id=actor)
        if "stage" in changes:
            process = await proc.get_process(uow.session, ws.id)
            await proc.place(
                uow,
                ws,
                target_type=TargetType.WORK_ITEM,
                target_id=t.id,
                process=process,
                stage=await proc.get_stage(uow.session, process, str(changes["stage"])),
                moved_by=actor,
                reason="bulk edit",
                correlation_id=corr,
            )
        if "weight" in changes:
            await weights.set_override(uow, ws, t, default, Decimal(str(changes["weight"])), by=actor)
    res.item_keys = [k for k in res.item_keys if k not in {t.key for t in targets}]
    res.kept = {k for k in res.kept if k not in {t.key for t in targets}}
    _ = target_ids
    return {
        "affected": len(targets),
        "children_affected": res.changed,
        "overrides_kept": res.kept_manual,
        "correlation_id": corr,
    }
