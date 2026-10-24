"""Workspace-scoped CRUD and read endpoints (tenancy, projects, items, lookups)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from tt_schema import tenancy as schema_tenancy
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    ContextKind,
    EventSource,
    IterationState,
    MembershipRole,
    MilestoneKind,
    Priority,
    ProjectKind,
    ProjectStatus,
    ReportingKind,
    ScopeType,
    StateCategory,
    TargetType,
    TeamKind,
    Visibility,
)

from tt_server.acl.visibility import visible
from tt_server.api.authz import check_project, needs
from tt_server.api.deps import CurrentActor, Uow, page
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.overlay import approvals
from tt_server.domain.projects import service as projects
from tt_server.domain.projects.service import workflow_states
from tt_server.domain.tenancy import service as tenancy

router = APIRouter(prefix="/api/v1/w/{ws}")
_ = schema_tenancy


def row_dict(row: Any, *exclude: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        if col.name in exclude:
            continue
        v = getattr(row, col.name)
        out[col.name] = v.value if hasattr(v, "value") and not isinstance(v, (str, int)) else v
    return out


async def _project(uow: Uow, ws_id: str, key: str) -> m.Project:
    stmt = select(m.Project).where(
        m.Project.workspace_id == ws_id,
        func.lower(m.Project.key) == key.lower(),
        m.Project.deleted_at.is_(None),
    )
    if (acl := uow.context.get("acl")) is not None:
        stmt = visible(stmt, acl, m.Project)  # invisible => 404, never 403 (ADR-0008)
    p = await uow.session.scalar(stmt)
    if p is None:
        raise ApiError(404, "not_found", "project not found")
    return p


async def _project_of(uow: Uow, actor: CurrentActor, project_id: str) -> m.Project:
    """Load a project the actor can see (404 otherwise) from its id."""
    p = await uow.session.get(m.Project, project_id)
    if p is None:
        raise ApiError(404, "not_found", "project not found")
    return await _project(uow, actor.ws.id, p.key)


async def _item(uow: Uow, ws_id: str, id_or_key: str) -> m.WorkItem:
    stmt = (
        select(m.WorkItem)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .where(
            m.Project.workspace_id == ws_id,
            m.WorkItem.deleted_at.is_(None),
            (m.WorkItem.id == id_or_key) | (func.lower(m.WorkItem.key) == id_or_key.lower()),
        )
    )
    if (acl := uow.context.get("acl")) is not None:
        stmt = visible(stmt, acl, m.WorkItem)
    it = await uow.session.scalar(stmt)
    if it is None:
        raise ApiError(404, "not_found", "item not found")
    return it


# ------------------------------------------------------------------ tenancy


class MemberIn(BaseModel):
    display_name: str
    email: str
    handle: str
    time_zone: str = "UTC"


@router.post("/members", status_code=201, tags=["tenancy"], dependencies=[needs("member.write")])
async def add_member(body: MemberIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    person = await tenancy.get_or_create_person(
        uow, display_name=body.display_name, email=body.email, time_zone=body.time_zone
    )
    member = await tenancy.add_member(uow, actor.ws, person, handle=body.handle, actor_id=actor.person.id)
    await uow.commit()
    return row_dict(member)


@router.get("/people", tags=["tenancy"])
async def people(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.execute(
            select(m.WorkspaceMember, m.Person)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(m.WorkspaceMember.workspace_id == actor.ws.id, m.WorkspaceMember.deleted_at.is_(None))
            .order_by(m.WorkspaceMember.handle)
        )
    ).all()
    return {
        "data": [
            {
                "person_id": p.id,
                "handle": mem.handle,
                "display_name": p.display_name,
                "email": p.email,
                "time_zone": p.time_zone,
                "status": mem.status.value,
            }
            for mem, p in rows
        ],
        "next_cursor": None,
    }


class TeamIn(BaseModel):
    name: str
    slug: str
    kind: TeamKind = TeamKind.ORG
    parent_team_id: str | None = None
    lead_id: str | None = None
    description: str | None = None


@router.post("/teams", status_code=201, tags=["tenancy"], dependencies=[needs("team.write")])
async def create_team(body: TeamIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    team = m.Team(id=new_id(), workspace_id=actor.ws.id, **body.model_dump())
    uow.session.add(team)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.TEAM,
        entity_id=team.id,
        event_type="team.created",
        payload={"after": {"slug": team.slug}},
    )
    await uow.commit()
    return row_dict(team)


@router.get("/teams", tags=["tenancy"])
async def list_teams(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.Team).where(m.Team.workspace_id == actor.ws.id, m.Team.deleted_at.is_(None))
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class MembershipIn(BaseModel):
    person_id: str
    role: MembershipRole = MembershipRole.MEMBER
    allocation_pct: int | None = Field(default=None, ge=0, le=100)


@router.post(
    "/teams/{team_id}/memberships", status_code=201, tags=["tenancy"], dependencies=[needs("team.write")]
)
async def add_membership(team_id: str, body: MembershipIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    team = await uow.session.scalar(
        select(m.Team).where(m.Team.id == team_id, m.Team.workspace_id == actor.ws.id)
    )
    if team is None:
        raise ApiError(404, "not_found", "team not found")
    row = m.Membership(id=new_id(), team_id=team.id, joined_at=utcnow(), **body.model_dump())
    uow.session.add(row)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.TEAM,
        entity_id=team.id,
        event_type="membership.changed",
        payload={"after": {"person_id": body.person_id, "role": body.role.value}},
    )
    await uow.commit()
    return row_dict(row)


@router.get("/teams/{team_id}/members", tags=["tenancy"])
async def team_members(
    team_id: str, actor: CurrentActor, uow: Uow, effective: bool = False
) -> dict[str, Any]:
    team_ids = {team_id}
    if effective:
        frontier = {team_id}
        while frontier:
            kids = (
                set(
                    (
                        await uow.session.scalars(
                            select(m.Team.id).where(
                                m.Team.workspace_id == actor.ws.id, m.Team.parent_team_id.in_(frontier)
                            )
                        )
                    ).all()
                )
                - team_ids
            )
            team_ids |= kids
            frontier = kids
    rows = (
        await uow.session.scalars(
            select(m.Membership).where(m.Membership.team_id.in_(team_ids), m.Membership.left_at.is_(None))
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class ReportingLineIn(BaseModel):
    person_id: str
    manager_id: str
    kind: ReportingKind = ReportingKind.LINE
    scope_type: ScopeType = ScopeType.NONE
    scope_id: str | None = None
    is_primary: bool = False


@router.post(
    "/reporting-lines", status_code=201, tags=["tenancy"], dependencies=[needs("reporting_line.write")]
)
async def add_reporting_line(body: ReportingLineIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    if (body.scope_type == ScopeType.NONE) != (body.scope_id is None):
        raise ApiError(
            400,
            "invalid_scope",
            "scope_id is required exactly when scope_type is not 'none'",
            pointer="/scope_id",
        )
    if body.person_id == body.manager_id:
        raise ApiError(400, "invalid_line", "a person cannot report to themselves", pointer="/manager_id")
    if body.is_primary and body.kind == ReportingKind.LINE:
        taken = await uow.session.scalar(
            select(m.ReportingLine.id).where(
                m.ReportingLine.person_id == body.person_id,
                m.ReportingLine.is_primary,
                m.ReportingLine.kind == ReportingKind.LINE,
                m.ReportingLine.deleted_at.is_(None),
            )
        )
        if taken:
            raise ApiError(
                409, "primary_exists", "person already has a primary line manager", pointer="/is_primary"
            )
    row = m.ReportingLine(id=new_id(), workspace_id=actor.ws.id, **body.model_dump())
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=body.person_id,
        event_type="reporting_line.changed",
        payload={"after": body.model_dump(mode="json")},
    )
    await uow.commit()
    return row_dict(row)


class ContextNoteIn(BaseModel):
    scope_type: ScopeType
    scope_id: str
    title: str
    body: str
    kind: ContextKind = ContextKind.OTHER
    pinned: bool = False
    visibility: Visibility = Visibility.INHERIT
    valid_from: date | None = None
    valid_to: date | None = None


@router.post("/context-notes", status_code=201, tags=["tenancy"], dependencies=[needs("context_note.write")])
async def create_note(body: ContextNoteIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = m.ContextNote(id=new_id(), workspace_id=actor.ws.id, author_id=actor.person.id, **body.model_dump())
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.CONTEXT_NOTE,
        entity_id=row.id,
        event_type="context_note.created",
        payload={"after": {"title": row.title}},
    )
    await uow.commit()
    return row_dict(row)


@router.get("/context-notes", tags=["tenancy"])
async def list_notes(
    actor: CurrentActor, uow: Uow, scope_type: ScopeType | None = None, scope_id: str | None = None
) -> dict[str, Any]:
    stmt = select(m.ContextNote).where(
        m.ContextNote.workspace_id == actor.ws.id, m.ContextNote.deleted_at.is_(None)
    )
    if scope_type:
        stmt = stmt.where(m.ContextNote.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(m.ContextNote.scope_id == scope_id)
    rows = (
        await uow.session.scalars(stmt.order_by(m.ContextNote.pinned.desc(), m.ContextNote.created_at.desc()))
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


# ------------------------------------------------------------------ projects


class ProjectIn(BaseModel):
    name: str
    key: str
    owner_id: str | None = None
    description: str | None = None
    team_id: str | None = None
    process: str | None = Field(
        default=None, description="process key; default: the workspace's default process"
    )
    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/projects", status_code=201, tags=["projects"], dependencies=[needs("project.create")])
async def create_project(body: ProjectIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if body.process:
        from tt_server.domain.overlay import process as proc

        extra["default_process_id"] = (await proc.get_process(uow.session, actor.ws.id, body.process)).id
    p = await projects.create_project(
        uow,
        actor.ws,
        key=body.key,
        name=body.name,
        owner_id=body.owner_id or actor.person.id,
        actor_id=actor.person.id,
        description=body.description,
        team_id=body.team_id,
        settings=body.settings,
        **extra,
    )
    await uow.commit()
    return row_dict(p)


@router.get("/projects", tags=["projects"])
async def list_projects(actor: CurrentActor, uow: Uow, fields: str | None = None) -> dict[str, Any]:
    stmt = visible(
        select(m.Project).where(
            m.Project.workspace_id == actor.ws.id,
            m.Project.deleted_at.is_(None),
            m.Project.kind != ProjectKind.INBOX,
        ),
        actor.acl,
        m.Project,
    )
    rows = (await uow.session.scalars(stmt.order_by(m.Project.key))).all()
    out = []
    for p in rows:
        d = row_dict(p)
        d["aliases"] = (p.settings or {}).get("aliases", [])
        out.append({k: d[k] for k in fields.split(",") if k in d} if fields else d)
    return {"data": out, "next_cursor": None}


@router.get("/projects/{key}", tags=["projects"])
async def get_project(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return row_dict(await _project(uow, actor.ws.id, key))


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    aliases: list[str] | None = None


@router.patch("/projects/{key}", tags=["projects"], dependencies=[needs("project.write", project="key")])
async def patch_project(key: str, body: ProjectPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    changed = []
    for f in ("name", "description", "status"):
        v = getattr(body, f)
        if v is not None and getattr(p, f) != v:
            setattr(p, f, v)
            changed.append(f)
    if body.aliases is not None:
        p.settings = {**(p.settings or {}), "aliases": body.aliases}
        changed.append("settings.aliases")
    if changed:
        emit(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            entity_type=TargetType.PROJECT,
            entity_id=p.id,
            event_type="project.updated",
            payload={"fields_changed": changed},
        )
    await uow.commit()
    return row_dict(p)


class IterationIn(BaseModel):
    name: str
    start_date: date
    end_date: date
    goal: str | None = None


@router.post(
    "/projects/{key}/iterations",
    status_code=201,
    tags=["projects"],
    dependencies=[needs("iteration.write", project="key")],
)
async def create_iteration(key: str, body: IterationIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    if body.end_date < body.start_date:
        raise ApiError(400, "invalid_range", "end_date precedes start_date", pointer="/end_date")
    it = m.Iteration(id=new_id(), project_id=p.id, **body.model_dump())
    uow.session.add(it)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PROJECT,
        entity_id=p.id,
        event_type="iteration.created",
        payload={"after": {"iteration_id": it.id, "name": it.name}},
    )
    await uow.commit()
    return row_dict(it)


ITERATION_FLOW = {
    IterationState.PLANNED: {IterationState.ACTIVE},
    IterationState.ACTIVE: {IterationState.CLOSED},
    IterationState.CLOSED: set(),
}


class IterationState_(BaseModel):
    state: IterationState


@router.post("/iterations/{iteration_id}/state", tags=["projects"])
async def iteration_state(
    iteration_id: str, body: IterationState_, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    it = await uow.session.scalar(
        select(m.Iteration)
        .join(m.Project, m.Project.id == m.Iteration.project_id)
        .where(m.Iteration.id == iteration_id, m.Project.workspace_id == actor.ws.id)
    )
    if it is None:
        raise ApiError(404, "not_found", "iteration not found")
    await check_project(actor, uow, "iteration.write", await _project_of(uow, actor, it.project_id))
    if body.state not in ITERATION_FLOW[it.state]:
        raise ApiError(409, "invalid_transition", f"cannot go from {it.state.value} to {body.state.value}")
    if body.state == IterationState.ACTIVE:
        other = await uow.session.scalar(
            select(m.Iteration.id).where(
                m.Iteration.project_id == it.project_id, m.Iteration.state == IterationState.ACTIVE
            )
        )
        if other:
            raise ApiError(409, "iteration_active", "the project already has an active iteration")
    if body.state == IterationState.CLOSED:
        pts = await uow.session.scalar(
            select(func.coalesce(func.sum(m.WorkItem.estimate_value), 0))
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(m.WorkItem.iteration_id == it.id, m.WorkflowState.category == StateCategory.DONE)
        )
        it.completed_points = pts
    it.state = body.state
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PROJECT,
        entity_id=it.project_id,
        event_type="iteration.state_changed",
        payload={"after": {"iteration_id": it.id, "state": it.state.value}},
    )
    await uow.commit()
    return row_dict(it)


@router.get("/iterations", tags=["projects"])
async def list_iterations(
    actor: CurrentActor, uow: Uow, state: IterationState | None = None
) -> dict[str, Any]:
    stmt = (
        select(m.Iteration, m.Project.key)
        .join(m.Project, m.Project.id == m.Iteration.project_id)
        .where(m.Project.workspace_id == actor.ws.id, m.Iteration.deleted_at.is_(None))
    )
    stmt = visible(stmt, actor.acl, m.Project)
    if state:
        stmt = stmt.where(m.Iteration.state == state)
    rows = (await uow.session.execute(stmt.order_by(m.Iteration.start_date))).all()
    return {"data": [{**row_dict(i), "project_key": k} for i, k in rows], "next_cursor": None}


class MilestoneIn(BaseModel):
    name: str
    due_date: date
    kind: MilestoneKind = MilestoneKind.DEADLINE
    description: str | None = None


@router.post(
    "/projects/{key}/milestones",
    status_code=201,
    tags=["projects"],
    dependencies=[needs("milestone.write", project="key")],
)
async def create_milestone(key: str, body: MilestoneIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    ms = m.Milestone(id=new_id(), project_id=p.id, **body.model_dump())
    uow.session.add(ms)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.MILESTONE,
        entity_id=ms.id,
        event_type="milestone.created",
        payload={"after": {"name": ms.name, "due_date": ms.due_date.isoformat()}},
    )
    await uow.commit()
    return row_dict(ms)


@router.get("/milestones", tags=["projects"])
async def list_milestones(
    actor: CurrentActor, uow: Uow, upcoming: str | None = Query(default=None, pattern=r"^\d+d$")
) -> dict[str, Any]:
    stmt = (
        select(m.Milestone, m.Project.key)
        .join(m.Project, m.Project.id == m.Milestone.project_id)
        .where(m.Project.workspace_id == actor.ws.id, m.Milestone.deleted_at.is_(None))
    )
    stmt = visible(stmt, actor.acl, m.Project)
    if upcoming:
        today = date.today()
        stmt = stmt.where(
            m.Milestone.due_date >= today,
            m.Milestone.due_date <= today + timedelta(days=int(upcoming[:-1])),
            m.Milestone.completed_at.is_(None),
        )
    rows = (await uow.session.execute(stmt.order_by(m.Milestone.due_date))).all()
    return {"data": [{**row_dict(ms), "project_key": k} for ms, k in rows], "next_cursor": None}


@router.post("/milestones/{milestone_id}/complete", tags=["projects"])
async def complete_milestone(milestone_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    ms = await uow.session.scalar(
        select(m.Milestone)
        .join(m.Project, m.Project.id == m.Milestone.project_id)
        .where(m.Milestone.id == milestone_id, m.Project.workspace_id == actor.ws.id)
    )
    if ms is None:
        raise ApiError(404, "not_found", "milestone not found")
    await check_project(actor, uow, "milestone.write", await _project_of(uow, actor, ms.project_id))
    if ms.approval_id and not await approvals.is_approved(uow.session, ms.approval_id):
        raise ApiError(
            409, "approval_required", "this milestone needs an approved approval before it can complete"
        )
    ms.completed_at = ms.completed_at or utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.MILESTONE,
        entity_id=ms.id,
        event_type="milestone.completed",
        payload={},
    )
    await uow.commit()
    return row_dict(ms)


@router.post("/milestones/{milestone_id}/approval", status_code=201, tags=["projects"])
async def request_milestone_approval(milestone_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Gate completion behind an approval (approvers: project owner/managers, `settings.approvers`)."""
    ms = await uow.session.scalar(
        select(m.Milestone)
        .join(m.Project, m.Project.id == m.Milestone.project_id)
        .where(m.Milestone.id == milestone_id, m.Project.workspace_id == actor.ws.id)
    )
    if ms is None:
        raise ApiError(404, "not_found", "milestone not found")
    await check_project(actor, uow, "approval.request", await _project_of(uow, actor, ms.project_id))
    if ms.approval_id:
        raise ApiError(409, "approval_exists", "an approval was already requested")
    uow.context["actor_id"] = actor.person.id
    appr = await approvals.request(
        uow,
        actor.ws,
        project_id=ms.project_id,
        target_type=TargetType.MILESTONE,
        target_id=ms.id,
        title=f"Milestone {ms.name}",
        requested_by=actor.person.id,
    )
    ms.approval_id = appr.id
    await uow.commit()
    return row_dict(appr)


