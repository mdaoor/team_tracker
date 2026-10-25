"""Query API, layouts and saved views (Phase 6)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_filter import FILTER_SCHEMA, FilterError
from tt_filter import parse as parse_filter
from tt_schema.base import new_id, utcnow
from tt_schema.enums import ActivityStatus, ScopeType, TargetType, ViewLayout

from tt_server.acl.export import csv_export
from tt_server.acl.visibility import redact_activity, visible
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _project, row_dict
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.overlay import process as proc
from tt_server.query import service
from tt_server.query.compile import QueryCtx

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["query"])


def _qc(actor: CurrentActor, uow: Uow, scheme: str | None = None, tz: str | None = None) -> QueryCtx:
    return QueryCtx(
        session=uow.session,
        ws_id=actor.ws.id,
        acl=actor.acl,
        person_id=actor.person.id,
        handle=actor.member.handle,
        tz=tz or actor.person.time_zone or "UTC",
        scheme_id=scheme,
    )


async def _scheme(uow: Uow, ws_id: str, ref: str | None) -> str | None:
    if not ref:
        return None
    sid = await uow.session.scalar(
        select(m.WeightScheme.id).where(
            m.WeightScheme.workspace_id == ws_id, (m.WeightScheme.id == ref) | (m.WeightScheme.name == ref)
        )
    )
    if sid is None:
        raise ApiError(400, "unknown_scheme", f"unknown weight scheme {ref!r}", pointer="/weight_scheme_id")
    return str(sid)


def _bad(exc: FilterError) -> ApiError:
    return ApiError(400, "invalid_filter", exc.message, pointer=exc.pointer or "/filter")


@router.get("/meta/schema/filter")
async def filter_schema() -> dict[str, Any]:
    return FILTER_SCHEMA


class ItemsQuery(BaseModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list)
    sort: list[dict[str, str]] = Field(default_factory=list)
    layout: ViewLayout = ViewLayout.LIST
    weight_scheme_id: str | None = None
    page: dict[str, Any] = Field(default_factory=dict)
    time_zone: str | None = None


@router.post("/query/items")
async def query_items(body: ItemsQuery, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    qc = _qc(actor, uow, await _scheme(uow, actor.ws.id, body.weight_scheme_id), body.time_zone)
    try:
        return await service.query_items(
            qc, filter=body.filter, sort=body.sort or None, group_by=body.group_by, page=body.page
        )
    except FilterError as exc:
        raise _bad(exc) from exc


class AggregateQuery(BaseModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    rows: str
    cols: str | None = None
    measure: str = "count"
    weight_scheme_id: str | None = None
    time_zone: str | None = None


@router.post("/query/aggregate")
async def query_aggregate(body: AggregateQuery, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    qc = _qc(actor, uow, await _scheme(uow, actor.ws.id, body.weight_scheme_id), body.time_zone)
    try:
        return await service.aggregate(
            qc, filter=body.filter, rows=body.rows, cols=body.cols, measure=body.measure
        )
    except FilterError as exc:
        raise _bad(exc) from exc


@router.post("/query/explain", dependencies=[needs("role.admin")])
async def explain(body: ItemsQuery, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Admin debugging: the SQL the filter compiles to."""
    qc = _qc(actor, uow, await _scheme(uow, actor.ws.id, body.weight_scheme_id), body.time_zone)
    try:
        stmt = await service.filtered_items(qc, body.filter, m.WorkItem.id)
    except FilterError as exc:
        raise _bad(exc) from exc
    return {"sql": str(stmt.compile(compile_kwargs={"literal_binds": False}))}


@router.post("/query/items:csv")
async def items_csv(body: ItemsQuery, actor: CurrentActor, uow: Uow) -> Response:
    qc = _qc(actor, uow, await _scheme(uow, actor.ws.id, body.weight_scheme_id), body.time_zone)
    try:
        res = await service.query_all(qc, filter=body.filter, sort=body.sort or None)
    except FilterError as exc:
        raise _bad(exc) from exc
    cols = [
        "key",
        "title",
        "project",
        "type",
        "assignee",
        "stage",
        "state",
        "priority",
        "weight",
        "due_date",
        "hours_logged",
    ]
    text = csv_export(
        actor.member.handle,
        "view" if body.filter else "all visible",
        cols,
        [[i[c] for c in cols] for i in res["items"]],
    )
    return Response(
        text, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="items.csv"'}
    )


