"""Process and stage administration plus moving an item between stages (Phase 3, UX §3.3)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from tt_schema.base import new_id
from tt_schema.enums import StageCategory, StateCategory, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit_admin
from tt_server.domain.errors import Invalid
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import weights


async def list_processes(uow: UnitOfWork, ws_id: str, actor_id: str) -> list[dict[str, Any]]:
    """Every live process with its ordered stages, seeding the default process first."""
    await proc.ensure_default_process(uow.session, ws_id, actor_id)
    rows = (
        await uow.session.scalars(
            select(m.Process).where(m.Process.workspace_id == ws_id, m.Process.deleted_at.is_(None))
        )
    ).all()
    return [{"process": p, "stages": await proc.stages_of(uow.session, p.id)} for p in rows]


async def create_process(uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any]) -> m.Process:
    """Create an (empty) process owned by the actor."""
    p = m.Process(id=new_id(), workspace_id=ws.id, owner_id=actor_id, **data)
    uow.session.add(p)
    await uow.session.flush()
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="process.created",
        payload={"after": {"key": p.key}},
    )
    return p


async def add_stage(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, ref: str, data: dict[str, Any]
) -> m.Stage:
    """Append (or insert at `position`) a stage to a process."""
    p = await proc.get_process(uow.session, ws.id, ref)
    position = data.pop("position", None)
    pos = position if position is not None else len(await proc.stages_of(uow.session, p.id))
    category, maps_to = data.pop("category"), data.pop("maps_to_state_category")
    try:
        st = m.Stage(
            id=new_id(),
            process_id=p.id,
            position=pos,
            category=StageCategory(category),
            maps_to_state_category=StateCategory(maps_to),
            **data,
        )
    except ValueError as exc:
        raise Invalid("invalid_stage", str(exc)) from exc
    uow.session.add(st)
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="stage.created",
        payload={"after": {"process": p.key, "key": st.key}},
    )
    return st


async def patch_stage(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, ref: str, stage_key: str, data: dict[str, Any]
) -> m.Stage:
    """Edit a stage in place (name, expected days, WIP limit, approval, category): the chip popover of UX §3.3."""
    p = await proc.get_process(uow.session, ws.id, ref)
    st = await proc.get_stage(uow.session, p, stage_key)
    before = {k: getattr(st, k) for k in data}
    try:
        if data.get("category") is not None:
            data["category"] = StageCategory(data["category"])
        if data.get("maps_to_state_category") is not None:
            data["maps_to_state_category"] = StateCategory(data["maps_to_state_category"])
    except ValueError as exc:
        raise Invalid("invalid_stage", str(exc)) from exc
    for k, v in data.items():
        setattr(st, k, v)
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="stage.updated",
        payload={
            "before": {k: getattr(v, "value", v) for k, v in before.items()},
            "after": {
                "process": p.key,
                "key": st.key,
                **{k: getattr(v, "value", v) for k, v in data.items()},
            },
        },
    )
    return st


async def move_item(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor_id: str,
    item: m.WorkItem,
    *,
    stage: str,
    process: str | None,
    reason: str | None,
) -> m.StagePlacement:
    """Place a work item in a stage of a process and refresh its weight."""
    pr = await proc.get_process(uow.session, ws.id, process)
    st = await proc.get_stage(uow.session, pr, stage)
    placement = await proc.place(
        uow,
        ws,
        target_type=TargetType.WORK_ITEM,
        target_id=item.id,
        process=pr,
        stage=st,
        moved_by=actor_id,
        reason=reason,
    )
    await weights.recompute_item(uow, ws, item, actor_id=actor_id)
    return placement


async def placements_of(uow: UnitOfWork, item: m.WorkItem) -> list[tuple[m.StagePlacement, str]]:
    """The item's stage history as (placement, stage key), oldest first."""
    rows = (
        await uow.session.execute(
            select(m.StagePlacement, m.Stage.key)
            .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
            .where(
                m.StagePlacement.target_type == TargetType.WORK_ITEM, m.StagePlacement.target_id == item.id
            )
            .order_by(m.StagePlacement.entered_at)
        )
    ).all()
    return [(p, k) for p, k in rows]