@router.post(
    "/projects/{key}/baselines",
    status_code=201,
    tags=["projects"],
    dependencies=[needs("project.write", project="key")],
)
async def capture_baseline(key: str, name: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
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
                captured_by=actor.person.id,
                planned_start=it.planned_start,
                planned_end=it.planned_end,
                estimate_value=it.estimate_value,
            )
        )
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PROJECT,
        entity_id=p.id,
        event_type="baseline.captured",
        payload={"after": {"name": name, "items": len(rows)}},
    )
    await uow.commit()
    return {"baseline_name": name, "items": len(rows)}


# ------------------------------------------------------------------ items


class ItemIn(BaseModel):
    title: str
    project: str
    type_id: str | None = None
    parent: str | None = None
    description: str | None = None
    priority: Priority = Priority.MEDIUM
    assignee_id: str | None = None


@router.post("/items", status_code=201, tags=["items"])
async def create_item(body: ItemIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, body.project)
    await check_project(actor, uow, "item.write_overlay", p)
    parent = await _item(uow, actor.ws.id, body.parent) if body.parent else None
    item = await items.create_item(
        uow,
        actor.ws,
        p,
        title=body.title,
        reporter_id=actor.person.id,
        assignee_id=body.assignee_id or actor.person.id,
        type_id=body.type_id,
        parent=parent,
        description=body.description,
        priority=body.priority,
    )
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.created",
        payload={"after": {"key": item.key, "title": item.title}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return row_dict(item)


async def _item_detail(uow: Uow, item: m.WorkItem) -> dict[str, Any]:
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


@router.get("/items/{id_or_key}", tags=["items"])
async def get_item(id_or_key: str, actor: CurrentActor, uow: Uow, response: Response) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    response.headers["ETag"] = f'"{item.updated_at.isoformat()}"'
    return await _item_detail(uow, item)


@router.get("/items", tags=["items"])
async def list_items(
    actor: CurrentActor,
    uow: Uow,
    assignee: str | None = None,
    state_category: str | None = None,
    project: str | None = None,
    team: str | None = None,
    q: str | None = None,
    fields: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    stmt = (
        select(m.WorkItem, m.Project.key)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(m.Project.workspace_id == actor.ws.id, m.WorkItem.deleted_at.is_(None))
    )
    stmt = visible(stmt, actor.acl, m.WorkItem)
    if assignee:
        pid = (
            actor.person.id
            if assignee == "me"
            else await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id, m.WorkspaceMember.handle == assignee
                )
            )
        )
        stmt = stmt.where(m.WorkItem.assignee_id == pid)
    if team:  # items of a team and its sub-teams
        root = await uow.session.scalar(
            select(m.Team.id).where(
                m.Team.workspace_id == actor.ws.id, (m.Team.slug == team) | (m.Team.id == team)
            )
        )
        ids = actor.acl.team_descendants(root) if root else set()
        if root:
            frontier = {root}
            while frontier:
                kids = (
                    set(
                        (
                            await uow.session.scalars(
                                select(m.Team.id).where(m.Team.parent_team_id.in_(frontier))
                            )
                        ).all()
                    )
                    - ids
                )
                ids |= kids
                frontier = kids
        stmt = stmt.where(m.WorkItem.team_id.in_(ids or {""}), m.Project.kind != ProjectKind.INBOX)
    if state_category:
        cats = [StateCategory(c) for c in state_category.split(",")]
        stmt = stmt.where(m.WorkflowState.category.in_(cats))
    if project:
        stmt = stmt.where(func.lower(m.Project.key) == project.lower())
    if q:
        stmt = stmt.where(m.WorkItem.title.ilike(f"%{q}%"))
    if cursor:
        stmt = stmt.where(m.WorkItem.id > cursor)
    rows = (await uow.session.execute(stmt.order_by(m.WorkItem.id).limit(limit + 1))).all()
    objs = []
    for it, pkey in rows:
        d = row_dict(it)
        d["project_key"] = pkey
        objs.append(type("R", (), {"id": it.id, "d": d}))
    envelope = page(objs, limit)
    data = [o.d for o in envelope["data"]]
    if fields:
        wanted = fields.split(",")
        data = [{k: v for k, v in d.items() if k in wanted} for d in data]
    return {"data": data, "next_cursor": envelope["next_cursor"]}


