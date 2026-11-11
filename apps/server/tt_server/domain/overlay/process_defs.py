"""Process definitions and read side: the default delivery process, stage lookup, current placement, WIP counts and the board."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    StageCategory,
    StateCategory,
    TargetType,
)

from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.domain.errors import NotFound

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
    """The workspace's default process, created with its stages the first time it is needed."""
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
    """A process by id or key (the default process when `ref` is None); 404 when absent."""
    stmt = select(m.Process).where(m.Process.workspace_id == ws_id, m.Process.deleted_at.is_(None))
    stmt = (
        stmt.where((m.Process.id == ref) | (m.Process.key == ref))
        if ref
        else stmt.where(m.Process.is_default)
    )
    proc = await session.scalar(stmt)
    if proc is None:
        raise NotFound("not_found", "process not found")
    return proc


async def stages_of(session: AsyncSession, process_id: str) -> list[m.Stage]:
    """The stages of a process in position order."""
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
    """A stage of the process by key or id; 404 when absent."""
    for s in await stages_of(session, process.id):
        if s.key == ref or s.id == ref:
            return s
    raise NotFound("not_found", f"stage {ref!r} not found in process {process.key!r}", pointer="/stage")


async def current_placement(
    session: AsyncSession, target_type: TargetType, target_id: str, process_id: str | None = None
) -> m.StagePlacement | None:
    """The open placement of a target in a process, if any."""
    stmt = select(m.StagePlacement).where(
        m.StagePlacement.target_type == target_type,
        m.StagePlacement.target_id == target_id,
        m.StagePlacement.exited_at.is_(None),
        m.StagePlacement.deleted_at.is_(None),
    )
    if process_id:
        stmt = stmt.where(m.StagePlacement.process_id == process_id)
    return await session.scalar(stmt.order_by(m.StagePlacement.entered_at.desc()).limit(1))


async def wip_count(session: AsyncSession, stage_id: str) -> int:
    """How many not-done work items currently sit in the stage."""
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
    session: AsyncSession, ws_id: str, process: m.Process, now: datetime | None = None, acl: Any = None
) -> dict[str, Any]:
    """The board of a process: stages with the visible targets in each, WIP counts and ageing."""
    now = now or utcnow()
    cols = []
    for st in await stages_of(session, process.id):
        stmt = (
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
        if acl is not None:  # a board only shows cards the viewer may see (Inbox items never appear)
            stmt = visible(stmt, acl, m.WorkItem)
        rows = (await session.execute(stmt)).all()
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
