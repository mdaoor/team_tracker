"""Iterations, milestones and moving items into an iteration. (Phase 14)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.enums import (
    IterationState,
    MilestoneKind,
    TargetType,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    core,
    core_projects,
)
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.errors import NotFound


class MoveToIteration(BaseModel):
    keys: list[str] = Field(min_length=1)
    iteration: str = Field(description="iteration name or id within the items' project")


@command(
    "item.move_to_iteration", "iterations", "Put items into an iteration", MoveToIteration, permission="iteration.write", scope="items", scope_arg="keys", modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"keys": ["WEB-1"], "iteration": "Sprint 4"}, example_text="move WEB-1 to Sprint 4",
)  # fmt: skip
async def move_to_iteration(ctx: Ctx, a: MoveToIteration) -> Outcome:
    its = await _items(ctx, a.keys)
    s = ctx.uow.session
    for it in its:
        iteration = await s.scalar(select(m.Iteration).where(m.Iteration.project_id == it.project_id, (m.Iteration.name == a.iteration) | (m.Iteration.id == a.iteration)))  # fmt: skip
        if iteration is None:
            raise NotFound(
                "not_found",
                f"no iteration {a.iteration!r} in the project of {it.key}",
                pointer="/args/iteration",
            )
        it.iteration_id = iteration.id
        emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=it.id, event_type="item.updated", payload={"fields_changed": ["iteration_id"]})  # fmt: skip
    return Outcome(result={"moved": [i.key for i in its], "iteration": a.iteration})


# ------------------------------------------------------------------ iterations & milestones


class IterationCreate(BaseModel):
    project: str
    name: str
    start_date: date
    end_date: date
    goal: str | None = None


@command(
    "iteration.create", "iterations", "Create an iteration", IterationCreate, permission="iteration.write", scope="project", scope_arg="project", modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"project": "WEB", "name": "Sprint 4", "start_date": "2026-11-02", "end_date": "2026-11-13"}, example_text="new sprint 4 on WEB from Nov 2 to Nov 13",
)  # fmt: skip
async def iteration_create(ctx: Ctx, a: IterationCreate) -> Outcome:
    return _res(
        await core_projects.create_iteration(
            a.project, core_projects.IterationIn(**a.model_dump(exclude={"project"})), ctx.actor, ctx.uow
        )
    )


class IterationRef(BaseModel):
    project: str
    iteration: str = Field(description="iteration name or id")


async def _iteration(ctx: Ctx, project: str, ref: str) -> m.Iteration:
    p = await core._project(ctx.uow, ctx.actor.ws.id, project)
    it = await ctx.uow.session.scalar(select(m.Iteration).where(m.Iteration.project_id == p.id, (m.Iteration.name == ref) | (m.Iteration.id == ref)))  # fmt: skip
    if it is None:
        raise NotFound("not_found", f"no iteration {ref!r} in {p.key}", pointer="/args/iteration")
    return it


@command(
    "iteration.start", "iterations", "Start a planned iteration", IterationRef, permission="iteration.write", scope="project", scope_arg="project", modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"project": "WEB", "iteration": "Sprint 4"}, example_text="start sprint 4 on WEB",
)  # fmt: skip
async def iteration_start(ctx: Ctx, a: IterationRef) -> Outcome:
    it = await _iteration(ctx, a.project, a.iteration)
    return _res(
        await core_projects.iteration_state(
            it.id, core_projects.IterationState_(state=IterationState.ACTIVE), ctx.actor, ctx.uow
        )
    )


@command(
    "iteration.close", "iterations", "Close the active iteration", IterationRef, permission="iteration.write", scope="project", scope_arg="project", destructive=True, modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"project": "WEB", "iteration": "Sprint 4"}, example_text="close sprint 4 on WEB",
)  # fmt: skip
async def iteration_close(ctx: Ctx, a: IterationRef) -> Outcome:
    it = await _iteration(ctx, a.project, a.iteration)
    return _res(
        await core_projects.iteration_state(
            it.id, core_projects.IterationState_(state=IterationState.CLOSED), ctx.actor, ctx.uow
        )
    )


class MilestoneCreate(BaseModel):
    project: str
    name: str
    due_date: date
    kind: MilestoneKind = MilestoneKind.DEADLINE


@command(
    "milestone.create", "iterations", "Create a milestone", MilestoneCreate, permission="milestone.write", scope="project", scope_arg="project", modes=("pm", "manager", "admin"),
    example_args={"project": "WEB", "name": "Beta", "due_date": "2026-12-01"}, example_text="milestone Beta on WEB due Dec 1",
)  # fmt: skip
async def milestone_create(ctx: Ctx, a: MilestoneCreate) -> Outcome:
    return _res(
        await core_projects.create_milestone(
            a.project, core_projects.MilestoneIn(**a.model_dump(exclude={"project"})), ctx.actor, ctx.uow
        )
    )


class MilestoneRef(BaseModel):
    project: str
    milestone: str = Field(description="milestone name or id")


@command(
    "milestone.complete", "iterations", "Complete a milestone", MilestoneRef, permission="milestone.write", scope="project", scope_arg="project", modes=("pm", "manager", "admin"),
    example_args={"project": "WEB", "milestone": "Beta"}, example_text="milestone Beta on WEB is done",
)  # fmt: skip
async def milestone_complete(ctx: Ctx, a: MilestoneRef) -> Outcome:
    p = await core._project(ctx.uow, ctx.actor.ws.id, a.project)
    ms = await ctx.uow.session.scalar(select(m.Milestone).where(m.Milestone.project_id == p.id, (m.Milestone.name == a.milestone) | (m.Milestone.id == a.milestone)))  # fmt: skip
    if ms is None:
        raise NotFound("not_found", f"no milestone {a.milestone!r} in {p.key}", pointer="/args/milestone")
    return _res(await core_projects.complete_milestone(ms.id, ctx.actor, ctx.uow))
