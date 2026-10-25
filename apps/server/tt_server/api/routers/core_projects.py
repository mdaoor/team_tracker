"""Project endpoints: projects, iterations, milestones and baselines."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.enums import (
    IterationState,
    MilestoneKind,
    ProjectKind,
    ProjectStatus,
    Visibility,
)

from tt_server.acl.visibility import visible
from tt_server.api.authz import check, check_project, needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.routers.core import _project, _project_of
from tt_server.clock import person_today
from tt_server.db import models as m
from tt_server.domain.common import row_dict
from tt_server.domain.overlay import process as proc
from tt_server.domain.projects import planning
from tt_server.domain.projects import service as projects

router = APIRouter(prefix="/api/v1/w/{ws}")


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
    visibility: Visibility | None = None
    default_process_id: str | None = None
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    aliases: list[str] | None = None


@router.patch("/projects/{key}", tags=["projects"], dependencies=[needs("project.write", project="key")])
async def patch_project(key: str, body: ProjectPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    if body.visibility is not None and body.visibility != p.visibility:
        check(actor, "project.write")
    await planning.patch_project(uow, actor.ws, actor.person.id, p, body.model_dump(exclude_none=True))
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
    it = await planning.create_iteration(uow, actor.ws, actor.person.id, p, body.model_dump())
    await uow.commit()
    return row_dict(it)


class IterationState_(BaseModel):
    state: IterationState


@router.post("/iterations/{iteration_id}/state", tags=["projects"])
async def iteration_state(
    iteration_id: str, body: IterationState_, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    it = await planning.get_iteration(uow, actor.ws.id, iteration_id)
    await check_project(actor, uow, "iteration.write", await _project_of(uow, actor, it.project_id))
    await planning.set_iteration_state(uow, actor.ws, actor.person.id, it, body.state)
    await uow.commit()
    return row_dict(it)


@router.get("/iterations", tags=["projects"])
async def list_iterations(
    actor: CurrentActor, uow: Uow, state: IterationState | None = None
) -> dict[str, Any]:
    rows = await planning.list_iterations(uow, actor.ws.id, actor.acl, state=state)
    return {"data": [{**row_dict(i), "project_key": k} for i, k in rows], "next_cursor": None}


class MilestoneIn(BaseModel):
    __module__ = "tt_server.api.routers.core"  # pins the published OpenAPI schema name

    name: str
    due_date: date
    kind: MilestoneKind = MilestoneKind.DEADLINE
    description: str | None = None


MilestoneIn.model_rebuild(_types_namespace={"date": date, "MilestoneKind": MilestoneKind})


@router.post(
    "/projects/{key}/milestones",
    status_code=201,
    tags=["projects"],
    dependencies=[needs("milestone.write", project="key")],
)
async def create_milestone(key: str, body: MilestoneIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    ms = await planning.create_milestone(uow, actor.ws, actor.person.id, p, body.model_dump())
    await uow.commit()
    return row_dict(ms)


@router.get("/milestones", tags=["projects"])
async def list_milestones(
    actor: CurrentActor, uow: Uow, upcoming: str | None = Query(default=None, pattern=r"^\d{1,4}d$")
) -> dict[str, Any]:
    window = (person_today(actor.person), int(upcoming[:-1])) if upcoming else None
    rows = await planning.list_milestones(uow, actor.ws.id, actor.acl, today_plus=window)
    return {"data": [{**row_dict(ms), "project_key": k} for ms, k in rows], "next_cursor": None}


@router.post("/milestones/{milestone_id}/complete", tags=["projects"])
async def complete_milestone(milestone_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    ms = await planning.get_milestone(uow, actor.ws.id, milestone_id)
    await check_project(actor, uow, "milestone.write", await _project_of(uow, actor, ms.project_id))
    await planning.complete_milestone(uow, actor.ws, actor.person.id, ms)
    await uow.commit()
    return row_dict(ms)


@router.post("/milestones/{milestone_id}/approval", status_code=201, tags=["projects"])
async def request_milestone_approval(milestone_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Gate completion behind an approval (approvers: project owner/managers, `settings.approvers`)."""
    ms = await planning.get_milestone(uow, actor.ws.id, milestone_id)
    await check_project(actor, uow, "approval.request", await _project_of(uow, actor, ms.project_id))
    appr = await planning.request_milestone_approval(uow, actor.ws, actor.person.id, ms)
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
    n = await planning.capture_baseline(uow, actor.ws, actor.person.id, p, name)
    await uow.commit()
    return {"baseline_name": name, "items": n}
