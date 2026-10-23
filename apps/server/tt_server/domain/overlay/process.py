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

from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.overlay.classify import classify
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value
from tt_server.domain.projects.service import pick_state, workflow_states

log = structlog.get_logger()

# key, name, category, maps_to_state_category, expected_duration_days (gen_snapshot.py), ADR-0005
SW_DELIVERY: list[tuple[str, str, StageCategory, StateCategory, int | None]] = [
    ("idea", "Idea", StageCategory.IDEATION, StateCategory.OPEN, 14),
    ("discovery", "Discovery", StageCategory.PLANNING, StateCategory.OPEN, 10),
    ("design", "Design", StageCategory.PLANNING, StateCategory.OPEN, 7),
    ("build", "Build", StageCategory.EXECUTION, StateCategory.ACTIVE, 10),
    ("test", "Test", StageCategory.VALIDATION, StateCategory.ACTIVE, 5),
    ("staging", "Staging", StageCategory.RELEASE, StateCategory.ACTIVE, 3),
    ("production", "Production", StageCategory.OPERATE, StateCategory.DONE, None),
    ("retired", "Retired", StageCategory.CLOSED, StateCategory.DONE, None),
]


async def ensure_default_process(session: AsyncSession, ws_id: str, owner_id: str | None = None) -> m.Process:
    proc = await session.scalar(
        select(m.Process).where(
            m.Process.workspace_id == ws_id, m.Process.is_default, m.Process.deleted_at.is_(None)
        )
    )
    if proc is not None:
        return proc
    proc = m.Process(
        id=new_id(),
        workspace_id=ws_id,
        key="sw-delivery",
        name="Software delivery",
        is_default=True,
        owner_id=owner_id,
        applies_to=["work_item", "project", "product"],
    )
    session.add(proc)
    await session.flush()
    for pos, (key, name, cat, maps, days) in enumerate(SW_DELIVERY):
        session.add(
            m.Stage(
                id=new_id(),
                process_id=proc.id,
                key=key,
                name=name,
                position=pos,
                category=cat,
                maps_to_state_category=maps,
                expected_duration_days=days,
            )
        )
    await session.flush()
    return proc


async def get_process(session: AsyncSession, ws_id: str, ref: str | None = None) -> m.Process:
    stmt = select(m.Process).where(m.Process.workspace_id == ws_id, m.Process.deleted_at.is_(None))
    stmt = (
        stmt.where((m.Process.id == ref) | (m.Process.key == ref))
        if ref
        else stmt.where(m.Process.is_default)
    )
    proc = await session.scalar(stmt)
    if proc is None:
        raise ApiError(404, "not_found", "process not found")
    return proc


async def stages_of(session: AsyncSession, process_id: str) -> list[m.Stage]:
    return list(
        (
            await session.scalars(
                select(m.Stage)
                .where(m.Stage.process_id == process_id, m.Stage.deleted_at.is_(None))
                .order_by(m.Stage.position)
            )
        ).all()
    )


async def get_stage(session: AsyncSession, process: m.Process, ref: str) -> m.Stage:
    for s in await stages_of(session, process.id):
        if s.key == ref or s.id == ref:
            return s
    raise ApiError(404, "not_found", f"stage {ref!r} not found in process {process.key!r}", pointer="/stage")


async def current_placement(
    session: AsyncSession, target_type: TargetType, target_id: str, process_id: str | None = None
) -> m.StagePlacement | None:
    stmt = select(m.StagePlacement).where(
        m.StagePlacement.target_type == target_type,
        m.StagePlacement.target_id == target_id,
        m.StagePlacement.exited_at.is_(None),
        m.StagePlacement.deleted_at.is_(None),
    )
    if process_id:
        stmt = stmt.where(m.StagePlacement.process_id == process_id)
    return await session.scalar(stmt.order_by(m.StagePlacement.entered_at.desc()).limit(1))


async def _lock_target(session: AsyncSession, target_type: TargetType, target_id: str) -> None:
    model: Any = {
        TargetType.WORK_ITEM: m.WorkItem,
        TargetType.PROJECT: m.Project,
        TargetType.PRODUCT: m.Product,
    }.get(target_type)
    if model is None:
        raise ApiError(400, "not_applicable", f"cannot place a {target_type.value}")
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
        if rule.required_evidence_kinds and target_type == TargetType.WORK_ITEM:
            missing = await _missing_evidence(s, target_id, [str(k) for k in rule.required_evidence_kinds])
            if missing and strict:
                raise ApiError(
                    409,
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


async def wip_count(session: AsyncSession, stage_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(m.StagePlacement)
            .join(
                m.WorkItem,
                (m.StagePlacement.target_type == TargetType.WORK_ITEM)
                & (m.WorkItem.id == m.StagePlacement.target_id),
            )
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(
                m.StagePlacement.stage_id == stage_id,
                m.StagePlacement.exited_at.is_(None),
                m.WorkflowState.category != StateCategory.DONE,
            )
        )
        or 0
    )


async def board(
    session: AsyncSession, ws_id: str, process: m.Process, now: datetime | None = None
) -> dict[str, Any]:
    now = now or utcnow()
    cols = []
    for st in await stages_of(session, process.id):
        rows = (
            await session.execute(
                select(m.StagePlacement, m.WorkItem, m.Project.key)
                .join(
                    m.WorkItem,
                    (m.StagePlacement.target_type == TargetType.WORK_ITEM)
                    & (m.WorkItem.id == m.StagePlacement.target_id),
                )
                .join(m.Project, m.Project.id == m.WorkItem.project_id)
                .where(
                    m.StagePlacement.stage_id == st.id,
                    m.StagePlacement.exited_at.is_(None),
                    m.Project.workspace_id == ws_id,
                    m.WorkItem.deleted_at.is_(None),
                )
                .order_by(m.StagePlacement.entered_at)
            )
        ).all()
        cards = []
        for pl, it, pkey in rows:
            approval = await session.get(m.Approval, pl.approval_id) if pl.approval_id else None
            cards.append(
                {
                    "key": it.key,
                    "title": it.title,
                    "project_key": pkey,
                    "priority": it.priority.value,
                    "stage_age_days": (now - pl.entered_at).days,
                    "entered_at": pl.entered_at.isoformat(),
                    "approval": approval.status.value if approval else None,
                }
            )
        cols.append(
            {
                "stage": st.key,
                "name": st.name,
                "category": st.category.value,
                "wip_limit": st.wip_limit,
                "expected_duration_days": st.expected_duration_days,
                "wip": await wip_count(session, st.id),
                "requires_approval": st.requires_approval,
                "items": cards,
            }
        )
    return {"process": process.key, "columns": cols}
