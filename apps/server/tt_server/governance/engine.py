"""Policy evaluation (Phase 8 §8.4): enumerate targets, build contexts, run CEL, reduce to matches.

`evaluate` is read-only and used by dry-runs, sweeps and on-event checks alike; `apply.py` turns matches into violations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import tt_cel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_filter import parse as parse_filter
from tt_schema.base import utcnow
from tt_schema.enums import ActivityStatus, DailyLogStatus, ProjectKind, ScopeType, TargetType

from tt_server.acl.context import AclContext
from tt_server.db import models as m
from tt_server.governance import context as ctxmod
from tt_server.governance import facts as factmod
from tt_server.governance import service
from tt_server.query.compile import QueryCtx, compile_filter

EVAL_BUDGET_S = 120.0  # wall-clock budget of one policy evaluation (celpy has no step cap)
BATCH = 500


@dataclass
class Match:
    target_type: TargetType
    target_id: str
    person_id: str | None = None
    team_id: str | None = None
    project_id: str | None = None
    product_id: str | None = None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    evaluated: set[tuple[str, str]] = field(default_factory=set)  # (target_type, target_id) we looked at
    matches: list[Match] = field(default_factory=list)
    sweep: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class Ref:
    """One evaluable target before its context is built."""

    type: TargetType
    id: str
    item_id: str | None = None
    activity: m.Activity | None = None
    log: m.DailyLog | None = None
    person_id: str | None = None
    project_id: str | None = None


async def template_for(s: AsyncSession, policy: m.Policy) -> tt_cel.Template | None:
    """The stored template a tier-1 policy was built from (carries `detail` and `grouping`); raw-CEL policies have none."""
    if not policy.condition_template_id:
        return None
    row = await s.get(m.PolicyTemplate, policy.condition_template_id)
    return service.template_from_row(row) if row is not None else None


async def team_scope_ids(s: AsyncSession, ws_id: str, team_id: str) -> set[str]:
    teams = (
        await s.execute(select(m.Team.id, m.Team.parent_team_id).where(m.Team.workspace_id == ws_id))
    ).all()
    out, frontier = {team_id}, {team_id}
    while frontier:
        frontier = {t for t, p in teams if p in frontier} - out
        out |= frontier
    return out


async def _scope_pred(s: AsyncSession, ws: m.Workspace, policy: m.Policy) -> Any:
    """SQL predicate over `work_items` for the policy's scope (None = whole workspace)."""
    st, sid = policy.scope_type, policy.scope_id
    if st in (ScopeType.WORKSPACE, ScopeType.NONE) or not sid:
        return None
    if st == ScopeType.PROJECT:
        return m.WorkItem.project_id == sid
    if st == ScopeType.TEAM:
        return m.WorkItem.team_id.in_(await team_scope_ids(s, ws.id, sid))
    if st == ScopeType.PRODUCT:
        links = select(m.ProductLink.target_id).where(
            m.ProductLink.product_id == sid,
            m.ProductLink.target_type == TargetType.PROJECT,
            m.ProductLink.deleted_at.is_(None),
        )
        return m.WorkItem.project_id.in_(links)
    if st == ScopeType.PROCESS:
        return m.WorkItem.id.in_(
            select(m.StagePlacement.target_id).where(
                m.StagePlacement.process_id == sid,
                m.StagePlacement.target_type == TargetType.WORK_ITEM,
                m.StagePlacement.exited_at.is_(None),
            )
        )
    return None


def system_qc(s: AsyncSession, ws: m.Workspace, now: datetime) -> QueryCtx:
    acl = AclContext(ws_id=ws.id, person_id="system", is_admin=True)
    return QueryCtx(session=s, ws_id=ws.id, acl=acl, person_id="system", handle="@system", tz="UTC", now=now)


async def candidate_statement(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, now: datetime, only: list[str] | None = None
) -> Any:
    """`SELECT id` of the items in scope that pass `candidate_filter`: the filter and the scope are SQL, not Python (Phase 6 compiler)."""
    cf = policy.candidate_filter or {}
    stmt = (
        select(m.WorkItem.id)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(
            m.Project.workspace_id == ws.id,
            m.WorkItem.deleted_at.is_(None),
            m.WorkItem.is_template.is_(False),
        )
    )
    if not cf.get("include_inbox"):
        stmt = stmt.where(m.Project.kind != ProjectKind.INBOX)
    if cf:
        stmt = stmt.where(await compile_filter(parse_filter(cf), system_qc(s, ws, now)))
    sp = await _scope_pred(s, ws, policy)
    if sp is not None:
        stmt = stmt.where(sp)
    if only is not None:
        stmt = stmt.where(m.WorkItem.id.in_(only))
    return stmt.order_by(m.WorkItem.key)


