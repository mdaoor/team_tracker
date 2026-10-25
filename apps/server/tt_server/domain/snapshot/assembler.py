"""Snapshot assembler (Phase 7 §7.1): the prototype's `D` object from live data, scoped, ACL-filtered, range-bounded.

Item rows come from the Phase 6 query service (so visibility is exactly the viewer's); everything else is looked up for the
visible item ids / projects only. Violations and habits are filtered by the same `visible()` policies as everywhere else, which
is what redacts wellbeing habits for a project manager.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from tt_schema.enums import (
    ActivityStatus,
    ProjectKind,
    RiskStatus,
    StageCategory,
    TargetType,
    ViolationStatus,
)

from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.domain import insights
from tt_server.domain.errors import Invalid, NotFound
from tt_server.domain.overlay import process as proc
from tt_server.domain.plan import service as plan_svc
from tt_server.domain.snapshot.model import SCHEMA_VERSION, Snapshot
from tt_server.query import service
from tt_server.query.compile import QueryCtx

PAGE = 5000  # the epics list still pages through the generic query
CHUNK = 5000
OPEN_VIOLATION = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


def parse_scope(scope: str) -> tuple[str, str]:
    kind, _, ref = scope.partition(":")
    if kind not in {"workspace", "product", "project", "team", "person"}:
        raise Invalid("invalid_scope", f"unknown scope {scope!r}", pointer="/scope")
    return kind, ref


def _chunks(xs: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(xs), CHUNK):
        yield xs[i : i + CHUNK]


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    return d.date().isoformat() if isinstance(d, datetime) else d.isoformat()


async def _scope_filter(qc: QueryCtx, kind: str, ref: str) -> tuple[dict[str, Any], str]:
    s = qc.session
    if kind == "workspace":
        return {}, f"Workspace: {ref or 'all'}"
    if kind == "project":
        name = await s.scalar(
            select(m.Project.name).where(m.Project.workspace_id == qc.ws_id, m.Project.key == ref.upper())
        )
        if name is None:
            raise NotFound("not_found", f"unknown project {ref!r}", pointer="/scope")
        return {"project": {"in": [ref.upper()]}}, f"Project: {name}"
    if kind == "product":
        prod = await s.scalar(
            select(m.Product).where(m.Product.workspace_id == qc.ws_id, m.Product.key == ref.upper())
        )
        if prod is None:
            raise NotFound("not_found", f"unknown product {ref!r}", pointer="/scope")
        keys = (
            await s.scalars(
                select(m.Project.key)
                .join(m.ProductLink, m.ProductLink.target_id == m.Project.id)
                .where(
                    m.ProductLink.product_id == prod.id,
                    m.ProductLink.target_type == TargetType.PROJECT,
                    m.ProductLink.deleted_at.is_(None),
                )
            )
        ).all()
        return {"project": {"in": sorted(keys) or ["-"]}}, f"Product: {prod.name}"
    if kind == "team":
        return {"people": {"assignee_in_team": ref}}, f"Team: {ref}"
    handle = ref if ref.startswith("@") else "@" + ref
    return {"people": {"assignee": handle}}, f"Person: {handle}"


def _range_filter(frm: date, to: date) -> dict[str, Any]:
    a, b = frm.isoformat(), to.isoformat()
    return {
        "or": [
            {"state": {"category": ["open", "active"]}},
            {"actual_end": {"between": [a, b]}},
            {"planned_end": {"between": [a, b]}},
            {"planned_start": {"between": [a, b]}},
            {"touched": {"since": a}},
        ]
    }


async def assemble(
    qc: QueryCtx,
    ws: m.Workspace,
    *,
    scope: str = "workspace",
    days: int = 14,
    viewer: str,
    scheme_name: str = "default",
) -> dict[str, Any]:
    s = qc.session
    kind, ref = parse_scope(scope)
    scope_f, label = await _scope_filter(qc, kind, ref or ws.slug)
    to = qc.today
    frm = to - timedelta(days=days)
    not_epic = {
        "not": {"type": {"in": ["epic", "phase", "deliverable"]}}
    }  # plan containers have their own sections

    # ---- items ----------------------------------------------------------------------------------------------
    rows, ids_sel = await service.bulk_items(qc, {"and": [scope_f, not_epic, _range_filter(frm, to)]})

    parent_ids = sorted({r["parent_id"] for r in rows if r["parent_id"]})
    epic_key: dict[str, str] = {}
    for ch in _chunks(parent_ids):
        for iid, key in (
            await s.execute(
                select(m.WorkItem.id, m.WorkItem.key)
                .join(m.WorkItemType, m.WorkItemType.id == m.WorkItem.type_id)
                .where(m.WorkItem.id.in_(ch), m.WorkItemType.key == "epic")
            )
        ).all():
            epic_key[iid] = key
    risks_of: dict[str, list[str]] = {}
    evidence: Counter[str] = Counter()
    hours: dict[str, float] = {}
    touched: dict[str, datetime] = {}
    for wid, at in (
        await s.execute(
            select(m.Event.entity_id, func.max(m.Event.occurred_at))
            .where(
                m.Event.entity_type == TargetType.WORK_ITEM,
                m.Event.entity_id.in_(ids_sel),
            )
            .group_by(m.Event.entity_id)
        )
    ).all():
        touched[wid] = at
    for wid, rkey in (
        await s.execute(
            select(m.RiskLink.work_item_id, m.Risk.key)
            .join(m.Risk, m.Risk.id == m.RiskLink.risk_id)
            .where(
                m.RiskLink.work_item_id.in_(ids_sel),
                m.RiskLink.deleted_at.is_(None),
                m.Risk.deleted_at.is_(None),
                m.Risk.status != RiskStatus.CLOSED,
            )
        )
    ).all():
        risks_of.setdefault(wid, []).append(rkey)
    for wid, n in (
        await s.execute(
            select(m.Evidence.work_item_id, func.count())
            .where(m.Evidence.work_item_id.in_(ids_sel), m.Evidence.deleted_at.is_(None))
            .group_by(m.Evidence.work_item_id)
        )
    ).all():
        evidence[wid] = n
    for wid, mins in (
        await s.execute(
            select(m.TimeEntry.work_item_id, func.sum(m.TimeEntry.minutes))
            .where(
                m.TimeEntry.work_item_id.in_(ids_sel),
                m.TimeEntry.date.between(frm, to),
                m.TimeEntry.deleted_at.is_(None),
            )
            .group_by(m.TimeEntry.work_item_id)
        )
    ).all():
        hours[wid] = round(float(mins or 0) / 60.0, 1)

    # ---- structure ------------------------------------------------------------------------------------------
    teams = {
        t.id: t.slug
        for t in (
            await s.scalars(
                select(m.Team).where(m.Team.workspace_id == qc.ws_id, m.Team.deleted_at.is_(None))
            )
        ).all()
    }
    projects = (
        await s.scalars(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == qc.ws_id,
                    m.Project.deleted_at.is_(None),
                    m.Project.kind == ProjectKind.STANDARD,
                ),
                qc.acl,
                m.Project,
            ).order_by(m.Project.key)
        )
    ).all()
    in_scope = {r["project"] for r in rows}
    if kind == "workspace":
        in_scope = {p.key for p in projects}
    elif kind == "project":
        in_scope.add(ref.upper())
    elif kind == "product":
        in_scope |= set(scope_f["project"]["in"])
    projects = [p for p in projects if p.key in in_scope]
    pkey = {p.id: p.key for p in projects}
    product_of: dict[str, str] = {}
    products: dict[str, str] = {}
    for pid, pk, pname in (
        await s.execute(
            select(m.ProductLink.target_id, m.Product.key, m.Product.name)
            .join(m.Product, m.Product.id == m.ProductLink.product_id)
            .where(
                m.ProductLink.target_type == TargetType.PROJECT,
                m.ProductLink.deleted_at.is_(None),
                m.Product.workspace_id == qc.ws_id,
                m.Product.deleted_at.is_(None),
            )
        )
    ).all():
        if pid in pkey:
            product_of[pkey[pid]] = pk
            products[pk] = pname
    epics = [
        {"key": e["key"], "title": e["title"], "project": e["project"]}
        for e in (
            await service.query_items(
                qc,
                filter={"and": [scope_f, {"type": {"in": ["epic"]}}]},
                sort=[{"field": "key", "dir": "asc"}],
                page={"size": PAGE},
                max_page=PAGE,
            )
        )["items"]
    ]
    plan_parts = await plan_svc.snapshot_parts(s, projects, today=to, acl=qc.acl)
    milestones = plan_parts["milestones"]
    risks = [
        {
            "key": r.key,
            "title": r.title,
            "project": pkey[r.project_id],
            "p": r.probability,
            "i": r.impact,
            "status": r.status.value,
        }
        for r in (
            await s.scalars(
                select(m.Risk)
                .where(
                    m.Risk.project_id.in_(list(pkey)),
                    m.Risk.deleted_at.is_(None),
                    m.Risk.status != RiskStatus.CLOSED,
                )
                .order_by(m.Risk.key)
            )
        ).all()
    ]

    # ---- stages (dominant process; all processes when there are several) -------------------------------------
    proc_count = Counter(r["process"] for r in rows if r["process"])
    pkeys = [k for k, _ in proc_count.most_common()] or [(await proc.get_process(s, qc.ws_id)).key]
    infos: list[dict[str, Any]] = []
    for k in pkeys:
        p = await proc.get_process(s, qc.ws_id, k)
        sts = [
            x for x in await proc.stages_of(s, p.id) if x.category != StageCategory.CLOSED
        ]  # `retired` is not a delivery stage
        infos.append(
            {
                "key": p.key,
                "stages": [x.key for x in sts],
                "stage_expected_days": {x.key: x.expected_duration_days or 0 for x in sts},
            }
        )
    stages, expected = infos[0]["stages"], infos[0]["stage_expected_days"]
    if len(infos) > 1:  # a union keeps every stage the scope uses; the first process orders them
        for info in infos[1:]:
            for st in info["stages"]:
                if st not in expected:
                    stages.append(st)
                    expected[st] = info["stage_expected_days"][st]

    # ---- people ---------------------------------------------------------------------------------------------
    primary_team: dict[str, str] = {}
    for pid, tid in (
        await s.execute(
            select(m.Membership.person_id, m.Membership.team_id)
            .where(m.Membership.left_at.is_(None), m.Membership.deleted_at.is_(None))
            .order_by(m.Membership.joined_at.desc())
        )
    ).all():
        primary_team[pid] = tid
    members = (
        await s.execute(
            select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle, m.Person.display_name)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(m.WorkspaceMember.workspace_id == qc.ws_id, m.WorkspaceMember.deleted_at.is_(None))
            .order_by(m.WorkspaceMember.handle)
        )
    ).all()
    handle_of = {pid: h for pid, h, _ in members}
    people = [{"handle": h, "name": n, "team": teams.get(primary_team.get(pid, ""))} for pid, h, n in members]

    # ---- items in prototype shape ---------------------------------------------------------------------------
    hpd = float((ws.settings or {}).get("hours_per_day", 8))
    out_items = []
    for r in rows:
        est = r["estimate"]
        est_h = None
        if est:
            est_h = (
                est["value"] * hpd
                if est["unit"] == "days"
                else est["value"]
                if est["unit"] == "hours"
                else None
            )
        out_items.append(
            {
                "key": r["key"], "title": r["title"], "project": r["project"], "product": product_of.get(r["project"]),
                "epic": epic_key.get(r["parent_id"]), "type": r["type"], "assignee": r["assignee"], "team": r["team"],
                "stage": r["stage"], "stage_age_days": r["stage_age_days"], "state": r["state_category"],
                "priority": r["priority"], "weight": r["weight"], "estimate_h": est_h,
                "hours_logged": hours.get(r["id"], 0.0), "planned_start": _iso(r["planned_start"]),
                "planned_end": _iso(r["planned_end"]), "actual_end": _iso(r["actual_end"]), "tags": r["tags"],
                "risks": sorted(risks_of.get(r["id"], [])), "last_touched": _iso(touched.get(r["id"])),
                "evidence_count": evidence.get(r["id"], 0),
            }
        )  # fmt: skip

    # ---- project summaries ----------------------------------------------------------------------------------
    stored = await insights.latest_summaries(s, [p.id for p in projects])
    out_projects = []
    for p in projects:
        pd = {
            "key": p.key, "name": p.name, "product": product_of.get(p.key), "methodology": p.methodology.value,
            "team": teams.get(p.team_id) if p.team_id else None, "summary": "",
        }  # fmt: skip
        pd.update(plan_parts["projects"].get(p.key, {}))
        pd["summary"] = stored.get(p.id) or insights.fallback_summary(
            insights.project_facts(pd, out_items, milestones, risks, expected, to)
        )
        out_projects.append(pd)

    # ---- violations / habits / now (all through visible()) --------------------------------------------------
    item_key = {r["id"]: r["key"] for r in rows}
    viol = []
    vstmt = visible(
        select(m.Violation, m.Policy.key)
        .join(m.Policy, m.Policy.id == m.Violation.policy_id)
        .where(m.Violation.workspace_id == qc.ws_id, m.Violation.status.in_(OPEN_VIOLATION), m.Violation.deleted_at.is_(None)),
        qc.acl,
        m.Violation,
    )  # fmt: skip
    for v, pol in (await s.execute(vstmt.order_by(m.Violation.detected_at))).all():
        if v.target_type == TargetType.WORK_ITEM:
            if v.target_id not in item_key:
                continue
            target = item_key[v.target_id]
        elif v.project_id in pkey:
            target = (v.evidence or {}).get("target_label") or v.target_id
        else:
            continue
        ev = v.evidence or {}
        viol.append(
            {
                "policy": pol, "category": v.category.value, "severity": v.severity.value, "target": target,
                "person": handle_of.get(v.person_id or ""), "team": teams.get(v.team_id or ""),
                "project": pkey.get(v.project_id or ""), "detail": str(ev.get("detail", "")),
            }
        )  # fmt: skip
    hab = []
    hstmt = visible(
        select(m.Habit).where(m.Habit.workspace_id == qc.ws_id, m.Habit.deleted_at.is_(None), m.Habit.subject_type == TargetType.PERSON),
        qc.acl,
        m.Habit,
    )  # fmt: skip
    for h in (await s.scalars(hstmt)).all():
        if h.subject_id in handle_of:
            hab.append(
                {
                    "subject": handle_of[h.subject_id],
                    "key": h.key,
                    "polarity": h.polarity.value,
                    "rate": float(h.rate),
                    "trend": h.trend.value,
                }
            )
    now_rows = []
    nstmt = visible(
        select(m.Activity, m.WorkItem.key, m.WorkItem.title, m.Project.key)
        .join(m.WorkItem, m.WorkItem.id == m.Activity.work_item_id)
        .join(m.Project, m.Project.id == m.Activity.project_id)
        .where(m.Project.workspace_id == qc.ws_id, m.Activity.status == ActivityStatus.ACTIVE),
        qc.acl,
        m.Activity,
    )  # fmt: skip
    keys = set(item_key.values())
    for a, ik, it, pk in (await s.execute(nstmt.order_by(m.Activity.started_at))).all():
        if ik in keys:
            now_rows.append(
                {
                    "person": handle_of.get(a.person_id, "?"), "item": ik, "title": it, "project": pk,
                    "since_min": int((qc.now - a.started_at).total_seconds() // 60), "kind": a.kind.value,
                }
            )  # fmt: skip

    snap = {
        "schema_version": SCHEMA_VERSION,
        "meta": {"viewer": viewer, "scope_label": label, "generated_for": viewer},
        "as_of": qc.now.astimezone(UTC).isoformat(timespec="seconds"),
        "workspace": ws.slug,
        "range": {"from": frm.isoformat(), "to": to.isoformat()},
        "weight_scheme": scheme_name,
        "stages": stages,
        "stage_expected_days": expected,
        "processes": infos if len(infos) > 1 else None,
        "people": people,
        "teams": sorted(teams.values()),
        "products": [{"key": k, "name": v} for k, v in sorted(products.items())],
        "projects": out_projects,
        "epics": epics,
        "milestones": milestones,
        "phases": plan_parts["phases"],
        "deliverables": plan_parts["deliverables"],
        "risks": risks,
        "items": out_items,
        "violations": viol,
        "habits": hab,
        "now": now_rows,
    }
    # The contract is enforced on everything but the bulk `items` list (validated on a sample here, in full by the test-suite):
    # 50 k pydantic models cost ~0.5 s per cold snapshot and every item comes from this one code path.
    head = {**snap, "items": snap["items"][:50]}
    Snapshot.model_validate(head)
    return snap
