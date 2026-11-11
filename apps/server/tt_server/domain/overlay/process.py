"""Delivery process, stages and placements with history (object model §5) + stage <-> state sync (§13.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    EventSource,
    EvidenceKind,
    StageCategory,
    StateCategory,
    TargetType,
    ValueSource,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, Forbidden, Invalid
from tt_server.domain.items import service as items
from tt_server.domain.overlay.classify import classify
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value
from tt_server.domain.overlay.process_defs import (
    SW_DELIVERY,
    board,
    current_placement,
    ensure_default_process,
    get_process,
    get_stage,
    stages_of,
    wip_count,
)
from tt_server.domain.projects.service import pick_state, workflow_states

log = structlog.get_logger()


async def _lock_target(session: AsyncSession, target_type: TargetType, target_id: str) -> None:
    model: Any = {
        TargetType.WORK_ITEM: m.WorkItem,
        TargetType.PROJECT: m.Project,
        TargetType.PRODUCT: m.Product,
    }.get(target_type)
    if model is None:
        raise Invalid("not_applicable", f"cannot place a {target_type.value}")
    await session.execute(select(model.id).where(model.id == target_id).with_for_update())


async def _missing_evidence(session: AsyncSession, item_id: str, kinds: list[str]) -> list[str]:
    missing = []
    for k in kinds:
        n = await session.scalar(
            select(func.count())
            .select_from(m.Evidence)
            .where(m.Evidence.work_item_id == item_id, m.Evidence.kind == EvidenceKind(k))
        )
        if not n:
            missing.append(k)
    return missing


async def place(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    target_type: TargetType,
    target_id: str,
    process: m.Process,
    stage: m.Stage,
    moved_by: str,
    reason: str | None = None,
    source: EventSource = EventSource.OVERLAY,
    strict: bool = True,
    entered_at: datetime | None = None,
    sync_state: bool = True,
    confirmed: bool = True,
    correlation_id: str | None = None,
) -> m.StagePlacement:
    """Move a target to `stage` (one current placement per target and process, guaranteed by the DB)."""
    s = uow.session
    await _lock_target(s, target_type, target_id)
    current = await current_placement(s, target_type, target_id, process.id)
    if current is not None and current.stage_id == stage.id:
        return current
    rules = (
        await s.scalars(
            select(m.StageTransitionRule).where(
                m.StageTransitionRule.process_id == process.id,
                m.StageTransitionRule.to_stage_id == stage.id,
                m.StageTransitionRule.deleted_at.is_(None),
            )
        )
    ).all()
    for rule in rules:
        if rule.from_stage_id and (current is None or rule.from_stage_id != current.stage_id):
            continue
        if rule.allowed_roles:
            acl = uow.context.get("acl")
            held = {a.role_key for a in acl.assignments} if acl is not None else None
            allowed = {str(r) for r in rule.allowed_roles}
            if held is not None and not (held & allowed) and not getattr(acl, "is_admin", False):
                if strict:
                    raise Forbidden(
                        "role_not_allowed",
                        "your role may not make this transition",
                        details={"allowed_roles": sorted(allowed)},
                    )
                s.add(  # capture-style actors are never blocked: place, then ask a manager
                    m.ReviewItem(
                        id=new_id(), workspace_id=ws.id, kind="transition_role", target_type=target_type.value, target_id=target_id,
                        suggestion={"allowed_roles": sorted(allowed), "stage": stage.key},
                    )
                )  # fmt: skip
        if rule.required_evidence_kinds and target_type == TargetType.WORK_ITEM:
            missing = await _missing_evidence(s, target_id, [str(k) for k in rule.required_evidence_kinds])
            if missing and strict:
                raise Conflict(
                    "missing_evidence",
                    "required evidence is missing for this transition",
                    details={"missing": missing},
                )
            if missing:  # capture-style actors are never blocked: place, then ask a manager
                s.add(
                    m.ReviewItem(
                        id=new_id(),
                        workspace_id=ws.id,
                        kind="missing_evidence",
                        target_type=target_type.value,
                        target_id=target_id,
                        suggestion={"missing": missing, "stage": stage.key},
                    )
                )
    now = entered_at or utcnow()
    previous_stage_id = current.stage_id if current else None
    if current is not None:
        current.exited_at = now
        await s.flush()
    placement = m.StagePlacement(
        id=new_id(),
        target_type=target_type,
        target_id=target_id,
        process_id=process.id,
        stage_id=stage.id,
        entered_at=now,
        moved_by=moved_by,
        reason=reason,
    )
    s.add(placement)
    await s.flush()
    if stage.requires_approval and target_type == TargetType.WORK_ITEM:
        from tt_server.domain.overlay import approvals

        item = await s.get(m.WorkItem, target_id)
        assert item is not None  # noqa: S101
        approval = await approvals.request(
            uow,
            ws,
            project_id=item.project_id,
            target_type=TargetType.STAGE_PLACEMENT,
            target_id=placement.id,
            title=f"{item.key} → {stage.name}",
            requested_by=moved_by,
        )
        placement.approval_id = approval.id
    dims = await ensure_builtin_dimensions(s, ws.id)
    value = await ensure_value(
        s, dims["stage"], backing_type="stage", backing_id=stage.id, key=stage.key, name=stage.name
    )
    await classify(
        uow,
        ws,
        target_type=target_type,
        target_id=target_id,
        dimension=dims["stage"],
        value=value,
        source=ValueSource.RULE,
        set_by=moved_by,
        is_confirmed=confirmed,
        emit_event=False,
    )
    ev = emit(
        uow,
        workspace_id=ws.id,
        actor_id=moved_by,
        entity_type=target_type,
        entity_id=target_id,
        event_type="stage.moved",
        payload={
            "before": {"stage_id": previous_stage_id},
            "after": {
                "stage": stage.key,
                "process": process.key,
                "placement_id": placement.id,
                "approval_id": placement.approval_id,
            },
            "reason": reason,
        },
        source=source,
        correlation_id=correlation_id or uow.context.get("correlation_id"),
    )
    if sync_state and target_type == TargetType.WORK_ITEM and not uow.context.get("sync_depth"):
        await _stage_to_state(uow, ws, target_id, previous_stage_id, stage, moved_by, ev.id)
    return placement


async def _stage_to_state(
    uow: UnitOfWork,
    ws: m.Workspace,
    item_id: str,
    previous_stage_id: str | None,
    stage: m.Stage,
    actor: str,
    trigger_event_id: str,
) -> None:
    s = uow.session
    item = await s.get(m.WorkItem, item_id)
    project = await s.get(m.Project, item.project_id) if item else None
    if item is None or project is None:
        return
    states = await workflow_states(s, project.workflow_id)
    cat = next((st.category for st in states if st.id == item.state_id), StateCategory.OPEN)
    prev_maps = None
    if previous_stage_id:
        prev = await s.get(m.Stage, previous_stage_id)
        prev_maps = prev.maps_to_state_category if prev else None
    target: m.WorkflowState | None = None
    if stage.maps_to_state_category == StateCategory.DONE and cat != StateCategory.DONE:
        target = pick_state(states, StateCategory.DONE, terminal=True)
    elif (
        prev_maps == StateCategory.DONE
        and stage.maps_to_state_category != StateCategory.DONE
        and cat == StateCategory.DONE
    ):
        target = pick_state(states, StateCategory.ACTIVE)
    if target is None or target.id == item.state_id:
        return
    uow.context["sync_depth"] = uow.context.get("sync_depth", 0) + 1
    try:
        old, new = await items.set_state(uow, item, target)
    finally:
        uow.context["sync_depth"] -= 1
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.reopened" if old == StateCategory.DONE else "item.state_changed",
        payload={
            "before": {"category": old.value},
            "after": {"category": new.value, "state": target.key},
            "cause": "stage",
            "stage": stage.key,
        },
        source=EventSource.PROPAGATION,
        correlation_id=trigger_event_id,
    )


async def state_to_stage(uow: UnitOfWork, item: m.WorkItem, old: StateCategory, new: StateCategory) -> None:
    """P1 extension-point hook: a person's state change moves the placement (guarded against ping-pong)."""
    if uow.context.get("sync_depth") or StateCategory.DONE not in (old, new):
        return
    s = uow.session
    project = await s.get(m.Project, item.project_id)
    if project is None:
        return
    ws = await s.get(m.Workspace, project.workspace_id)
    if ws is None or project.kind.value == "inbox":
        return
    process = (
        await get_process(s, ws.id, project.default_process_id)
        if project.default_process_id
        else await get_process(s, ws.id)
    )
    cur = await current_placement(s, TargetType.WORK_ITEM, item.id, process.id)
    if cur is None:
        return
    stages = {st.id: st for st in await stages_of(s, process.id)}
    cur_stage = stages.get(cur.stage_id)
    if cur_stage is None:
        return
    target: m.Stage | None = None
    if new == StateCategory.DONE and cur_stage.maps_to_state_category != StateCategory.DONE:
        target = next((st for st in stages.values() if st.category == StageCategory.CLOSED), None)
        reason = "state changed to done"
    elif old == StateCategory.DONE and cur_stage.maps_to_state_category == StateCategory.DONE:
        history = (
            await s.scalars(
                select(m.StagePlacement)
                .where(
                    m.StagePlacement.target_type == TargetType.WORK_ITEM,
                    m.StagePlacement.target_id == item.id,
                    m.StagePlacement.process_id == process.id,
                )
                .order_by(m.StagePlacement.entered_at.desc())
            )
        ).all()
        target = next(
            (
                stages[h.stage_id]
                for h in history
                if h.stage_id in stages and stages[h.stage_id].maps_to_state_category != StateCategory.DONE
            ),
            None,
        )
        target = target or next(
            (st for st in stages.values() if st.category == StageCategory.EXECUTION), None
        )
        reason = "state reopened"
    else:
        return
    if target is None:
        return
    actor = uow.context.get("actor_id") or item.assignee_id or item.reporter_id
    uow.context["sync_depth"] = uow.context.get("sync_depth", 0) + 1
    try:
        await place(
            uow,
            ws,
            target_type=TargetType.WORK_ITEM,
            target_id=item.id,
            process=process,
            stage=target,
            moved_by=actor,
            reason=reason,
            source=EventSource.PROPAGATION,
            sync_state=False,
            strict=False,
        )
    finally:
        uow.context["sync_depth"] -= 1


items.on_state_category_changed.append(state_to_stage)


# The definitions and read side moved to `process_defs`; the old import path keeps working.
__all__ = [
    "SW_DELIVERY",
    "board",
    "current_placement",
    "ensure_default_process",
    "get_process",
    "get_stage",
    "place",
    "stages_of",
    "state_to_stage",
    "wip_count",
]