class StateIn(BaseModel):
    state: str


@router.post(
    "/items/{id_or_key}/state", tags=["items"], dependencies=[needs("item.write_overlay", item="id_or_key")]
)
async def change_state(id_or_key: str, body: StateIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    proj = await uow.session.get(m.Project, item.project_id)
    assert proj is not None  # noqa: S101
    states = await workflow_states(uow.session, proj.workflow_id)
    target = next((s for s in states if s.key == body.state), None)
    if target is None:
        raise ApiError(
            400, "unknown_state", f"state {body.state!r} is not in the project workflow", pointer="/state"
        )
    old, new = await items.set_state(uow, item, target)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.reopened"
        if old == StateCategory.DONE and new != StateCategory.DONE
        else "item.state_changed",
        payload={"before": {"category": old.value}, "after": {"category": new.value, "state": target.key}},
    )
    await uow.commit()
    return row_dict(item)


class MoveIn(BaseModel):
    parent: str | None = None


@router.post(
    "/items/{id_or_key}/move", tags=["items"], dependencies=[needs("item.write_overlay", item="id_or_key")]
)
async def move(id_or_key: str, body: MoveIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, id_or_key)
    parent = await _item(uow, actor.ws.id, body.parent) if body.parent else None
    if parent is not None:
        await items.validate_nesting(uow.session, actor.ws, item.type_id, parent)
    await items.move_item(uow.session, item, parent)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.parent_changed",
        payload={"after": {"parent_id": item.parent_id}},
    )
    await uow.commit()
    return row_dict(item)


