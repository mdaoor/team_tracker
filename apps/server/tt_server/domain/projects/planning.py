"""Project edits, iterations, milestones and baselines behind `/projects/{key}/...` and `/iterations`, `/milestones`."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import IterationState, StateCategory, TargetType, Visibility

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, Invalid, NotFound
from tt_server.domain.overlay import approvals, propagation

ITERATION_FLOW = {
    IterationState.PLANNED: {IterationState.ACTIVE},
    IterationState.ACTIVE: {IterationState.CLOSED},
    IterationState.CLOSED: set(),
}


async def patch_project(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Project, data: dict[str, Any]
) -> m.Project:
    """Apply a project edit: plain fields, visibility (propagates to items), default process, aliases.

    `data` holds only the fields the caller sent. The caller authorises visibility changes.
    """
    changed = []
    old_vis, old_process = p.visibility.value, p.default_process_id
    for f in ("name", "description", "status"):
        v = data.get(f)
        if v is not None and getattr(p, f) != v:
            setattr(p, f, v)
            changed.append(f)
    uow.context["actor_id"] = actor_id
    visibility: Visibility | None = data.get("visibility")
    if visibility is not None and visibility != p.visibility:
        p.visibility = visibility
        await propagation.project_visibility_changed(uow, ws, p, old_vis, actor=actor_id)
    process_id = data.get("default_process_id")
    if process_id is not None and process_id != p.default_process_id:
        proc_row = await uow.session.scalar(
            select(m.Process).where(m.Process.workspace_id == ws.id, m.Process.id == process_id)
        )
        if proc_row is None:
            raise Invalid(
                "unknown_process", "no such process in this workspace", pointer="/default_process_id"
            )
        p.default_process_id = proc_row.id
        await propagation.project_process_changed(uow, ws, p, old_process, actor=actor_id)
    if data.get("aliases") is not None:
        p.settings = {**(p.settings or {}), "aliases": data["aliases"]}
        changed.append("settings.aliases")
    if changed:
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=actor_id,
            entity_type=TargetType.PROJECT,
            entity_id=p.id,
            event_type="project.updated",
            payload={"fields_changed": changed},
        )
    return p


async def create_iteration(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Project, data: dict[str, Any]
) -> m.Iteration:
    """Create an iteration; its end may not precede its start."""
    if data["end_date"] < data["start_date"]:
        raise Invalid("invalid_range", "end_date precedes start_date", pointer="/end_date")
    it = m.Iteration(id=new_id(), project_id=p.id, **data)
    uow.session.add(it)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PROJECT,
        entity_id=p.id,
        event_type="iteration.created",
        payload={"after": {"iteration_id": it.id, "name": it.name}},
    )
    return it


async def get_iteration(uow: UnitOfWork, ws_id: str, iteration_id: str) -> m.Iteration:
    """An iteration of the workspace by id; 404 when absent."""
    it = await uow.session.scalar(
        select(m.Iteration)
        .join(m.Project, m.Project.id == m.Iteration.project_id)
        .where(m.Iteration.id == iteration_id, m.Project.workspace_id == ws_id)
    )
    if it is None:
        raise NotFound("not_found", "iteration not found")
    return it


async def set_iteration_state(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, it: m.Iteration, state: IterationState
) -> m.Iteration:
    """Move an iteration planned -> active -> closed (one active per project; closing totals completed points)."""
    if state not in ITERATION_FLOW[it.state]:
        raise Conflict("invalid_transition", f"cannot go from {it.state.value} to {state.value}")
    if state == IterationState.ACTIVE:
        other = await uow.session.scalar(
            select(m.Iteration.id).where(
                m.Iteration.project_id == it.project_id, m.Iteration.state == IterationState.ACTIVE
            )
        )
        if other:
            raise Conflict("iteration_active", "the project already has an active iteration")
    if state == IterationState.CLOSED:
        pts = await uow.session.scalar(
            select(func.coalesce(func.sum(m.WorkItem.estimate_value), 0))
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(m.WorkItem.iteration_id == it.id, m.WorkflowState.category == StateCategory.DONE)
        )
        it.completed_points = pts
    it.state = state
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PROJECT,
        entity_id=it.project_id,
        event_type="iteration.state_changed",
        payload={"after": {"iteration_id": it.id, "state": it.state.value}},
    )
    return it


async def list_iterations(
    uow: UnitOfWork, ws_id: str, acl: AclContext, *, state: IterationState | None
) -> list[tuple[m.Iteration, str]]:
    """Iterations of visible projects as (iteration, project key), by start date."""
    stmt = (
        select(m.Iteration, m.Project.key)
        .join(m.Project, m.Project.id == m.Iteration.project_id)
        .where(m.Project.workspace_id == ws_id, m.Iteration.deleted_at.is_(None))
    )
    stmt = visible(stmt, acl, m.Project)
    if state:
        stmt = stmt.where(m.Iteration.state == state)
    return [(i, k) for i, k in (await uow.session.execute(stmt.order_by(m.Iteration.start_date))).all()]


async def create_milestone(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Project, data: dict[str, Any]
) -> m.Milestone:
    """Create a milestone on a project."""
    ms = m.Milestone(id=new_id(), project_id=p.id, **data)
    uow.session.add(ms)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.MILESTONE,
        entity_id=ms.id,
        event_type="milestone.created",
        payload={"after": {"name": ms.name, "due_date": ms.due_date.isoformat()}},
    )
    return ms


async def list_milestones(
    uow: UnitOfWork, ws_id: str, acl: AclContext, *, today_plus: tuple[date, int] | None
) -> list[tuple[m.Milestone, str]]:
    """Milestones of visible projects as (milestone, project key); `today_plus=(today, days)` keeps open ones due soon."""
    stmt = (
        select(m.Milestone, m.Project.key)
        .join(m.Project, m.Project.id == m.Milestone.project_id)
        .where(m.Project.workspace_id == ws_id, m.Milestone.deleted_at.is_(None))
    )
    stmt = visible(stmt, acl, m.Project)
    if today_plus:
        today, days = today_plus
        stmt = stmt.where(
            m.Milestone.due_date >= today,
            m.Milestone.due_date <= today + timedelta(days=days),
            m.Milestone.completed_at.is_(None),
        )
    return [(ms, k) for ms, k in (await uow.session.execute(stmt.order_by(m.Milestone.due_date))).all()]


async def get_milestone(uow: UnitOfWork, ws_id: str, milestone_id: str) -> m.Milestone:
    """A milestone of the workspace by id; 404 when absent."""
    ms = await uow.session.scalar(
        select(m.Milestone)
        .join(m.Project, m.Project.id == m.Milestone.project_id)
        .where(m.Milestone.id == milestone_id, m.Project.workspace_id == ws_id)
    )
    if ms is None:
        raise NotFound("not_found", "milestone not found")
    return ms


async def complete_milestone(uow: UnitOfWork, ws: m.Workspace, actor_id: str, ms: m.Milestone) -> m.Milestone:
    """Mark a milestone complete (idempotent); a requested approval must be approved first."""
    if ms.approval_id and not await approvals.is_approved(uow.session, ms.approval_id):
        raise Conflict(
            "approval_required", "this milestone needs an approved approval before it can complete"
        )
    ms.completed_at = ms.completed_at or utcnow()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.MILESTONE,
        entity_id=ms.id,
        event_type="milestone.completed",
        payload={},
    )
    return ms


async def request_milestone_approval(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, ms: m.Milestone
) -> m.Approval:
    """Gate completion behind an approval (approvers: project owner/managers, `settings.approvers`)."""
    if ms.approval_id:
        raise Conflict("approval_exists", "an approval was already requested")
    uow.context["actor_id"] = actor_id
    appr = await approvals.request(
        uow,
        ws,
        project_id=ms.project_id,
        target_type=TargetType.MILESTONE,
        target_id=ms.id,
        title=f"Milestone {ms.name}",
        requested_by=actor_id,
    )
    ms.approval_id = appr.id
    return appr


async def capture_baseline(uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Project, name: str) -> int:
    """Snapshot planned dates and estimates of every live item of the project; returns the item count."""
    rows = (
        await uow.session.scalars(
            select(m.WorkItem).where(m.WorkItem.project_id == p.id, m.WorkItem.deleted_at.is_(None))
        )
    ).all()
    for it in rows:
        uow.session.add(
            m.Baseline(
                id=new_id(),
                project_id=p.id,
                work_item_id=it.id,
                baseline_name=name,
                captured_by=actor_id,
                planned_start=it.planned_start,
                planned_end=it.planned_end,
                estimate_value=it.estimate_value,
            )
        )
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PROJECT,
        entity_id=p.id,
        event_type="baseline.captured",
        payload={"after": {"name": name, "items": len(rows)}},
    )
    return len(rows)
