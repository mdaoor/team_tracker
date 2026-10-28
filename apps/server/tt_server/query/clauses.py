"""One compiler per filter clause kind (`dims`, `people`, `stage`, ...) and the `_CLAUSES` registry."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import and_, exists, func, not_, or_, select, true
from tt_filter import Clause, FilterError
from tt_schema.enums import (
    AssignmentRole,
    ItemTypeKey,
    Priority,
    ReportingKind,
    RiskStatus,
    TargetType,
)

from tt_server.acl.reporting import reports_of
from tt_server.db import models as m
from tt_server.query.context import (
    OPEN_VIOLATION,
    PRIORITY_ORDER,
    SEVERITY_ORDER,
    QueryCtx,
    priority_rank,
    weight_expr,
)


# ---------------------------------------------------------------- clauses
def _cls_exists(dim: m.Dimension, value_cond: Any = None) -> Any:
    q = (
        select(1)
        .select_from(m.Classification)
        .join(m.DimensionValue, m.DimensionValue.id == m.Classification.value_id)
        .where(
            m.Classification.target_type == TargetType.WORK_ITEM,
            m.Classification.target_id == m.WorkItem.id,
            m.Classification.dimension_id == dim.id,
            m.Classification.deleted_at.is_(None),
            m.Classification.is_confirmed.is_(True),
        )
    )
    if value_cond is not None:
        q = q.where(value_cond)
    return exists(q.correlate(m.WorkItem))


def _lower_in(col: Any, vals: list[str]) -> Any:
    return func.lower(col).in_([v.lower() for v in vals])


async def _dims(c: Clause, qc: QueryCtx) -> Any:
    parts: list[Any] = []
    for key, spec in c.args.items():
        ptr = f"{c.pointer}/{key}"
        special = {"stage": "stage", "project": "project", "state": "state"}.get(key.lower())
        if special and not await _has_dim(qc, key):
            vals = spec.get("in")
            if vals is None:
                raise FilterError(ptr, f"{key} supports only 'in' here")
            sub = Clause(special, {"in": vals} if special != "state" else {"category": vals}, ptr)
            parts.append(await _CLAUSES[special](sub, qc))
            continue
        dim = await qc.dimension(key, ptr)
        if "in" in spec:
            parts.append(_cls_exists(dim, _lower_in(m.DimensionValue.key, spec["in"])))
        elif "not_in" in spec:
            parts.append(not_(_cls_exists(dim, _lower_in(m.DimensionValue.key, spec["not_in"]))))
        elif "is_null" in spec:
            parts.append(not_(_cls_exists(dim)) if spec["is_null"] else _cls_exists(dim))
        else:  # under: the value and everything below it in the value tree
            root = select(m.DimensionValue.id).where(
                m.DimensionValue.dimension_id == dim.id,
                func.lower(m.DimensionValue.key) == spec["under"].lower(),
            )
            tree = root.cte(f"tree_{uuid.uuid4().hex[:8]}", recursive=True)
            tree = tree.union_all(
                select(m.DimensionValue.id).where(m.DimensionValue.parent_value_id == tree.c.id)
            )
            parts.append(_cls_exists(dim, m.DimensionValue.id.in_(select(tree.c.id))))
    return and_(true(), *parts)


async def _has_dim(qc: QueryCtx, key: str) -> bool:
    try:
        await qc.dimension(key, "")
    except FilterError:
        return False
    return True


async def _people(c: Clause, qc: QueryCtx) -> Any:
    parts: list[Any] = []
    for k, v in c.args.items():
        ptr = f"{c.pointer}/{k}"
        if k == "assignee":
            parts.append(m.WorkItem.assignee_id.in_(await qc.person_ids(v, ptr)))
        elif k == "reporter":
            parts.append(m.WorkItem.reporter_id.in_(await qc.person_ids(v, ptr)))
        elif k == "unassigned":
            parts.append(m.WorkItem.assignee_id.is_(None) if v else m.WorkItem.assignee_id.is_not(None))
        elif k == "assignee_in_team":
            teams = await qc.team_ids(v, ptr)
            members = select(m.Membership.person_id).where(
                m.Membership.team_id.in_(teams), m.Membership.left_at.is_(None)
            )
            parts.append(m.WorkItem.assignee_id.in_(members))
        else:  # assignment_role
            who = (await qc.person_ids([v["person"]], f"{ptr}/person"))[0]
            try:
                role = AssignmentRole(v["role"])
            except ValueError as exc:
                raise FilterError(f"{ptr}/role", f"unknown assignment role {v['role']!r}") from exc
            parts.append(
                exists(
                    select(1)
                    .select_from(m.Assignment)
                    .where(
                        m.Assignment.work_item_id == m.WorkItem.id,
                        m.Assignment.person_id == who,
                        m.Assignment.role == role,
                        m.Assignment.unassigned_at.is_(None),
                    )
                    .correlate(m.WorkItem)
                )
            )
    return and_(true(), *parts)


async def _people2(c: Clause, qc: QueryCtx) -> Any:
    manager = (await qc.person_ids([c.args["reports_to"]], f"{c.pointer}/reports_to"))[0]
    try:
        kinds = [ReportingKind(k) for k in c.args["kind"]]
    except ValueError as exc:
        raise FilterError(f"{c.pointer}/kind", str(exc)) from exc
    return m.WorkItem.assignee_id.in_(
        await reports_of(qc.session, qc.ws_id, manager, kinds, None, qc.today) or [""]
    )


def _cmp(expr: Any, args: dict[str, Any]) -> Any:
    parts = []
    if "gte" in args:
        parts.append(expr >= args["gte"])
    if "lte" in args:
        parts.append(expr <= args["lte"])
    if "eq" in args:
        parts.append(expr == args["eq"])
    return and_(true(), *parts)


async def _priority(c: Clause, qc: QueryCtx) -> Any:
    rank = {p: i for i, p in enumerate(PRIORITY_ORDER)}
    a = c.args
    if "in" in a:
        return m.WorkItem.priority.in_([Priority(x) for x in a["in"]])
    return _cmp(priority_rank(), {k: rank[v] for k, v in a.items()})


async def _weight(c: Clause, qc: QueryCtx) -> Any:
    sid = await qc.scheme(c.args.get("scheme"), f"{c.pointer}/scheme")
    return _cmp(weight_expr(sid), {k: v for k, v in c.args.items() if k != "scheme"})


async def _impact(c: Clause, qc: QueryCtx) -> Any:
    return _cmp(m.WorkItem.impact, c.args)


def _date_col(kind: str) -> Any:
    return {
        "due_date": m.WorkItem.due_date,
        "planned_end": m.WorkItem.planned_end,
        "planned_start": m.WorkItem.planned_start,
        "actual_end": sa.cast(m.WorkItem.actual_end, sa.Date),
        "created": sa.cast(m.WorkItem.created_at, sa.Date),
    }[kind]


async def _dates(c: Clause, qc: QueryCtx) -> Any:
    col = _date_col(c.kind)
    ((op, arg),) = c.args.items()
    try:
        if op == "is_null":
            return col.is_(None) if arg else col.is_not(None)
        if op == "between":
            return col.between(qc.day(arg[0]), qc.day(arg[1]))
        return col < qc.day(arg) if op == "before" else col > qc.day(arg)
    except ValueError as exc:
        raise FilterError(c.pointer, str(exc)) from exc


async def _touched(c: Clause, qc: QueryCtx) -> Any:
    since = qc.start_of(qc.day(c.args["since"]))
    return exists(
        select(1)
        .select_from(m.Event)
        .where(
            m.Event.entity_type == TargetType.WORK_ITEM,
            m.Event.entity_id == m.WorkItem.id,
            m.Event.occurred_at >= since,
        )
        .correlate(m.WorkItem)
    )


async def _state(c: Clause, qc: QueryCtx) -> Any:
    parts = []
    if "category" in c.args:
        parts.append(m.WorkflowState.category.in_(c.args["category"]))
    if "in" in c.args:
        parts.append(_lower_in(m.WorkflowState.key, c.args["in"]))
    return and_(true(), *parts)


async def _stage(c: Clause, qc: QueryCtx) -> Any:
    q = (
        select(1)
        .select_from(m.StagePlacement)
        .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
        .where(
            m.StagePlacement.target_type == TargetType.WORK_ITEM,
            m.StagePlacement.target_id == m.WorkItem.id,
            m.StagePlacement.exited_at.is_(None),
        )
    )
    if "in" in c.args:
        q = q.where(_lower_in(m.Stage.key, c.args["in"]))
    if "process" in c.args:
        q = q.join(m.Process, m.Process.id == m.StagePlacement.process_id).where(
            func.lower(m.Process.key) == c.args["process"].lower()
        )
    if c.args.get("age_gt_expected"):
        q = q.where(
            m.Stage.expected_duration_days.is_not(None),
            m.StagePlacement.entered_at
            < qc.now - func.make_interval(0, 0, 0, m.Stage.expected_duration_days),
        )
    return exists(q.correlate(m.WorkItem))


async def _type(c: Clause, qc: QueryCtx) -> Any:
    try:
        keys = [ItemTypeKey(x.lower()) for x in c.args["in"]]
    except ValueError as exc:
        raise FilterError(f"{c.pointer}/in", f"unknown item type: {exc}") from exc
    return m.WorkItem.type_id.in_(select(m.WorkItemType.id).where(m.WorkItemType.key.in_(keys)))


def _tag_exists(names: list[str]) -> Any:
    return exists(
        select(1)
        .select_from(m.TagLink)
        .join(m.Tag, m.Tag.id == m.TagLink.tag_id)
        .where(
            m.TagLink.target_type == TargetType.WORK_ITEM,
            m.TagLink.target_id == m.WorkItem.id,
            m.TagLink.deleted_at.is_(None),
            _lower_in(m.Tag.name, names),
        )
        .correlate(m.WorkItem)
    )


async def _tags(c: Clause, qc: QueryCtx) -> Any:
    parts = []
    for n in c.args.get("all", []):
        parts.append(_tag_exists([n]))
    if "any" in c.args:
        parts.append(_tag_exists(c.args["any"]))
    if "none" in c.args:
        parts.append(not_(_tag_exists(c.args["none"])))
    return and_(true(), *parts)


def risk_query(*, open_only: bool = True) -> Any:
    q = (
        select(1)
        .select_from(m.RiskLink)
        .join(m.Risk, m.Risk.id == m.RiskLink.risk_id)
        .where(m.RiskLink.work_item_id == m.WorkItem.id, m.RiskLink.deleted_at.is_(None))
    )
    return q.where(m.Risk.status.notin_([RiskStatus.CLOSED])) if open_only else q


async def _risk(c: Clause, qc: QueryCtx) -> Any:
    q = risk_query(open_only="open_score_gte" in c.args)
    if "open_score_gte" in c.args:
        q = q.where(m.Risk.probability * m.Risk.impact >= c.args["open_score_gte"])
    if "linked" in c.args:
        q = q.where(func.lower(m.Risk.key) == c.args["linked"].lower())
    return exists(q.correlate(m.WorkItem))


async def _violations(c: Clause, qc: QueryCtx) -> Any:
    q = (
        select(1)
        .select_from(m.Violation)
        .where(m.Violation.target_type == TargetType.WORK_ITEM, m.Violation.target_id == m.WorkItem.id)
    )
    if "category" in c.args:
        q = q.where(m.Violation.category.in_(c.args["category"]))
    if "severity_gte" in c.args:
        q = q.where(m.Violation.severity.in_(SEVERITY_ORDER[SEVERITY_ORDER.index(c.args["severity_gte"]) :]))
    want_open = c.args.get("open", True) if "open" in c.args else None
    if want_open is not None:
        q = q.where(m.Violation.status.in_(OPEN_VIOLATION))
    has = exists(q.correlate(m.WorkItem))
    return not_(has) if want_open is False else has


async def _text(c: Clause, qc: QueryCtx) -> Any:
    like = "%" + c.args["text"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return or_(m.WorkItem.title.ilike(like, escape="\\"), m.WorkItem.key.ilike(like, escape="\\"))


async def _project(c: Clause, qc: QueryCtx) -> Any:
    return func.upper(m.Project.key).in_([k.upper() for k in c.args["in"]])


async def _include_inbox(c: Clause, qc: QueryCtx) -> Any:
    return true()


_CLAUSES: dict[str, Any] = {
    "dims": _dims, "people": _people, "people2": _people2, "priority": _priority, "weight": _weight,
    "impact": _impact, "touched": _touched, "state": _state, "stage": _stage, "type": _type, "tags": _tags,
    "risk": _risk, "violations": _violations, "text": _text, "project": _project, "include_inbox": _include_inbox,
    **{k: _dates for k in ("due_date", "planned_end", "planned_start", "actual_end", "created")},
}  # fmt: skip
