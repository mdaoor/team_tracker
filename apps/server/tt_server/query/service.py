"""Item queries, aggregates and layout data on top of the compiled filter (Phase 6 §6.2-6.3)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from tt_filter import FilterError
from tt_schema.enums import TargetType

from tt_server.db import models as m
from tt_server.query.aggregate import MEASURES as MEASURES
from tt_server.query.aggregate import _check_dim as _check_dim
from tt_server.query.aggregate import aggregate as aggregate
from tt_server.query.aggregate import dim_values as dim_values
from tt_server.query.aggregate import measure_expr as measure_expr
from tt_server.query.compile import (
    QueryCtx,
    priority_rank,
    weight_expr,
)
from tt_server.query.expressions import (
    _hours as _hours,
)
from tt_server.query.expressions import (
    _last_touched as _last_touched,
)
from tt_server.query.expressions import (
    _open_violations as _open_violations,
)
from tt_server.query.expressions import (
    _top_risk as _top_risk,
)
from tt_server.query.expressions import (
    current_stage as current_stage,
)
from tt_server.query.expressions import (
    filtered_items as filtered_items,
)

MAX_PAGE = 200
SORTS: dict[str, Any] = {
    "priority": priority_rank(), "key": m.WorkItem.key, "title": m.WorkItem.title, "due_date": m.WorkItem.due_date,
    "planned_end": m.WorkItem.planned_end, "created": m.WorkItem.created_at, "updated": m.WorkItem.updated_at,
    "impact": m.WorkItem.impact,
}  # fmt: skip


async def query_all(qc: QueryCtx, *, hard_cap: int = 100_000, **kw: Any) -> dict[str, Any]:
    """Every matching row, paging internally (layout endpoints and CSV must not silently stop at one page)."""
    kw.pop("page", None)
    items: list[Any] = []
    offset = 0
    res: dict[str, Any] = {}
    while offset < hard_cap:
        res = await query_items(qc, page={"size": MAX_PAGE, "cursor": str(offset)}, **kw)
        items += res["items"]
        if not res["page"].get("next_cursor") or not res["items"]:
            break
        offset += MAX_PAGE
    total = res["page"]["total"] if res else 0
    return {
        **res,
        "items": items,
        "page": {**dict(res.get("page") or {}), "total": total, "next_cursor": None},
    }


async def query_items(
    qc: QueryCtx,
    *,
    filter: Any = None,  # noqa: A002
    sort: list[dict[str, str]] | None = None,
    group_by: list[str] | None = None,
    page: dict[str, Any] | None = None,
    extra_where: Any = None,
    max_page: int = MAX_PAGE,
) -> dict[str, Any]:
    w = weight_expr(qc.scheme_id)
    # Phase 1: only ids, in order, with the filter and ACL -- cheap even on 50 k items.
    need_stage = any(x.get("field") == "stage_age" for x in (sort or []))
    cur1 = current_stage() if need_stage else None
    id_stmt = await filtered_items(qc, filter, m.WorkItem.id, where=extra_where)
    if cur1 is not None:
        id_stmt = id_stmt.outerjoin(cur1, cur1.c.item_id == m.WorkItem.id)
    order = []
    for s_ in sort or [{"field": "priority", "dir": "desc"}]:
        field = s_.get("field", "priority")
        desc = s_.get("dir", "desc") == "desc"
        if field == "stage_age":
            assert cur1 is not None  # noqa: S101
            order.append(
                cur1.c.entered_at.asc().nulls_last() if desc else cur1.c.entered_at.desc().nulls_last()
            )
            continue
        if field == "weight":
            expr: Any = w
        elif field in SORTS:
            expr = SORTS[field]
        else:
            raise FilterError(
                "/sort", f"cannot sort by {field!r}; allowed: {', '.join([*SORTS, 'weight', 'stage_age'])}"
            )
        order.append(expr.desc().nulls_last() if desc else expr.asc().nulls_last())
    order.append(m.WorkItem.key)
    try:
        size = max(1, min(int((page or {}).get("size", 50)), max_page))
        offset = max(0, int((page or {}).get("cursor") or 0))
    except (TypeError, ValueError) as exc:
        raise FilterError("/page", "page.size and page.cursor must be integers") from exc
    # one scan: the window count rides along with the page instead of a second pass over the filtered set
    id_rows = (
        await qc.session.execute(
            id_stmt.add_columns(func.count().over().label("total"))
            .order_by(*order)
            .limit(size + 1)
            .offset(offset)
        )
    ).all()
    total = id_rows[0].total if id_rows else 0
    if not id_rows and offset:
        total = await qc.session.scalar(select(func.count()).select_from(id_stmt.subquery())) or 0
    page_ids = [r[0] for r in id_rows]
    more = len(page_ids) > size
    page_ids = page_ids[:size]
    # Phase 2: the card fields, for at most `size` items.
    cur = current_stage(page_ids)
    assignee = (
        select(m.WorkspaceMember.handle)
        .where(
            m.WorkspaceMember.person_id == m.WorkItem.assignee_id, m.WorkspaceMember.workspace_id == qc.ws_id
        )
        .correlate(m.WorkItem)
        .scalar_subquery()
    )
    team = select(m.Team.slug).where(m.Team.id == m.WorkItem.team_id).correlate(m.WorkItem).scalar_subquery()
    wtype = (
        select(m.WorkItemType.key)
        .where(m.WorkItemType.id == m.WorkItem.type_id)
        .correlate(m.WorkItem)
        .scalar_subquery()
    )
    cols = [
        m.WorkItem, m.Project.key.label("project_key"), m.WorkflowState.key.label("state"),
        m.WorkflowState.category.label("state_category"), assignee.label("assignee"), team.label("team"),
        wtype.label("type"), cur.c.stage, cur.c.entered_at, cur.c.expected_days, cur.c.process,
        w.label("weight_v"),
        _hours().label("hours_logged"), _top_risk().label("top_risk"),
        _open_violations().label("open_violations"), _last_touched().label("last_touched"),
    ]  # fmt: skip
    detail = (
        select(*cols)
        .select_from(m.WorkItem)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .outerjoin(cur, cur.c.item_id == m.WorkItem.id)
        .where(m.WorkItem.id.in_(page_ids))
    )
    by_id = {r[0].id: r for r in (await qc.session.execute(detail)).all()} if page_ids else {}
    rows = [by_id[i] for i in page_ids if i in by_id]
    ids = [r[0].id for r in rows]
    tags: dict[str, list[str]] = defaultdict(list)
    prod: dict[str, str] = {}
    gdims: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    if ids:
        for tid, name in (
            await qc.session.execute(
                select(m.TagLink.target_id, m.Tag.name)
                .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
                .where(
                    m.TagLink.target_type == TargetType.WORK_ITEM,
                    m.TagLink.target_id.in_(ids),
                    m.TagLink.deleted_at.is_(None),
                )
            )
        ).all():
            tags[tid].append(name)
        want = {"product", *(group_by or [])}
        for tid, dkey, vkey in (
            await qc.session.execute(
                select(m.Classification.target_id, m.Dimension.key, m.DimensionValue.key)
                .join(m.Dimension, m.Dimension.id == m.Classification.dimension_id)
                .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
                .where(
                    m.Classification.target_type == TargetType.WORK_ITEM,
                    m.Classification.target_id.in_(ids),
                    m.Classification.deleted_at.is_(None),
                    m.Classification.is_confirmed.is_(True),
                    m.Dimension.key.in_(want),
                )
            )
        ).all():
            if dkey == "product":
                prod[tid] = vkey
            gdims[tid][dkey].append(vkey)
    items = []
    for r in rows:
        it = r[0]
        age = (qc.now - r.entered_at).days if r.entered_at else None
        items.append(
            {
                "key": it.key, "id": it.id, "title": it.title, "project": r.project_key, "product": prod.get(it.id),
                "type": r.type.value if r.type else None, "assignee": r.assignee, "team": r.team, "stage": r.stage,
                "process": r.process, "stage_age_days": age, "expected_duration_days": r.expected_days,
                "state": r.state, "state_category": r.state_category.value, "priority": it.priority.value,
                "weight": float(r.weight_v or 0), "impact": it.impact,
                "estimate": {"value": float(it.estimate_value), "unit": it.estimate_unit.value} if it.estimate_value is not None else None,
                "remaining": float(it.remaining_value) if it.remaining_value is not None else None,
                "hours_logged": round(float(r.hours_logged or 0), 2), "planned_start": it.planned_start,
                "planned_end": it.planned_end, "due_date": it.due_date, "actual_end": it.actual_end,
                "tags": sorted(tags.get(it.id, [])), "top_risk_score": r.top_risk, "open_violations": r.open_violations or 0,
                "last_touched": r.last_touched, "parent_id": it.parent_id,
                "dims": {g: sorted(gdims[it.id].get(g, [])) for g in (group_by or [])},
            }
        )  # fmt: skip
    out: dict[str, Any] = {
        "items": items,
        "page": {"next_cursor": str(offset + size) if more else None, "size": size, "total": total or 0},
    }
    if group_by:
        agg = await aggregate(qc, filter=filter, rows=group_by[0], measure="count")
        out["groups"] = [{"value": r["value"], "count": r["count"]} for r in agg["rows"]]
    return out


SNAP_IDS = sa.table("snap_ids", sa.column("id", sa.Text()))


async def bulk_items(qc: QueryCtx, raw_filter: Any) -> tuple[list[dict[str, Any]], Any]:
    """Every matching item in one pass (the snapshot's read): no paging, no sort, no per-item subqueries.

    The filtered, ACL-checked ids go into a transaction-scoped temp table once; the card query and the caller's grouped enrichment
    queries all join it, so the filter is evaluated a single time. Returns the rows and a `SELECT id` over that table.
    """
    s = qc.session
    ids_sel = await filtered_items(qc, raw_filter, m.WorkItem.id)
    await s.execute(sa.text("CREATE TEMP TABLE IF NOT EXISTS snap_ids (id text PRIMARY KEY) ON COMMIT DROP"))
    await s.execute(sa.text("TRUNCATE snap_ids"))
    await s.execute(sa.insert(SNAP_IDS).from_select(["id"], ids_sel))
    await s.execute(sa.text("ANALYZE snap_ids"))
    cur = current_stage(restrict=select(SNAP_IDS.c.id))
    stmt = (
        select(
            m.WorkItem.id, m.WorkItem.key, m.WorkItem.title, m.WorkItem.priority, m.WorkItem.estimate_value, m.WorkItem.estimate_unit,
            m.WorkItem.planned_start, m.WorkItem.planned_end, m.WorkItem.actual_end, m.WorkItem.parent_id, m.Project.key.label("project_key"), m.WorkflowState.key.label("state"),
            m.WorkflowState.category.label("state_category"), m.WorkspaceMember.handle.label("assignee"),
            m.Team.slug.label("team"), m.WorkItemType.key.label("type"), cur.c.stage, cur.c.entered_at, cur.c.process,
            weight_expr(qc.scheme_id).label("weight_v"),
        )
        .select_from(SNAP_IDS)
        .join(m.WorkItem, m.WorkItem.id == SNAP_IDS.c.id)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .outerjoin(cur, cur.c.item_id == m.WorkItem.id)
        .outerjoin(m.WorkspaceMember, (m.WorkspaceMember.person_id == m.WorkItem.assignee_id) & (m.WorkspaceMember.workspace_id == qc.ws_id))
        .outerjoin(m.Team, m.Team.id == m.WorkItem.team_id)
        .outerjoin(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
        .order_by(m.WorkItem.key)
    )  # fmt: skip
    ids_only = select(SNAP_IDS.c.id)
    tags: dict[str, list[str]] = defaultdict(list)
    for tid, name in (
        await s.execute(
            select(m.TagLink.target_id, m.Tag.name)
            .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
            .where(
                m.TagLink.target_type == TargetType.WORK_ITEM,
                m.TagLink.deleted_at.is_(None),
                m.TagLink.target_id.in_(ids_only),
            )
        )
    ).all():
        tags[tid].append(name)
    out = []
    for r in (await s.execute(stmt)).all():
        it = r
        out.append(
            {
                "key": it.key, "id": it.id, "title": it.title, "project": r.project_key, "type": r.type.value if r.type else None,
                "assignee": r.assignee, "team": r.team, "stage": r.stage, "process": r.process,
                "stage_age_days": (qc.now - r.entered_at).days if r.entered_at else None, "state": r.state,
                "state_category": r.state_category.value, "priority": it.priority.value, "weight": float(r.weight_v or 0),
                "estimate": {"value": float(it.estimate_value), "unit": it.estimate_unit.value} if it.estimate_value is not None else None,
                "planned_start": it.planned_start, "planned_end": it.planned_end, "actual_end": it.actual_end,
                "tags": sorted(tags.get(it.id, [])), "parent_id": it.parent_id,
            }
        )  # fmt: skip
    return out, ids_only
