"""Top-down project plan (plan P1 §1.2): phases → deliverables → milestones → dependencies, with statuses derived on read.

A project is fully plannable with zero capture rows. Nothing here is stored as a status: `plan_status` is a function of dates,
state categories and whether any task or activity exists, so it can never disagree with the work.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import (
    EventSource,
    ItemTypeKey,
    LinkType,
    MilestoneKind,
    StateCategory,
    TargetType,
)

from tt_server.acl.context import AclContext
from tt_server.acl.visibility import item_visible_for
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.projects.service import item_type

AT_RISK_DAYS, DUE_SOON_DAYS, AT_RISK_PROGRESS = 7, 14, 50


class DeliverableIn(BaseModel):
    ref: str | None = None  # a local name other deliverables can depend on
    title: str = Field(min_length=1)
    planned_start: date | None = None
    planned_end: date | None = None
    owner: str | None = None  # @handle
    milestone: str | None = None  # a milestone name from this plan, or an existing one
    depends_on: list[str] = Field(default_factory=list)  # refs or titles of other deliverables in this plan
    lag_days: int | None = None


class PhaseIn(BaseModel):
    title: str = Field(min_length=1)
    planned_start: date | None = None
    planned_end: date | None = None
    deliverables: list[DeliverableIn] = Field(default_factory=list)


class MilestoneIn(BaseModel):
    name: str = Field(min_length=1)
    kind: MilestoneKind = MilestoneKind.OTHER
    due_date: date
    description: str | None = None


class PlanIn(BaseModel):
    phases: list[PhaseIn] = Field(default_factory=list)
    milestones: list[MilestoneIn] = Field(default_factory=list)


def _bad(msg: str, pointer: str) -> ApiError:
    return ApiError(400, "invalid_plan", msg, pointer=pointer)


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


# ---------------------------------------------------------------- statuses (pure, tested directly)
def deliverable_status(
    *,
    done: bool,
    planned_end: date | None,
    progress: int | None,
    has_work: bool,
    predecessor_overdue: bool,
    today: date,
) -> str:
    if done:
        return "done"
    if planned_end is not None and planned_end < today:
        return "overdue"
    soon = planned_end is not None and (planned_end - today).days <= AT_RISK_DAYS
    if (soon and (progress or 0) < AT_RISK_PROGRESS) or predecessor_overdue:
        return "at_risk"
    return "in_progress" if has_work else "not_started"


def milestone_status(*, completed: bool, due: date, today: date) -> str:
    if completed:
        return "completed"
    if due < today:
        return "overdue"
    return "due_soon" if (due - today).days <= DUE_SOON_DAYS else "planned"


def project_health(milestones: list[str], deliverables: list[str]) -> str:
    if not milestones and not deliverables:
        return "unknown"
    if "overdue" in milestones:
        return "red"
    if "due_soon" in milestones or "at_risk" in deliverables or "overdue" in deliverables:
        return "amber"
    return "green"


# ---------------------------------------------------------------- the read
async def get_plan(
    session: AsyncSession, project: m.Project, *, today: date, acl: AclContext | None = None
) -> dict[str, Any]:
    vis = item_visible_for(acl, m.WorkItem) if acl is not None else None
    base = (
        select(m.WorkItem, m.WorkItem.id, m.WorkflowState.category, m.WorkItemType.key)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .join(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
        .where(m.WorkItem.project_id == project.id, m.WorkItem.deleted_at.is_(None))
    )
    if vis is not None:
        base = base.where(vis)
    rows = (await session.execute(base.order_by(m.WorkItem.planned_start.nulls_last(), m.WorkItem.key))).all()
    phases = [(it, c) for it, _, c, k in rows if k == ItemTypeKey.PHASE]
    delivs = [(it, c) for it, _, c, k in rows if k == ItemTypeKey.DELIVERABLE]
    tasks = [(it, c) for it, _, c, k in rows if k not in (ItemTypeKey.PHASE, ItemTypeKey.DELIVERABLE)]
    deliv_ids = {it.id for it, _ in delivs}
    # work under each deliverable: tasks whose path is below it (ltree) -> group by the nearest deliverable ancestor
    by_parent: dict[str, list[tuple[m.WorkItem, StateCategory]]] = {}
    path_owner = {str(it.path): it.id for it, _ in delivs}
    for it, c in tasks:
        parts = str(it.path).split(".")
        for j in range(len(parts) - 1, 0, -1):
            owner = path_owner.get(".".join(parts[:j]))
            if owner:
                by_parent.setdefault(owner, []).append((it, c))
                break
    last_act = {
        wid: ts
        for wid, ts in (
            await session.execute(
                select(m.Activity.work_item_id, func.max(m.Activity.started_at))
                .where(m.Activity.project_id == project.id)
                .group_by(m.Activity.work_item_id)
            )
        ).all()
    }
    active_item_ids = set(last_act)
    minutes_by_item = {
        wid: int(mins)
        for wid, mins in (
            await session.execute(
                select(m.TimeEntry.work_item_id, func.sum(m.TimeEntry.minutes))
                .where(m.TimeEntry.work_item_id.in_([it.id for it, _ in tasks] or [""]))
                .group_by(m.TimeEntry.work_item_id)
            )
        ).all()
    }
    links = (
        await session.execute(
            select(m.WorkItemLink.from_item_id, m.WorkItemLink.to_item_id, m.WorkItemLink.lag_days).where(
                m.WorkItemLink.link_type == LinkType.FINISH_TO_START,
                m.WorkItemLink.to_item_id.in_(deliv_ids or {""}),
            )
        )
    ).all()
    preds: dict[str, list[tuple[str, int | None]]] = {}
    for a, b, lag in links:
        preds.setdefault(b, []).append((a, lag))
    latest = await session.scalar(
        select(m.Baseline.baseline_name)
        .where(m.Baseline.project_id == project.id)
        .order_by(m.Baseline.captured_at.desc())
        .limit(1)
    )
    base_end: dict[str, date | None] = {}
    if latest:
        base_end = {
            wid: pe
            for wid, pe in (
                await session.execute(
                    select(m.Baseline.work_item_id, m.Baseline.planned_end).where(
                        m.Baseline.project_id == project.id, m.Baseline.baseline_name == latest
                    )
                )
            ).all()
        }
    status: dict[str, str] = {}
    # overdue-ness of predecessors first (needs a pass over all deliverables)
    overdue = {
        it.id for it, c in delivs if c != StateCategory.DONE and it.planned_end and it.planned_end < today
    }

    def variance(it: m.WorkItem) -> int | None:
        b = base_end.get(it.id)
        return (it.planned_end - b).days if (b and it.planned_end) else None

    out_delivs: dict[str, dict[str, Any]] = {}
    for it, c in delivs:
        kids = by_parent.get(it.id, [])
        has_work = bool(kids) or it.id in active_item_ids
        total = len(kids)
        done_k = sum(1 for _, kc in kids if kc == StateCategory.DONE)
        progress = round(done_k * 100 / total) if total else None
        st = deliverable_status(
            done=c == StateCategory.DONE, planned_end=it.planned_end, progress=progress, has_work=has_work,
            predecessor_overdue=any(p in overdue for p, _ in preds.get(it.id, [])), today=today,
        )  # fmt: skip
        status[it.id] = st
        out_delivs[it.id] = {
            "id": it.id, "key": it.key, "title": it.title, "status": st, "planned_start": it.planned_start, "planned_end": it.planned_end,
            "owner_id": it.assignee_id, "milestone_id": it.milestone_id, "task_count": total, "tasks_done": done_k,
            "linked_tasks": total, "hours_logged": round(sum(minutes_by_item.get(k.id, 0) for k, _ in kids) / 60, 2),
            "last_activity_at": max([last_act[k.id] for k, _ in kids if k.id in last_act] + ([last_act[it.id]] if it.id in last_act else []), default=None),
            "progress_pct": progress, "depends_on": [p for p, _ in preds.get(it.id, [])], "baseline_variance_days": variance(it),
        }  # fmt: skip
    ms_rows = (
        await session.scalars(
            select(m.Milestone)
            .where(m.Milestone.project_id == project.id, m.Milestone.deleted_at.is_(None))
            .order_by(m.Milestone.due_date)
        )
    ).all()
    ms_out: list[dict[str, Any]] = []
    for ms in ms_rows:
        mine = [d for d in out_delivs.values() if d["milestone_id"] == ms.id]
        ms_out.append(
            {
                "id": ms.id, "name": ms.name, "kind": ms.kind.value, "due_date": ms.due_date, "completed_at": ms.completed_at, "description": ms.description,
                "status": milestone_status(completed=ms.completed_at is not None, due=ms.due_date, today=today),
                "deliverables_total": len(mine), "deliverables_done": sum(1 for d in mine if d["status"] == "done"),
            }
        )  # fmt: skip
    parent_of = {it.id: it.parent_id for it, _ in delivs}
    ph_out: list[dict[str, Any]] = []
    for ph, _c in phases:
        pd = [d for d in out_delivs.values() if parent_of.get(d["id"]) == ph.id]
        total = sum(d["task_count"] for d in pd)
        done_t = sum(d["tasks_done"] for d in pd)
        if pd and all(d["status"] == "done" for d in pd):
            ph_status = "done"
        elif all(d["status"] == "not_started" for d in pd):
            ph_status = "not_started"
        else:
            ph_status = "in_progress"
        ph_out.append(
            {
                "id": ph.id, "key": ph.key, "title": ph.title, "planned_start": ph.planned_start, "planned_end": ph.planned_end,
                "status": ph_status, "task_count": total, "tasks_done": done_t,
                "progress_pct": round(done_t * 100 / total) if total else None,
                "baseline_variance_days": variance(ph), "deliverables": pd,
            }
        )  # fmt: skip
    starts = [p["planned_start"] for p in ph_out if p["planned_start"]]
    ends = [p["planned_end"] for p in ph_out if p["planned_end"]]
    health = project_health([x["status"] for x in ms_out], list(status.values()))
    owner_h = await session.scalar(
        select(m.WorkspaceMember.handle).where(
            m.WorkspaceMember.person_id == project.owner_id,
            m.WorkspaceMember.workspace_id == project.workspace_id,
        )
    )
    return {
        "project": {
            "key": project.key, "name": project.name, "owner": owner_h, "planned_start": min(starts) if starts else None,
            "planned_end": max(ends) if ends else None, "health": health, "baseline_name": latest,
            "status": "not_started" if not any(d["status"] != "not_started" for d in out_delivs.values()) else "in_progress",
        },  # fmt: skip
        "phases": ph_out, "milestones": ms_out, "dependencies": [{"from": a, "to": b, "lag_days": lag, "type": "finish_to_start"} for a, b, lag in links],
        "as_of": today, "counts": {"phases": len(ph_out), "deliverables": len(out_delivs), "milestones": len(ms_out)},
    }  # fmt: skip


def _parent(delivs: list[tuple[m.WorkItem, StateCategory]], iid: str) -> str | None:
    for it, _ in delivs:
        if it.id == iid:
            return it.parent_id
    return None


def today_in(tz: str) -> date:
    from datetime import datetime

    from tt_server.domain.common import zone

    return datetime.now(zone(tz)).date()


async def snapshot_parts(
    session: AsyncSession, projects: list[m.Project], *, today: date, acl: AclContext | None
) -> dict[str, Any]:
    """The plan-shaped parts of a snapshot for `projects` (visible ones only): milestones with status, phases, deliverables, and the
    per-project plan summary. Projects with no plan items still contribute their milestones."""
    handle = {
        pid: h
        for pid, h in (
            await session.execute(select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle))
        ).all()
    }
    ms_out: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    delivs: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    with_plan = set(
        (
            await session.scalars(
                select(m.WorkItem.project_id)
                .join(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
                .where(
                    m.WorkItem.project_id.in_([p.id for p in projects] or [""]),
                    m.WorkItem.deleted_at.is_(None),
                    m.WorkItemType.key.in_([ItemTypeKey.PHASE, ItemTypeKey.DELIVERABLE]),
                )
                .distinct()
            )
        ).all()
    )
    for p in projects:
        if p.id in with_plan:
            plan = await get_plan(session, p, today=today, acl=acl)
            by_key = {d["id"]: d for ph in plan["phases"] for d in ph["deliverables"]}
            ms_name = {x["id"]: x["name"] for x in plan["milestones"]}
            for x in plan["milestones"]:
                ms_out.append(
                    {
                        "name": x["name"], "project": p.key, "due": x["due_date"].isoformat(), "kind": x["kind"], "status": x["status"],
                        "completed_at": x["completed_at"].isoformat() if x["completed_at"] else None,
                        "deliverables_done": x["deliverables_done"], "deliverables_total": x["deliverables_total"],
                    }
                )  # fmt: skip
            for ph in plan["phases"]:
                phases.append(
                    {
                        "key": ph["key"], "title": ph["title"], "project": p.key, "planned_start": _iso(ph["planned_start"]),
                        "planned_end": _iso(ph["planned_end"]), "status": ph["status"], "progress_pct": ph["progress_pct"],
                        "deliverable_count": len(ph["deliverables"]),
                    }
                )  # fmt: skip
                for d in ph["deliverables"]:
                    delivs.append(
                        {
                            "key": d["key"], "title": d["title"], "project": p.key, "phase": ph["key"], "owner": handle.get(d["owner_id"] or ""),
                            "planned_start": _iso(d["planned_start"]), "planned_end": _iso(d["planned_end"]),
                            "baseline_end": _iso(d["planned_end"] - timedelta(days=d["baseline_variance_days"]))
                            if d["planned_end"] and d["baseline_variance_days"] is not None else None,
                            "baseline_variance_days": d["baseline_variance_days"], "actual_end": None, "status": d["status"],
                            "progress_pct": d["progress_pct"], "linked_tasks": d["linked_tasks"], "hours_logged": d["hours_logged"],
                            "depends_on": [by_key[x]["key"] for x in d["depends_on"] if x in by_key],
                            "milestone": ms_name.get(d["milestone_id"] or ""),
                        }
                    )  # fmt: skip
            pr = plan["project"]
            summary[p.key] = {
                "owner": pr["owner"], "planned_start": _iso(pr["planned_start"]), "planned_end": _iso(pr["planned_end"]),
                "status": pr["status"], "health": pr["health"], "baseline_name": pr["baseline_name"],
            }  # fmt: skip
        else:
            for ms in (
                await session.scalars(
                    select(m.Milestone)
                    .where(m.Milestone.project_id == p.id, m.Milestone.deleted_at.is_(None))
                    .order_by(m.Milestone.due_date)
                )
            ).all():
                ms_out.append(
                    {
                        "name": ms.name, "project": p.key, "due": ms.due_date.isoformat(), "kind": ms.kind.value,
                        "status": milestone_status(completed=ms.completed_at is not None, due=ms.due_date, today=today),
                        "completed_at": ms.completed_at.isoformat() if ms.completed_at else None, "deliverables_done": 0, "deliverables_total": 0,
                    }
                )  # fmt: skip
    ms_out.sort(key=lambda x: (x["due"], x["project"], x["name"]))
    return {"milestones": ms_out, "phases": phases, "deliverables": delivs, "projects": summary}


def _iso(d: Any) -> str | None:
    return d.isoformat() if d else None