@router.post("/query/aggregate:csv")
async def aggregate_csv(body: AggregateQuery, actor: CurrentActor, uow: Uow) -> Response:
    qc = _qc(actor, uow, await _scheme(uow, actor.ws.id, body.weight_scheme_id), body.time_zone)
    try:
        agg = await service.aggregate(
            qc, filter=body.filter, rows=body.rows, cols=body.cols, measure=body.measure
        )
    except FilterError as exc:
        raise _bad(exc) from exc
    header = [body.rows, *(agg["cols"] or [body.measure])]
    text = csv_export(
        actor.member.handle,
        f"{body.rows} x {body.cols or body.measure}",
        header,
        [[r["value"], *cells] for r, cells in zip(agg["rows"], agg["cells"], strict=True)],
    )
    return Response(
        text, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="matrix.csv"'}
    )


# ---------------------------------------------------------------- layouts
def _filter_param(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_filter", "filter must be JSON", pointer="/filter") from exc
    if not isinstance(v, dict):
        raise ApiError(400, "invalid_filter", "filter must be an object", pointer="/filter")
    return v


@router.get("/projects/{key}/board")
async def project_board(key: str, actor: CurrentActor, uow: Uow, filter: str | None = None) -> dict[str, Any]:  # noqa: A002
    p = await _project(uow, actor.ws.id, key)
    states = (
        await uow.session.scalars(
            select(m.WorkflowState)
            .where(m.WorkflowState.workflow_id == p.workflow_id)
            .order_by(m.WorkflowState.position)
        )
    ).all()
    f = {"and": [_filter_param(filter), {"project": {"in": [p.key]}}]}
    try:
        res = await service.query_all(_qc(actor, uow), filter=f, sort=[{"field": "priority", "dir": "desc"}])
    except FilterError as exc:
        raise _bad(exc) from exc
    cols = []
    for s in states:
        cards = [i for i in res["items"] if i["state"] == s.key]
        cols.append(
            {
                "state": s.key,
                "name": s.name,
                "category": s.category.value,
                "wip_limit": s.wip_limit,
                "count": len(cards),
                "over_wip": s.wip_limit is not None and len(cards) > s.wip_limit,
                "items": cards,
            }
        )
    return {"project": p.key, "columns": cols, "total": res["page"]["total"]}


@router.get("/processes/{ref}/pipeline")
async def pipeline(ref: str, actor: CurrentActor, uow: Uow, filter: str | None = None) -> dict[str, Any]:  # noqa: A002
    p = await proc.get_process(uow.session, actor.ws.id, ref)
    stages = await proc.stages_of(uow.session, p.id)
    f = {"and": [_filter_param(filter), {"stage": {"process": p.key, "in": [s.key for s in stages]}}]}
    try:
        res = await service.query_all(_qc(actor, uow), filter=f)
    except FilterError as exc:
        raise _bad(exc) from exc
    approvals = {
        pl.target_id: a.status.value
        for pl, a in (
            await uow.session.execute(
                select(m.StagePlacement, m.Approval)
                .join(m.Approval, m.Approval.id == m.StagePlacement.approval_id)
                .where(m.StagePlacement.exited_at.is_(None), m.StagePlacement.process_id == p.id)
            )
        ).all()
    }
    cols = []
    for s in stages:
        cards = [dict(i, approval=approvals.get(i["id"])) for i in res["items"] if i["stage"] == s.key]
        over = [
            c
            for c in cards
            if s.expected_duration_days and (c["stage_age_days"] or 0) >= s.expected_duration_days
        ]
        cols.append(
            {
                "stage": s.key, "name": s.name, "category": s.category.value, "expected_duration_days": s.expected_duration_days,
                "wip_limit": s.wip_limit, "wip_count": len(cards), "over_wip": s.wip_limit is not None and len(cards) > s.wip_limit,
                "over_expected": len(over), "weight_sum": round(sum(c["weight"] for c in cards), 4), "requires_approval": s.requires_approval,
                "items": cards,
            }
        )  # fmt: skip
    return {"process": p.key, "columns": cols}


class GanttQuery(BaseModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    rows: str = Field(default="item", pattern="^(item|epic|milestone)$")
    baseline: str | None = None
    weight_scheme_id: str | None = None


@router.post("/query/gantt")
async def gantt(body: GanttQuery, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    qc = _qc(actor, uow, await _scheme(uow, actor.ws.id, body.weight_scheme_id))
    try:
        res = await service.query_all(qc, filter=body.filter, sort=[{"field": "key", "dir": "asc"}])
    except FilterError as exc:
        raise _bad(exc) from exc
    items = res["items"]
    ids = [i["id"] for i in items]
    row_by_id = {i["id"]: i for i in items}
    kids: dict[str, list[str]] = {}
    for i in items:
        if i["parent_id"] in row_by_id:
            kids.setdefault(i["parent_id"], []).append(i["id"])

    def span(iid: str) -> tuple[date | None, date | None]:
        it = row_by_id[iid]
        starts = [d for d in [it["planned_start"], *(span(k)[0] for k in kids.get(iid, []))] if d]
        ends = [d for d in [it["planned_end"], *(span(k)[1] for k in kids.get(iid, []))] if d]
        return (min(starts) if starts else None, max(ends) if ends else None)

    base: dict[str, Any] = {}
    if body.baseline and ids:
        for b in (
            await uow.session.scalars(
                select(m.Baseline).where(
                    m.Baseline.work_item_id.in_(ids), m.Baseline.baseline_name == body.baseline
                )
            )
        ).all():
            base[b.work_item_id] = {"planned_start": b.planned_start, "planned_end": b.planned_end}
    starts_actual = (
        dict(
            (
                await uow.session.execute(
                    select(m.WorkItem.id, m.WorkItem.actual_start).where(m.WorkItem.id.in_(ids))
                )
            ).all()
        )
        if ids
        else {}
    )
    rows = []
    for i in items:
        s, e = span(i["id"])
        if body.rows == "epic" and i["type"] != "epic":
            continue
        bl = base.get(i["id"])
        var = (e - i["planned_end"]).days if (e and i["planned_end"] and i["actual_end"]) else None
        rows.append(
            {
                "key": i["key"], "title": i["title"], "type": i["type"], "project": i["project"], "parent": row_by_id.get(i["parent_id"], {}).get("key"),
                "planned_start": s, "planned_end": e, "own_start": i["planned_start"], "own_end": i["planned_end"],
                "actual_start": starts_actual.get(i["id"]), "actual_end": i["actual_end"], "state_category": i["state_category"],
                "baseline": bl, "variance_days": var, "rollup": bool(kids.get(i["id"])),
            }
        )  # fmt: skip
    deps = []
    if ids:
        for lk in (
            await uow.session.scalars(
                select(m.WorkItemLink).where(
                    m.WorkItemLink.from_item_id.in_(ids), m.WorkItemLink.to_item_id.in_(ids)
                )
            )
        ).all():
            deps.append(
                {
                    "from": row_by_id[lk.from_item_id]["key"],
                    "to": row_by_id[lk.to_item_id]["key"],
                    "type": lk.link_type.value,
                    "lag_days": lk.lag_days or 0,
                }
            )
    projects = sorted({i["project"] for i in items})
    ms = (
        await uow.session.execute(
            select(m.Milestone, m.Project.key)
            .join(m.Project, m.Project.id == m.Milestone.project_id)
            .where(
                m.Project.workspace_id == actor.ws.id,
                m.Project.key.in_(projects),
                m.Milestone.deleted_at.is_(None),
            )
        )
    ).all()
    if body.rows == "milestone":
        # one bar per milestone: from its earliest item start to the later of its due date and its last item end
        owner = (
            dict(
                (
                    await uow.session.execute(
                        select(m.WorkItem.id, m.WorkItem.milestone_id).where(m.WorkItem.id.in_(ids))
                    )
                ).all()
            )
            if ids
            else {}
        )
        by_ms: dict[str, list[dict[str, Any]]] = {}
        for i in items:
            if (mid := owner.get(i["id"])) is not None:
                by_ms.setdefault(mid, []).append(i)
        rows = []
        for x, k in ms:
            members = by_ms.get(x.id, [])
            starts = [d for d in (i["planned_start"] for i in members) if d]
            ends = [d for d in ([i["planned_end"] for i in members] + [x.due_date]) if d]
            if not members and not x.due_date:
                continue
            done = bool(members) and all(i["state_category"] == "done" for i in members)
            rows.append({
                "key": x.name, "title": f"{k} · {x.name}", "type": "milestone", "project": k, "parent": None,
                "planned_start": min(starts) if starts else x.due_date, "planned_end": max(ends) if ends else x.due_date,
                "own_start": None, "own_end": x.due_date, "actual_start": None, "actual_end": None,
                "state_category": "done" if done else "active", "baseline": None, "variance_days": None, "rollup": True,
                "items": len(members),
            })  # fmt: skip
        deps = []
    return {
        "rows": rows, "dependencies": deps, "milestones": [{"name": x.name, "project": k, "due": x.due_date} for x, k in ms],
        "today": qc.today, "baseline": body.baseline,
    }  # fmt: skip


class CalendarQuery(BaseModel):
    filter: dict[str, Any] = Field(default_factory=dict)
    from_: date = Field(alias="from")
    to: date
    fields: list[str] = Field(default_factory=lambda: ["due_date", "planned_end", "milestones", "iterations"])

    model_config = {"populate_by_name": True}


@router.post("/query/calendar")
async def calendar(body: CalendarQuery, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    if body.to < body.from_ or (body.to - body.from_).days > 366:
        raise ApiError(400, "invalid_range", "from..to must be at most 366 days", pointer="/to")
    qc = _qc(actor, uow)
    days: dict[str, list[dict[str, Any]]] = {}
    try:
        res = await service.query_all(qc, filter=body.filter)
    except FilterError as exc:
        raise _bad(exc) from exc
    for i in res["items"]:
        for f in ("due_date", "planned_end"):
            d = i[f]
            if f in body.fields and d and body.from_ <= d <= body.to:
                days.setdefault(d.isoformat(), []).append(
                    {"kind": f, "key": i["key"], "title": i["title"], "priority": i["priority"]}
                )
    projects = (
        await uow.session.scalars(
            visible(select(m.Project).where(m.Project.workspace_id == actor.ws.id), actor.acl, m.Project)
        )
    ).all()
    pk = {p.id: p.key for p in projects}
    if "milestones" in body.fields:
        for ms in (
            await uow.session.scalars(
                select(m.Milestone).where(
                    m.Milestone.project_id.in_(pk),
                    m.Milestone.due_date.between(body.from_, body.to),
                    m.Milestone.deleted_at.is_(None),
                )
            )
        ).all():
            days.setdefault(ms.due_date.isoformat(), []).append(
                {"kind": "milestone", "title": ms.name, "project": pk[ms.project_id]}
            )
    if "iterations" in body.fields:
        for it in (
            await uow.session.scalars(
                select(m.Iteration).where(
                    m.Iteration.project_id.in_(pk),
                    m.Iteration.end_date >= body.from_,
                    m.Iteration.start_date <= body.to,
                    m.Iteration.deleted_at.is_(None),
                )
            )
        ).all():
            days.setdefault(max(it.start_date, body.from_).isoformat(), []).append(
                {"kind": "iteration", "title": it.name, "project": pk[it.project_id], "end": it.end_date}
            )
    return {"from": body.from_, "to": body.to, "days": dict(sorted(days.items()))}


@router.get("/now")
async def now_board(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    stmt = visible(
        select(m.Activity, m.WorkItem.key, m.WorkItem.title, m.Project.key, m.WorkspaceMember.handle)
        .join(m.WorkItem, m.WorkItem.id == m.Activity.work_item_id)
        .join(m.Project, m.Project.id == m.Activity.project_id)
        .join(
            m.WorkspaceMember,
            (m.WorkspaceMember.person_id == m.Activity.person_id)
            & (m.WorkspaceMember.workspace_id == actor.ws.id),
        )
        .where(m.Project.workspace_id == actor.ws.id, m.Activity.status == ActivityStatus.ACTIVE),
        actor.acl,
        m.Activity,
    )
    rows = (await uow.session.execute(stmt.order_by(m.Activity.started_at))).all()
    return {
        "data": [
            {**redact_activity(actor.acl, {k: v for k, v in row_dict(a).items() if k in {"id", "kind", "person_id", "started_at", "note", "mood", "client_meta"}}), "item_key": ik, "item_title": it, "project": pk, "person": h, "since_minutes": int((utcnow() - a.started_at).total_seconds() // 60)}
            for a, ik, it, pk, h in rows
        ],
        "next_cursor": None,
    }  # fmt: skip


# ---------------------------------------------------------------- saved views
class ViewIn(BaseModel):
    name: str = Field(min_length=1)
    filter: dict[str, Any] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list)
    sort_by: list[dict[str, str]] = Field(default_factory=list)
    layout: ViewLayout = ViewLayout.LIST
    weight_scheme_id: str | None = None
    is_shared: bool = False
    scope_type: ScopeType = ScopeType.PERSON
    scope_id: str | None = None


class ViewPatch(BaseModel):
    name: str | None = None
    filter: dict[str, Any] | None = None
    group_by: list[str] | None = None
    sort_by: list[dict[str, str]] | None = None
    layout: ViewLayout | None = None
    weight_scheme_id: str | None = None
    is_shared: bool | None = None


def _validate(filter_: dict[str, Any]) -> None:
    try:
        parse_filter(filter_)
    except FilterError as exc:
        raise ApiError(400, "invalid_filter", exc.message, pointer="/filter" + exc.pointer) from exc


@router.post("/views", status_code=201, dependencies=[needs("view.save")])
async def create_view(body: ViewIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _validate(body.filter)
    if body.is_shared and not actor.acl.can("view.share"):
        raise ApiError(
            403, "forbidden", "missing permission 'view.share'", details={"permission": "view.share"}
        )
    v = m.SavedView(
        id=new_id(),
        workspace_id=actor.ws.id,
        owner_id=actor.person.id,
        entity=TargetType.WORK_ITEM,
        **body.model_dump(),
    )
    uow.session.add(v)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="view.created",
        payload={"after": {"name": v.name, "shared": v.is_shared}},
    )
    await uow.commit()
    return row_dict(v)


@router.get("/views")
async def list_views(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            visible(
                select(m.SavedView).where(
                    m.SavedView.workspace_id == actor.ws.id, m.SavedView.deleted_at.is_(None)
                ),
                actor.acl,
                m.SavedView,
            ).order_by(m.SavedView.name)
        )
    ).all()
    return {
        "data": [dict(row_dict(v), mine=v.owner_id == actor.person.id) for v in rows],
        "next_cursor": None,
    }


async def _view(actor: CurrentActor, uow: Uow, vid: str) -> m.SavedView:
    v: m.SavedView | None = await uow.session.scalar(
        visible(
            select(m.SavedView).where(
                m.SavedView.id == vid,
                m.SavedView.workspace_id == actor.ws.id,
                m.SavedView.deleted_at.is_(None),
            ),
            actor.acl,
            m.SavedView,
        )
    )
    if v is None:
        raise ApiError(404, "not_found", "view not found")
    return v


@router.get("/views/{vid}")
async def get_view(vid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return row_dict(await _view(actor, uow, vid))


@router.patch("/views/{vid}")
async def patch_view(vid: str, body: ViewPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    v = await _view(actor, uow, vid)
    if v.owner_id != actor.person.id:
        raise ApiError(403, "forbidden", "only the owner may change a view")
    data = body.model_dump(exclude_unset=True)
    if "filter" in data:
        _validate(data["filter"])
    if data.get("is_shared") and not actor.acl.can("view.share"):
        raise ApiError(
            403, "forbidden", "missing permission 'view.share'", details={"permission": "view.share"}
        )
    for k, val in data.items():
        setattr(v, k, val)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="view.updated",
        payload={"after": {"id": v.id, "fields": sorted(data)}},
    )
    await uow.commit()
    return row_dict(v)


@router.delete("/views/{vid}", status_code=204)
async def delete_view(vid: str, actor: CurrentActor, uow: Uow) -> Response:
    v = await _view(actor, uow, vid)
    if v.owner_id != actor.person.id and not actor.acl.is_admin:
        raise ApiError(403, "forbidden", "only the owner may delete a view")
    v.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="view.deleted",
        payload={"before": {"id": v.id}},
    )
    await uow.commit()
    return Response(status_code=204)


@router.post("/views/{vid}:restore")
async def restore_view(vid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Undo a view delete (UX 6)."""
    v: m.SavedView | None = await uow.session.scalar(
        select(m.SavedView).where(
            m.SavedView.id == vid,
            m.SavedView.workspace_id == actor.ws.id,
            m.SavedView.deleted_at.is_not(None),
        )
    )
    if v is None or not (v.owner_id == actor.person.id or actor.acl.is_admin):
        raise ApiError(404, "not_found", "view not found")
    v.deleted_at = None
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PERSON,
        entity_id=actor.person.id, event_type="view.restored", payload={"after": {"id": v.id}},
    )  # fmt: skip
    await uow.commit()
    return row_dict(v)


@router.post("/views/{vid}/run")
async def run_view(vid: str, actor: CurrentActor, uow: Uow, cursor: str | None = None) -> dict[str, Any]:
    """Execute a saved view *as the caller*: a shared view never shows more than its opener may see."""
    v = await _view(actor, uow, vid)
    qc = _qc(actor, uow, v.weight_scheme_id)
    try:
        return await service.query_items(
            qc,
            filter=v.filter,
            sort=[{"field": s.get("field", "priority"), "dir": s.get("dir", "desc")} for s in v.sort_by]
            or None,
            group_by=list(v.group_by),
            page={"cursor": cursor},
        )
    except FilterError as exc:
        raise _bad(exc) from exc