async def candidate_item_ids(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, now: datetime, only: list[str] | None = None
) -> list[str]:
    """Item ids in scope that pass `candidate_filter`. Governance is a system actor: no ACL."""
    return list((await s.scalars(await candidate_statement(s, ws, policy, now, only))).all())


async def enumerate_targets(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, now: datetime, only: list[Ref] | None
) -> list[Ref]:
    t = policy.target
    if only is not None and t in (
        TargetType.ACTIVITY,
        TargetType.DAILY_LOG,
        TargetType.PERSON,
        TargetType.PROJECT,
    ):
        return [r for r in only if r.type == t]
    if t in (TargetType.WORK_ITEM, TargetType.STAGE_PLACEMENT):
        wanted = [r.item_id for r in only if r.item_id] if only is not None else None
        ids = await candidate_item_ids(s, ws, policy, now, wanted)
        if t == TargetType.WORK_ITEM:
            return [Ref(TargetType.WORK_ITEM, i, item_id=i) for i in ids]
        out: list[Ref] = []
        for k in range(0, len(ids), 5000):
            for pid, tid in (
                await s.execute(
                    select(m.StagePlacement.id, m.StagePlacement.target_id).where(
                        m.StagePlacement.target_type == TargetType.WORK_ITEM,
                        m.StagePlacement.target_id.in_(ids[k : k + 5000]),
                        m.StagePlacement.exited_at.is_(None),
                    )
                )
            ).all():
                out.append(Ref(TargetType.STAGE_PLACEMENT, pid, item_id=tid))
        return out
    if t == TargetType.ACTIVITY:
        acts = (
            await s.scalars(
                select(m.Activity)
                .join(m.Project, m.Project.id == m.Activity.project_id)
                .where(m.Project.workspace_id == ws.id, m.Activity.status == ActivityStatus.ACTIVE)
            )
        ).all()
        return [
            Ref(TargetType.ACTIVITY, a.id, item_id=a.work_item_id, activity=a, person_id=a.person_id)
            for a in acts
        ]
    if t == TargetType.PERSON:
        rows = (
            await s.scalars(
                select(m.WorkspaceMember).where(
                    m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.deleted_at.is_(None)
                )
            )
        ).all()
        return [Ref(TargetType.PERSON, r.person_id, person_id=r.person_id) for r in rows]
    if t == TargetType.DAILY_LOG:
        logs = (
            await s.scalars(
                select(m.DailyLog).where(
                    m.DailyLog.workspace_id == ws.id, m.DailyLog.status == DailyLogStatus.DRAFT
                )
            )
        ).all()
        return [Ref(TargetType.DAILY_LOG, d.id, log=d, person_id=d.person_id) for d in logs]
    if t == TargetType.PROJECT:
        pj = (
            await s.scalars(
                select(m.Project).where(
                    m.Project.workspace_id == ws.id,
                    m.Project.deleted_at.is_(None),
                    m.Project.kind == ProjectKind.STANDARD,
                )
            )
        ).all()
        return [Ref(TargetType.PROJECT, p.id, project_id=p.id) for p in pj]
    return []


