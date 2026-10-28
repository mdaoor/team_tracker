"""Query API, layouts and saved views (Phase 6)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_filter import FILTER_SCHEMA, FilterError
from tt_schema.enums import ViewLayout

from tt_server.acl.export import csv_export
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.db import models as m
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
