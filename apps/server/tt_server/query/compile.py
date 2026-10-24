"""Filter AST -> SQLAlchemy predicate over `work_items` (Phase 6 §6.1).

Everything is parameterised; names from the document are looked up (dimension keys, handles, team slugs) and unknown ones raise
`FilterError` with the JSON pointer of the offending value. The caller joins `Project` and `WorkflowState` and applies
`acl.visible()` last, so a shared view only ever shows what the opener may see.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import and_, case, exists, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from tt_filter import And, Clause, FilterError, Node, Not, Or, resolve
from tt_schema.enums import (
    AssignmentRole,
    ItemTypeKey,
    Priority,
    ProjectKind,
    ReportingKind,
    RiskStatus,
    TargetType,
    ViolationStatus,
)

from tt_server.acl.context import AclContext
from tt_server.acl.reporting import reports_of
from tt_server.db import models as m

PRIORITY_ORDER = [p.value for p in Priority]
SEVERITY_ORDER = ["info", "warning", "violation", "critical"]
OPEN_VIOLATION = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


@dataclass
class QueryCtx:
    session: AsyncSession
    ws_id: str
    acl: AclContext
    person_id: str
    handle: str
    tz: str = "UTC"
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    scheme_id: str | None = None
    _dims: dict[str, m.Dimension] = field(default_factory=dict)
    _teams: list[m.Team] | None = None

    @property
    def today(self) -> date:
        try:
            return self.now.astimezone(ZoneInfo(self.tz)).date()
        except Exception:  # noqa: BLE001
            return self.now.date()

    def day(self, token: str) -> date:
        d: date = resolve(token, self.today)
        return d

    def start_of(self, d: date) -> datetime:
        try:
            return datetime.combine(d, time.min, tzinfo=ZoneInfo(self.tz))
        except Exception:  # noqa: BLE001
            return datetime.combine(d, time.min, tzinfo=UTC)

    async def dimension(self, key: str, ptr: str) -> m.Dimension:
        if key not in self._dims:
            d = await self.session.scalar(
                select(m.Dimension).where(
                    m.Dimension.workspace_id == self.ws_id,
                    func.lower(m.Dimension.key) == key.lower(),
                    m.Dimension.deleted_at.is_(None),
                )
            )
            if d is None:
                raise FilterError(ptr, f"unknown dimension {key!r}")
            self._dims[key] = d
        return self._dims[key]

    async def person_ids(self, handles: list[str], ptr: str) -> list[str]:
        out: list[str] = []
        for h in handles:
            if h == "me":
                out.append(self.person_id)
                continue
            handle = h if h.startswith("@") else "@" + h
            pid = await self.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == self.ws_id, m.WorkspaceMember.handle == handle
                )
            )
            if pid is None:
                raise FilterError(ptr, f"unknown person {h!r}")
            out.append(pid)
        return out

    async def team_ids(self, slug: str, ptr: str) -> set[str]:
        if self._teams is None:
            self._teams = list(
                (await self.session.scalars(select(m.Team).where(m.Team.workspace_id == self.ws_id))).all()
            )
        root = next((t for t in self._teams if slug in (t.slug, t.id)), None)
        if root is None:
            raise FilterError(ptr, f"unknown team {slug!r}")
        ids, frontier = {root.id}, {root.id}
        while frontier:
            kids = {t.id for t in self._teams if t.parent_team_id in frontier} - ids
            ids |= kids
            frontier = kids
        return ids

    async def scheme(self, ref: str | None, ptr: str) -> str | None:
        if ref is None:
            return self.scheme_id
        sid = await self.session.scalar(
            select(m.WeightScheme.id).where(
                m.WeightScheme.workspace_id == self.ws_id,
                m.WeightScheme.deleted_at.is_(None),
                (m.WeightScheme.id == ref) | (m.WeightScheme.name == ref),
            )
        )
        if sid is None:
            raise FilterError(ptr, f"unknown weight scheme {ref!r}")
        return str(sid)


def weight_expr(scheme_id: str | None) -> Any:
    """The item's weight under a scheme, without touching `WorkItem.weight` (§13.4)."""
    if scheme_id is None:
        return m.WorkItem.weight
    sub = (
        select(m.Weight.value)
        .where(m.Weight.work_item_id == m.WorkItem.id, m.Weight.scheme_id == scheme_id)
        .correlate(m.WorkItem)
        .scalar_subquery()
    )
    return func.coalesce(sub, 0)


def priority_rank() -> Any:
    return case(*[(m.WorkItem.priority == Priority(p), i) for i, p in enumerate(PRIORITY_ORDER)], else_=-1)


def wants_inbox(node: Node) -> bool:
    """`include_inbox: true` at the top level (ACL still decides whose Inbox items are visible)."""
    items = node.items if isinstance(node, And) else (node,)
    return any(isinstance(n, Clause) and n.kind == "include_inbox" and n.args["value"] for n in items)


async def compile_node(node: Node, qc: QueryCtx) -> Any:
    if isinstance(node, And):
        return and_(true(), *[await compile_node(n, qc) for n in node.items]) if node.items else true()
    if isinstance(node, Or):
        return or_(*[await compile_node(n, qc) for n in node.items])
    if isinstance(node, Not):
        return not_(await compile_node(node.item, qc))
    # SQL's three-valued logic would make NOT(unknown) unknown: treat "unknown" as "no match" so `not` is a true complement
    return func.coalesce(await _CLAUSES[node.kind](node, qc), sa.false())


async def compile_filter(node: Node, qc: QueryCtx) -> Any:
    pred = await compile_node(node, qc)
    if not wants_inbox(node):
        pred = and_(pred, m.Project.kind != ProjectKind.INBOX)
    return pred


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
