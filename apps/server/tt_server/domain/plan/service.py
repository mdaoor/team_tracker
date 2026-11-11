"""Top-down project plan (plan P1 §1.2): phases → deliverables → milestones → dependencies, with statuses derived on read.

A project is fully plannable with zero capture rows. Nothing here is stored as a status: `plan_status` is a function of dates,
state categories and whether any task or activity exists, so it can never disagree with the work.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.enums import (
    EventSource,
    ItemTypeKey,
    LinkType,
    MilestoneKind,
    TargetType,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import DomainError, Invalid
from tt_server.domain.items import service as items
from tt_server.domain.plan.read import _iso as _iso
from tt_server.domain.plan.read import _parent as _parent
from tt_server.domain.plan.read import get_plan as get_plan
from tt_server.domain.plan.read import snapshot_parts as snapshot_parts
from tt_server.domain.plan.read import today_in as today_in
from tt_server.domain.plan.status import AT_RISK_DAYS as AT_RISK_DAYS
from tt_server.domain.plan.status import AT_RISK_PROGRESS as AT_RISK_PROGRESS
from tt_server.domain.plan.status import DUE_SOON_DAYS as DUE_SOON_DAYS
from tt_server.domain.plan.status import deliverable_status as deliverable_status
from tt_server.domain.plan.status import milestone_status as milestone_status
from tt_server.domain.plan.status import project_health as project_health
from tt_server.domain.projects.service import item_type


class DeliverableIn(BaseModel):
    """Input shape of a deliverable inside a submitted plan."""

    ref: str | None = None  # a local name other deliverables can depend on
    title: str = Field(min_length=1)
    planned_start: date | None = None
    planned_end: date | None = None
    owner: str | None = None  # @handle
    milestone: str | None = None  # a milestone name from this plan, or an existing one
    depends_on: list[str] = Field(default_factory=list)  # refs or titles of other deliverables in this plan
    lag_days: int | None = None


class PhaseIn(BaseModel):
    """Input shape of a plan phase with its deliverables."""

    title: str = Field(min_length=1)
    planned_start: date | None = None
    planned_end: date | None = None
    deliverables: list[DeliverableIn] = Field(default_factory=list)


class MilestoneIn(BaseModel):
    """Input shape of a plan milestone."""

    name: str = Field(min_length=1)
    kind: MilestoneKind = MilestoneKind.OTHER
    due_date: date
    description: str | None = None


class PlanIn(BaseModel):
    """Input shape of a whole plan: phases and milestones."""

    phases: list[PhaseIn] = Field(default_factory=list)
    milestones: list[MilestoneIn] = Field(default_factory=list)


def _bad(msg: str, pointer: str) -> DomainError:
    return Invalid("invalid_plan", msg, pointer=pointer)


async def create_plan(
    uow: UnitOfWork, ws: m.Workspace, project: m.Project, plan: PlanIn, *, actor_id: str
) -> dict[str, Any]:
    """Create the whole plan in the caller's transaction (all or nothing) and emit `plan.created`."""
    s = uow.session
    phase_t = await item_type(s, ws.id, ItemTypeKey.PHASE, project.id)
    deliv_t = await item_type(s, ws.id, ItemTypeKey.DELIVERABLE, project.id)
    ms_rows: dict[str, m.Milestone] = {}
    for i, mi in enumerate(plan.milestones):
        if mi.name in ms_rows:
            raise _bad(f"duplicate milestone {mi.name!r}", f"/milestones/{i}/name")
        row = m.Milestone(
            id=_id(),
            project_id=project.id,
            name=mi.name,
            due_date=mi.due_date,
            description=mi.description,
            kind=mi.kind,
        )
        s.add(row)
        ms_rows[mi.name] = row
    existing = {
        r.name: r
        for r in (await s.scalars(select(m.Milestone).where(m.Milestone.project_id == project.id))).all()
    }
    handles = {
        h: p
        for h, p in (
            await s.execute(
                select(m.WorkspaceMember.handle, m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == ws.id
                )
            )
        ).all()
    }
    created: dict[str, m.WorkItem] = {}
    by_name: dict[str, m.WorkItem] = {}
    pending_deps: list[tuple[m.WorkItem, str, int | None, str]] = []
    n_items = 0
    for pi, ph in enumerate(plan.phases):
        _check_dates(ph.planned_start, ph.planned_end, f"/phases/{pi}")
        phase = await items.create_item(
            uow, ws, project, title=ph.title, reporter_id=actor_id, type_id=phase_t.id,
            source={"origin": "plan"}, planned_start=ph.planned_start, planned_end=ph.planned_end,
        )  # fmt: skip
        n_items += 1
        for di, d in enumerate(ph.deliverables):
            ptr = f"/phases/{pi}/deliverables/{di}"
            _check_dates(d.planned_start, d.planned_end, ptr)
            owner = None
            if d.owner:
                owner = handles.get(d.owner if d.owner.startswith("@") else "@" + d.owner)
                if owner is None:
                    raise _bad(f"unknown owner {d.owner}", ptr + "/owner")
            ms = ms_rows.get(d.milestone or "") or existing.get(d.milestone or "")
            if d.milestone and ms is None:
                raise _bad(f"unknown milestone {d.milestone!r}", ptr + "/milestone")
            item = await items.create_item(
                uow, ws, project, title=d.title, reporter_id=actor_id, assignee_id=owner, type_id=deliv_t.id, parent=phase,
                source={"origin": "plan"}, planned_start=d.planned_start, planned_end=d.planned_end,
                milestone_id=ms.id if ms else None,
            )  # fmt: skip
            n_items += 1
            for name in {d.ref, d.title} - {None}:
                if name in created:
                    if name == d.ref:
                        raise _bad(f"duplicate ref {name!r}", ptr + "/ref")
                    continue
                created[str(name)] = item
            by_name[d.title] = item
            for dep in d.depends_on:
                pending_deps.append((item, dep, d.lag_days, ptr + "/depends_on"))
    n_links = 0
    for item, dep, lag, ptr in pending_deps:
        pred = created.get(dep)
        if pred is None:
            raise _bad(f"depends_on {dep!r} is not a deliverable of this plan", ptr)
        if pred.id == item.id:
            raise _bad("a deliverable cannot depend on itself", ptr)
        s.add(
            m.WorkItemLink(
                id=_id(),
                from_item_id=pred.id,
                to_item_id=item.id,
                link_type=LinkType.FINISH_TO_START,
                lag_days=lag,
            )
        )
        n_links += 1
    await s.flush()
    emit(
        uow, workspace_id=ws.id, actor_id=actor_id, entity_type=TargetType.PROJECT, entity_id=project.id, event_type="plan.created",
        payload={"after": {"phases": len(plan.phases), "items": n_items, "milestones": len(plan.milestones), "dependencies": n_links}},
        source=EventSource.OVERLAY, correlation_id=uow.context.get("correlation_id"),
    )  # fmt: skip
    return {
        "phases": len(plan.phases),
        "items": n_items,
        "milestones": len(plan.milestones),
        "dependencies": n_links,
    }


def _id() -> str:
    from tt_schema.base import new_id

    return new_id()


def _check_dates(start: date | None, end: date | None, pointer: str) -> None:
    if start and end and end < start:
        raise _bad("planned_end is before planned_start", pointer + "/planned_end")