@router.get("/tags", tags=["items"])
async def list_tags(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.Tag)
            .where(m.Tag.workspace_id == actor.ws.id, m.Tag.deleted_at.is_(None))
            .order_by(m.Tag.name)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post(
    "/tags/{tag_id}/merge-into/{other_id}", tags=["items"], dependencies=[needs("classification.write")]
)
async def merge_tags(tag_id: str, other_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    src, dst = (await uow.session.get(m.Tag, tag_id), await uow.session.get(m.Tag, other_id))
    if (
        not src
        or not dst
        or src.workspace_id != actor.ws.id
        or dst.workspace_id != actor.ws.id
        or src.id == dst.id
    ):
        raise ApiError(404, "not_found", "tag not found")
    have = set(
        (await uow.session.scalars(select(m.TagLink.target_id).where(m.TagLink.tag_id == dst.id))).all()
    )
    moved = 0
    for link in (await uow.session.scalars(select(m.TagLink).where(m.TagLink.tag_id == src.id))).all():
        if link.target_id in have:
            link.deleted_at = utcnow()
        else:
            link.tag_id = dst.id
            moved += 1
    src.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="tag.merged",
        payload={"before": {"tag_id": src.id}, "after": {"tag_id": dst.id, "links_moved": moved}},
    )
    await uow.commit()
    return {"merged_into": dst.id, "links_moved": moved}