async def _project_roots(s: AsyncSession, ws_id: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    teams = {
        t.id: t.slug for t in (await s.scalars(select(m.Team).where(m.Team.workspace_id == ws_id))).all()
    }
    prod = {
        pid: key
        for pid, key in (
            await s.execute(
                select(m.ProductLink.target_id, m.Product.key)
                .join(m.Product, m.Product.id == m.ProductLink.product_id)
                .where(
                    m.ProductLink.target_type == TargetType.PROJECT,
                    m.ProductLink.deleted_at.is_(None),
                    m.ProductLink.target_id.in_(ids),
                )
            )
        ).all()
    }
    out = {}
    for p in (await s.scalars(select(m.Project).where(m.Project.id.in_(ids)))).all():
        out[p.id] = {
            "id": p.id, "key": p.key, "methodology": p.methodology.value, "status": p.status.value,
            "planned_end": factmod.date_ts(p.planned_end), "team": teams.get(p.team_id or ""), "product": prod.get(p.id),
            "_team_id": p.team_id,
        }  # fmt: skip
    return out


async def _person_roots(s: AsyncSession, ws_id: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    primary: dict[str, str] = {}
    teams = {
        t.id: t.slug for t in (await s.scalars(select(m.Team).where(m.Team.workspace_id == ws_id))).all()
    }
    for pid, tid in (
        await s.execute(
            select(m.Membership.person_id, m.Membership.team_id)
            .where(
                m.Membership.person_id.in_(ids),
                m.Membership.left_at.is_(None),
                m.Membership.deleted_at.is_(None),
            )
            .order_by(m.Membership.joined_at.desc())
        )
    ).all():
        primary[pid] = teams.get(tid, "")
    out = {}
    for mem, person in (
        await s.execute(
            select(m.WorkspaceMember, m.Person)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.person_id.in_(ids))
        )
    ).all():
        out[person.id] = ctxmod.person_root(mem, person, primary.get(person.id))
    return out


async def evaluate(
    s: AsyncSession, ws: m.Workspace, policy: m.Policy, *, now: datetime | None = None, event: m.Event | None = None,
    only: list[Ref] | None = None, limit: int | None = None,
) -> EvalResult:  # fmt: skip
    now = now or utcnow()
    tmpl = await template_for(s, policy)
    compiled = tt_cel.compile(policy.condition_cel)
    details = (
        tt_cel.compile_detail(tmpl.detail, tmpl.params, (policy.template_params or {})) if tmpl else None
    )
    needs = compiled.fact_groups | (details.fact_groups if details else set())
    windows = compiled.windows | (details.windows if details else set())
    result = EvalResult(sweep=only is None)
    refs = await enumerate_targets(s, ws, policy, now, only)
    if tmpl and tmpl.grouping == "project_stage":
        refs = await _group_representatives(s, ws.id, refs)
    if limit is not None:
        refs = refs[:limit]
    ev_root = ctxmod.event_root(event)
    deadline = time.monotonic() + EVAL_BUDGET_S
    for k in range(0, len(refs), BATCH):
        batch = refs[k : k + BATCH]
        item_ids = sorted({r.item_id for r in batch if r.item_id})
        items = await ctxmod.load_items(s, ws.id, item_ids)
        proj_ids = {r.project_id for r in batch if r.project_id} | {i["_project_id"] for i in items.values()}
        projects = await _project_roots(s, ws.id, {p for p in proj_ids if p})
        person_ids = {r.person_id for r in batch if r.person_id} | {
            i["_assignee_id"] for i in items.values() if i["_assignee_id"]
        }
        persons = await _person_roots(s, ws.id, person_ids)
        item_keys = {i["id"]: i["key"] for i in items.values()}
        fx = await factmod.load(
            s,
            ws.id,
            needs,
            now=now,
            item_ids=item_ids,
            person_ids=sorted(person_ids),
            item_rows=items,
            windows=windows,
            project_keys=[p["key"] for p in projects.values()],
        )
        funcs = tt_cel.bind(fx)
        for r in batch:
            root = ctxmod.blank_context()
            root["event"] = ev_root
            it = items.get(r.item_id or "") if r.item_id else None
            if it:
                proj = projects.get(it["_project_id"], root["project"])
                root["item"] = {**{k2: v for k2, v in it.items() if not k2.startswith("_")}, "project": proj}
                root["stage"] = {**it["stage"], "_wip_key": f"{proj.get('key')}|{it['stage'].get('key')}"}
                root["placement"] = it["placement"]
                root["project"] = proj
                root["person"] = persons.get(it["_assignee_id"] or "", root["person"])
            if r.type == TargetType.ACTIVITY and r.activity is not None:
                root["activity"] = ctxmod.activity_root(
                    r.activity, item_keys.get(r.activity.work_item_id or ""), now
                )
                root["person"] = persons.get(r.activity.person_id, root["person"])
            if r.type == TargetType.DAILY_LOG and r.log is not None:
                root["daily_log"] = ctxmod.daily_log_root(r.log)
                root["person"] = persons.get(r.log.person_id, root["person"])
            if r.type == TargetType.PERSON:
                root["person"] = persons.get(r.person_id or "", root["person"])
            if r.type == TargetType.PROJECT:
                root["project"] = projects.get(r.project_id or "", root["project"])
            if (
                time.monotonic() > deadline
            ):  # a runaway condition must not hold a worker: stop, and do not auto-resolve the rest
                result.errors.append(
                    f"evaluation budget of {EVAL_BUDGET_S:g}s exceeded after {len(result.evaluated)} targets"
                )
                result.sweep = False
                return result
            result.evaluated.add(_vkey(r, it, tmpl))
            try:
                hit = compiled.evaluate(root, funcs, now=now)
            except tt_cel.CelError as exc:
                result.errors.append(f"{r.id}: {exc.message}")
                continue
            if hit is not True:
                continue
            match = _match(r, root, it, details, funcs, now, tmpl)
            result.matches.append(match)
    return result


def _vkey(r: Ref, it: dict[str, Any] | None, tmpl: Any) -> tuple[str, str]:
    """The (target type, id) a violation of this reference is filed under: items for placements, `project:stage` for groups."""
    if tmpl is not None and tmpl.grouping == "project_stage" and it:
        return TargetType.PROJECT.value, f"{it['_project_id']}:{it['stage']['key']}"
    if r.type == TargetType.STAGE_PLACEMENT and it:
        return TargetType.WORK_ITEM.value, it["id"]
    return r.type.value, r.id


def _match(
    r: Ref,
    root: dict[str, Any],
    it: dict[str, Any] | None,
    details: Any,
    funcs: Any,
    now: datetime,
    tmpl: Any,
) -> Match:
    detail = ""
    if details is not None:
        try:
            detail = details.render(root, funcs, now=now)
        except tt_cel.CelError:
            detail = ""
    person_id = (it or {}).get("_assignee_id") or r.person_id
    mt = Match(
        r.type, r.id, person_id=person_id, team_id=(it or {}).get("_team_id"), project_id=(it or {}).get("_project_id") or r.project_id,
        detail=detail, evidence={"detail": detail},
    )  # fmt: skip
    if r.type == TargetType.ACTIVITY and r.activity is not None:
        mt.project_id = r.activity.project_id
    if it:
        mt.evidence["item"] = it["key"]
    if r.type == TargetType.STAGE_PLACEMENT and it:
        mt.target_type, mt.target_id = (
            TargetType.WORK_ITEM,
            it["id"],
        )  # filed on the item; the placement is evidence
        mt.evidence["placement_id"] = r.id
    if tmpl and tmpl.grouping == "project_stage" and it:
        mt.target_type, mt.target_id = TargetType.PROJECT, f"{it['_project_id']}:{it['stage']['key']}"
        mt.person_id = None
        mt.team_id = root["project"].get("_team_id")
        mt.evidence["target_label"] = f"{root['project']['key']}/{it['stage']['key']}"
    return mt


async def _group_representatives(s: AsyncSession, ws_id: str, refs: list[Ref]) -> list[Ref]:
    """`project_stage` policies fire once per (project, stage): keep one placement per group as its representative."""
    if not refs:
        return refs
    ids = [r.item_id for r in refs if r.item_id]
    keys: dict[str, tuple[str, str]] = {}
    for k in range(0, len(ids), 5000):
        for iid, pj, st in (
            await s.execute(
                select(m.WorkItem.id, m.WorkItem.project_id, m.Stage.key)
                .join(
                    m.StagePlacement,
                    (m.StagePlacement.target_id == m.WorkItem.id)
                    & (m.StagePlacement.exited_at.is_(None))
                    & (m.StagePlacement.target_type == TargetType.WORK_ITEM),
                )
                .join(m.Stage, m.Stage.id == m.StagePlacement.stage_id)
                .where(m.WorkItem.id.in_(ids[k : k + 5000]))
            )
        ).all():
            keys[iid] = (pj, st)
    seen: set[tuple[str, str]] = set()
    out = []
    for r in refs:
        g = keys.get(r.item_id or "")
        if g and g not in seen:
            seen.add(g)
            out.append(r)
    return out
