"""Projects: create, update, membership, top-down plan and baselines. (Phase 14)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from tt_schema.enums import (
    ProjectRole,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    access,
    core_projects,
    plan,
)
from tt_server.domain.plan import service as plan_svc

# ------------------------------------------------------------------ projects


class ProjectCreate(BaseModel):
    name: str
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{1,9}$")
    process: str | None = None
    team: str | None = Field(default=None, description="team slug")
    description: str | None = None


@command(
    "project.create", "projects", "Create a project", ProjectCreate, permission="project.create", preview=True,
    example_args={"name": "Payments", "key": "PAY", "process": "sw-delivery"}, example_text="create project Payments PAY",
)  # fmt: skip
async def project_create(ctx: Ctx, a: ProjectCreate) -> Outcome:
    team = await _team(ctx, a.team) if a.team else None
    if ctx.preview:
        return Outcome(
            preview={
                "affected": 1,
                "will_create": [f"project {a.key.upper()} “{a.name}”"]
                + ([f"inherits process {a.process}"] if a.process else []),
            }
        )
    body = core_projects.ProjectIn(
        name=a.name,
        key=a.key,
        process=a.process,
        team_id=team.id if team else None,
        description=a.description,
    )
    return _res(await core_projects.create_project(body, ctx.actor, ctx.uow))


class ProjectUpdate(BaseModel):
    project: str
    name: str | None = None
    description: str | None = None
    status: str | None = None


@command(
    "project.update", "projects", "Rename or change a project's status", ProjectUpdate, permission="project.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "name": "Payments v2"}, example_text="rename project PAY to Payments v2",
)  # fmt: skip
async def project_update(ctx: Ctx, a: ProjectUpdate) -> Outcome:
    body = core_projects.ProjectPatch(**a.model_dump(exclude={"project"}, exclude_none=True))
    return _res(await core_projects.patch_project(a.project, body, ctx.actor, ctx.uow))


class ProjectAddMember(BaseModel):
    project: str
    person: str
    role: ProjectRole = ProjectRole.CONTRIBUTOR


@command(
    "project.add_member", "projects", "Add a person to a project", ProjectAddMember, token_scope="admin", permission="project_member.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "person": "@sara", "role": "contributor"}, example_text="add @sara to PAY",
)  # fmt: skip
async def project_add_member(ctx: Ctx, a: ProjectAddMember) -> Outcome:
    return _res(
        await access.add_project_member(
            a.project, access.ProjectMemberIn(handle=a.person.lstrip("@"), role=a.role), ctx.actor, ctx.uow
        )
    )


class ProjectPlanCreate(BaseModel):
    project: str
    plan: dict[str, Any] = Field(
        description="the Phase 1 §1.2 plan payload (phases, deliverables, milestones)"
    )


@command(
    "project.plan.create", "projects", "Create a top-down plan (phases, deliverables, milestones)", ProjectPlanCreate, permission="project.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "plan": {"phases": []}}, example_text="plan PAY: discovery, build, launch",
)  # fmt: skip
async def project_plan_create(ctx: Ctx, a: ProjectPlanCreate) -> Outcome:
    return _res(await plan.create_plan(a.project, plan_svc.PlanIn.model_validate(a.plan), ctx.actor, ctx.uow))


class BaselineCapture(BaseModel):
    project: str
    name: str


@command(
    "project.baseline.capture", "projects", "Freeze the current plan as a named baseline", BaselineCapture, permission="project.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "name": "kickoff"}, example_text="baseline PAY as kickoff",
)  # fmt: skip
async def baseline_capture(ctx: Ctx, a: BaselineCapture) -> Outcome:
    return _res(await core_projects.capture_baseline(a.project, a.name, ctx.actor, ctx.uow))
