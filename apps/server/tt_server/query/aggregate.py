"""Aggregates (group-by dimension x measure) over the filtered, ACL-visible items (Phase 6 section 6.3)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from tt_filter import FilterError
from tt_schema.enums import StateCategory, TargetType

from tt_server.db import models as m
from tt_server.query.compile import (
    QueryCtx,
    weight_expr,
)

from tt_server.query.expressions import _hours, _open_violations, current_stage, filtered_items


MEASURES = (
    "count",
    "hours",
    "weighted_throughput",
    "sum_weight",
    "open_violations",
    "avg_stage_age",
    "remaining_estimate",
)


# ---------------------------------------------------------------- aggregate
def dim_values(qc: QueryCtx, dim: str) -> Any:
    """Subquery (item_id, val) for any dimension: built-ins read their columns, the rest read classifications."""
    k = dim.lower()
    wi = m.WorkItem
    if k == "stage":
        cur = current_stage()
        return select(cur.c.item_id.label("item_id"), cur.c.stage.label("val")).subquery()
    if k == "state":
        return (
            select(wi.id.label("item_id"), m.WorkflowState.category.label("val"))
            .join(m.WorkflowState, m.WorkflowState.id == wi.state_id)
            .subquery()
        )
    if k == "project":
        return (
            select(wi.id.label("item_id"), m.Project.key.label("val"))
            .join(m.Project, m.Project.id == wi.project_id)
            .subquery()
        )
    if k == "type":
        return (
            select(wi.id.label("item_id"), m.WorkItemType.key.label("val"))
            .join(m.WorkItemType, m.WorkItemType.id == wi.type_id)
            .subquery()
        )
    if k == "priority":
        return select(wi.id.label("item_id"), wi.priority.label("val")).subquery()
    if k == "assignee":
        return (
            select(wi.id.label("item_id"), m.WorkspaceMember.handle.label("val"))
            .join(
                m.WorkspaceMember,
                (m.WorkspaceMember.person_id == wi.assignee_id)
                & (m.WorkspaceMember.workspace_id == qc.ws_id),
            )
            .subquery()
        )
    if k == "team":
        return (
            select(wi.id.label("item_id"), m.Team.slug.label("val"))
            .join(m.Team, m.Team.id == wi.team_id)
            .subquery()
        )
    if k == "tags":
        return (
            select(m.TagLink.target_id.label("item_id"), m.Tag.name.label("val"))
            .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
            .where(m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.deleted_at.is_(None))
            .subquery()
        )
    return (
        select(m.Classification.target_id.label("item_id"), m.DimensionValue.key.label("val"))
        .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
        .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
        .where(
            m.Classification.target_type == TargetType.WORK_ITEM,
            m.Classification.deleted_at.is_(None),
            m.Classification.is_confirmed.is_(True),
            func.lower(m.Dimension.key) == k,
            m.Dimension.workspace_id == qc.ws_id,
        )
        .subquery()
    )


async def _check_dim(qc: QueryCtx, dim: str, ptr: str) -> None:
    if dim.lower() not in {"stage", "state", "project", "type", "priority", "assignee", "team", "tags"}:
        await qc.dimension(dim, ptr)


def measure_expr(measure: str, qc: QueryCtx) -> Any:
    w = weight_expr(qc.scheme_id)
    if measure == "count":
        return func.count(sa.distinct(m.WorkItem.id))
    if measure == "hours":
        return func.coalesce(func.sum(_hours()), 0)
    if measure == "sum_weight":
        return func.coalesce(func.sum(w), 0)
    if measure == "weighted_throughput":
        return func.coalesce(
            func.sum(sa.case((m.WorkflowState.category == StateCategory.DONE, w), else_=0)), 0
        )
    if measure == "open_violations":
        return func.coalesce(func.sum(_open_violations()), 0)
    if measure == "remaining_estimate":
        return func.coalesce(func.sum(m.WorkItem.remaining_value), 0)
    if measure == "avg_stage_age":
        cur = current_stage()
        age = (
            select(func.extract("epoch", qc.now - cur.c.entered_at) / 86400.0)
            .where(cur.c.item_id == m.WorkItem.id)
            .correlate(m.WorkItem)
            .scalar_subquery()
        )
        return func.avg(age)
    raise FilterError("/measure", f"unknown measure {measure!r}; allowed: {', '.join(MEASURES)}")


async def aggregate(
    qc: QueryCtx,
    *,
    filter: Any = None,
    rows: str,
    cols: str | None = None,
    measure: str = "count",  # noqa: A002
) -> dict[str, Any]:
    await _check_dim(qc, rows, "/rows")
    if cols:
        await _check_dim(qc, cols, "/cols")
    mexpr = measure_expr(measure, qc)
    ids = (await filtered_items(qc, filter, m.WorkItem.id)).subquery("f")
    r = dim_values(qc, rows)
    base = (
        select()
        .select_from(ids)
        .join(m.WorkItem, m.WorkItem.id == ids.c.id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .outerjoin(r, r.c.item_id == m.WorkItem.id)
    )
    rv = func.coalesce(sa.cast(r.c.val, sa.Text), "—")
    c = dim_values(qc, cols) if cols else None
    if c is not None:
        base = base.outerjoin(c, c.c.item_id == m.WorkItem.id)
    cv = func.coalesce(sa.cast(c.c.val, sa.Text), "—") if c is not None else None
    sel = [rv.label("r"), *([cv.label("c")] if cv is not None else []), mexpr.label("v")]
    grp = [rv, *([cv] if cv is not None else [])]
    data = (await qc.session.execute(base.add_columns(*sel).group_by(*grp))).all()
    row_keys = sorted({d.r for d in data})
    col_keys = sorted({d.c for d in data}) if cv is not None else []
    cell = {(d.r, getattr(d, "c", None)): float(d.v or 0) for d in data}
    row_tot = {
        d.r: float(d.v or 0)
        for d in (
            await qc.session.execute(base.add_columns(rv.label("r"), mexpr.label("v")).group_by(rv))
        ).all()
    }
    col_tot: dict[str, float] = {}
    if cv is not None:
        col_tot = {
            d.c: float(d.v or 0)
            for d in (
                await qc.session.execute(base.add_columns(cv.label("c"), mexpr.label("v")).group_by(cv))
            ).all()
        }
    total = float(
        await qc.session.scalar(
            select(mexpr)
            .select_from(ids)
            .join(m.WorkItem, m.WorkItem.id == ids.c.id)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        )
        or 0
    )
    return {
        "measure": measure,
        "rows": [{"value": k, "count": row_tot.get(k, 0), "total": row_tot.get(k, 0)} for k in row_keys],
        "cols": col_keys,
        "cells": [[cell.get((rk, ck), 0.0) for ck in col_keys] for rk in row_keys] if cv is not None else [[cell.get((rk, None), 0.0)] for rk in row_keys],
        "totals": {"rows": row_tot, "cols": col_tot, "all": total},
    }  # fmt: skip
