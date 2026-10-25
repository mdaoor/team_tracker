"""Read models of work items behind `GET /items` and `GET /items/{id}`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from tt_schema.base import utcnow
from tt_schema.enums import ProjectKind, StateCategory, TargetType

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import row_dict, team_subtree_ids
from tt_server.domain.names import Names


async def item_detail(uow: UnitOfWork, item: m.WorkItem) -> dict[str, Any]:
    """The item row plus its latest activity/estimate, evidence and time totals, people, state and stage."""
    s = uow.session
    d = row_dict(item)
    act = await s.scalar(
        select(m.Activity)
        .where(m.Activity.work_item_id == item.id)
        .order_by(m.Activity.started_at.desc())
        .limit(1)
    )
    est = await s.scalar(
        select(m.Estimate)
        .where(m.Estimate.work_item_id == item.id)
        .order_by(m.Estimate.created_at.desc())
        .limit(1)
    )
    d["latest_activity"] = row_dict(act) if act else None
    d["latest_estimate"] = row_dict(est) if est else None
    d["evidence_count"] = await s.scalar(
        select(func.count()).select_from(m.Evidence).where(m.Evidence.work_item_id == item.id)
    )
    mins = await s.scalar(
        select(func.coalesce(func.sum(m.TimeEntry.minutes), 0)).where(m.TimeEntry.work_item_id == item.id)
    )
    d["hours_logged"] = round((mins or 0) / 60, 2)
    proj = await s.get(m.Project, item.project_id)
    d["project_key"] = proj.key if proj else None
    people = await Names.load(
        s, proj.workspace_id if proj else "", [("person", item.reporter_id), ("person", item.assignee_id)]
    )
    d["reporter"], d["assignee"] = people.person(item.reporter_id), people.person(item.assignee_id)
    state = await s.get(m.WorkflowState, item.state_id)
    d["state"], d["state_category"] = (state.key, state.category.value) if state else (None, None)
    placement = (
        await s.execute(
            select(m.Stage.key, m.StagePlacement.entered_at)
            .join(m.StagePlacement, m.StagePlacement.stage_id == m.Stage.id)
            .where(
                m.StagePlacement.target_type == TargetType.WORK_ITEM,
                m.StagePlacement.target_id == item.id,
                m.StagePlacement.exited_at.is_(None),
            )
            .limit(1)
        )
    ).first()
    d["stage"] = placement[0] if placement else None
    d["stage_age_days"] = (utcnow() - placement[1]).days if placement else None
    return d


async def list_items(
    uow: UnitOfWork,
    ws_id: str,
    acl: AclContext,
    person_id: str,
    *,
    assignee: str | None,
    state_categories: list[StateCategory] | None,
    project: str | None,
    team: str | None,
    q: str | None,
    cursor: str | None,
    limit: int,
) -> list[tuple[m.WorkItem, str]]:
    """Visible items as (item, project key) in id order after `cursor`; fetches `limit + 1` rows to detect a next page."""
    stmt = (
        select(m.WorkItem, m.Project.key)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(m.Project.workspace_id == ws_id, m.WorkItem.deleted_at.is_(None))
    )
    stmt = visible(stmt, acl, m.WorkItem)
    if assignee:
        pid = (
            person_id
            if assignee == "me"
            else await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.handle == assignee
                )
            )
        )
        stmt = stmt.where(m.WorkItem.assignee_id == pid)
    if team:  # items of a team and its sub-teams
        root = await uow.session.scalar(
            select(m.Team.id).where(m.Team.workspace_id == ws_id, (m.Team.slug == team) | (m.Team.id == team))
        )
        ids = acl.team_descendants(root) if root else set()
        if root:
            ids |= await team_subtree_ids(uow.session, ws_id, {root})
        stmt = stmt.where(m.WorkItem.team_id.in_(ids or {""}), m.Project.kind != ProjectKind.INBOX)
    if state_categories:
        stmt = stmt.where(m.WorkflowState.category.in_(state_categories))
    if project:
        stmt = stmt.where(func.lower(m.Project.key) == project.lower())
    if q:
        stmt = stmt.where(m.WorkItem.title.ilike(f"%{q}%"))
    if cursor:
        stmt = stmt.where(m.WorkItem.id > cursor)
    return [
        (it, pkey)
        for it, pkey in (await uow.session.execute(stmt.order_by(m.WorkItem.id).limit(limit + 1))).all()
    ]
