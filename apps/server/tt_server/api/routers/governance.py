"""Phase 8 API: policy templates, policies, violations, habits, metric snapshots, notifications (object model §8)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any

import tt_cel
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    EventSource,
    HabitStatus,
    PolicyCategory,
    PolicyTrigger,
    ScopeType,
    Severity,
    TargetType,
    ViolationStatus,
)

from tt_server.acl.visibility import visible
from tt_server.api.authz import check
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain.common import emit, row_dict
from tt_server.domain.names import Names
from tt_server.governance import apply as applymod
from tt_server.governance import engine, habits, runner, service

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["governance"])

OPEN = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


def _ev(uow: Uow, actor: Actor, etype: TargetType, eid: str, event: str, **payload: Any) -> None:
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=etype,
        entity_id=eid,
        event_type=event,
        payload={"after": payload},
    )


# ---------------------------------------------------------------- context schema + templates
@router.get("/meta/policy-context")
async def policy_context() -> dict[str, Any]:
    """The context schema v1 the editor type-checks against: roots, object fields and registered functions with their docs."""
    return {
        "version": tt_cel.VERSION, "roots": tt_cel.ROOTS, "objects": tt_cel.OBJECTS,
        "functions": [{"name": f.name, "receiver": f.receiver, "namespace": f.namespace, "args": list(f.args), "returns": f.returns, "doc": f.doc} for f in tt_cel.FUNCTIONS],
    }  # fmt: skip


@router.get("/policy-templates")
async def list_templates(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.read")
    rows = (
        await uow.session.scalars(
            select(m.PolicyTemplate)
            .where(
                (m.PolicyTemplate.workspace_id == actor.ws.id) | m.PolicyTemplate.workspace_id.is_(None),
                m.PolicyTemplate.deleted_at.is_(None),
            )
            .order_by(m.PolicyTemplate.key)
        )
    ).all()
    shadowed = {r.key for r in rows if r.workspace_id}
    return {
        "data": [service.template_view(r) for r in rows if r.workspace_id or r.key not in shadowed],
        "next_cursor": None,
    }


class TemplateIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str
    description: str | None = None
    category: PolicyCategory
    cel_source: str
    target: str = "work_item"
    trigger: str = "scheduled"
    severity: Severity = Severity.WARNING
    schedule: str | None = None
    event_types: list[str] = Field(default_factory=list)
    params: list[dict[str, Any]] = Field(default_factory=list)
    detail: str = ""
    candidate_filter: dict[str, Any] | None = None
    published_scope: str = "workspace"


@router.post("/policy-templates", status_code=201)
async def create_template(body: TemplateIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.admin")
    try:
        params = tuple(tt_cel.Param(**p) for p in body.params)
        tt_cel.compile(tt_cel.render(body.cel_source, params, {p.name: p.default for p in params}))
        tt_cel.compile_detail(body.detail, params, {p.name: p.default for p in params})
    except (tt_cel.CelError, TypeError) as exc:
        raise ApiError(
            400, "invalid_condition", getattr(exc, "message", str(exc)), pointer="/cel_source"
        ) from exc
    if body.published_scope not in {"workspace", "shared"}:
        raise ApiError(
            400, "invalid_scope", "published_scope is workspace or shared", pointer="/published_scope"
        )
    row = m.PolicyTemplate(
        id=new_id(), key=body.key, name=body.name, category=body.category, cel_source=body.cel_source, description=body.description, workspace_id=actor.ws.id,
        is_builtin=False, published_scope=body.published_scope,
        params={"parameters": body.params, "target": body.target, "trigger": body.trigger, "severity": body.severity.value, "schedule": body.schedule, "event_types": body.event_types, "detail": body.detail, "candidate_filter": body.candidate_filter},
    )  # fmt: skip
    uow.session.add(row)
    await uow.session.flush()
    _ev(uow, actor, TargetType.PERSON, actor.person.id, "policy.template_created", template=row.key)
    await uow.commit()
    return service.template_view(row)


@router.post("/policy-templates/{tid}:copy", status_code=201)
async def copy_template(tid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Import a template another workspace published as `shared`: an independent copy (§14.2)."""
    check(actor, "policy.admin")
    row = await uow.session.scalar(
        select(m.PolicyTemplate).where(
            m.PolicyTemplate.id == tid,
            (m.PolicyTemplate.published_scope == "shared")
            | (m.PolicyTemplate.workspace_id == actor.ws.id)
            | m.PolicyTemplate.workspace_id.is_(None),
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "template not found or not shared")
    copy = await service.copy_template(uow.session, row, actor.ws.id, actor=actor.person.id)
    _ev(
        uow,
        actor,
        TargetType.PERSON,
        actor.person.id,
        "policy.template_created",
        template=copy.key,
        copied_from=row.id,
    )
    await uow.commit()
    return service.template_view(copy)


@router.post("/policy-templates/{tid}:publish")
async def publish_template(tid: str, actor: CurrentActor, uow: Uow, shared: bool = True) -> dict[str, Any]:
    check(actor, "policy.admin")
    row = await uow.session.scalar(
        select(m.PolicyTemplate).where(
            m.PolicyTemplate.id == tid, m.PolicyTemplate.workspace_id == actor.ws.id
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "template not found")
    row.published_scope = "shared" if shared else "workspace"
    _ev(
        uow,
        actor,
        TargetType.PERSON,
        actor.person.id,
        "policy.template_published",
        template=row.key,
        scope=row.published_scope,
    )
    await uow.commit()
    return service.template_view(row)


# ---------------------------------------------------------------- policies
class PolicyIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1)
    description: str | None = None
    template: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    condition_cel: str | None = None
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None
    target: TargetType | None = None
    trigger: PolicyTrigger | None = None
    event_types: list[str] | None = None
    schedule: str | None = None
    candidate_filter: dict[str, Any] | None = None
    severity: Severity | None = None
    category: PolicyCategory | None = None
    notify: dict[str, Any] = Field(default_factory=dict)
    auto_action: dict[str, Any] | None = None
    grace_period: str | None = None


class PolicyPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    params: dict[str, Any] | None = None
    condition_cel: str | None = None
    scope_type: ScopeType | None = None
    scope_id: str | None = None
    event_types: list[str] | None = None
    schedule: str | None = None
    candidate_filter: dict[str, Any] | None = None
    severity: Severity | None = None
    category: PolicyCategory | None = None
    notify: dict[str, Any] | None = None
    auto_action: dict[str, Any] | None = None
    grace_period: str | None = None


async def _policy(actor: Actor, uow: Uow, pid: str) -> m.Policy:
    p = await uow.session.scalar(
        select(m.Policy).where(
            (m.Policy.id == pid) | (m.Policy.key == pid),
            m.Policy.workspace_id == actor.ws.id,
            m.Policy.deleted_at.is_(None),
        )
    )
    if p is None:
        raise ApiError(404, "not_found", "policy not found")
    return p


def _can_raw(actor: Actor) -> bool:
    return actor.acl.can("policy.admin")


@router.post("/policies", status_code=201)
async def create_policy(body: PolicyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.write")
    s = uow.session
    comp = await service.compile_condition(s, actor.ws.id, body.model_dump(), raw_allowed=_can_raw(actor))
    comp.pop("_template", None)
    over = {
        k: v
        for k, v in {
            "target": body.target,
            "trigger": body.trigger,
            "event_types": body.event_types,
            "schedule": body.schedule,
            "candidate_filter": body.candidate_filter,
            "severity": body.severity,
            "category": body.category,
        }.items()
        if v is not None
    }
    f = {
        "target": TargetType.WORK_ITEM,
        "trigger": PolicyTrigger.SCHEDULED,
        "event_types": None,
        "schedule": None,
        "candidate_filter": None,
        "severity": Severity.WARNING,
        "category": PolicyCategory.PROCESS,
        **comp,
        **over,
    }
    f["target"], f["trigger"] = TargetType(f["target"]), PolicyTrigger(f["trigger"])
    f["severity"], f["category"] = Severity(f["severity"]), PolicyCategory(f["category"])
    service.validate_fields(
        {**f, "notify": body.notify, "auto_action": body.auto_action, "grace_period": body.grace_period}
    )
    if await s.scalar(
        select(m.Policy.id).where(
            m.Policy.workspace_id == actor.ws.id, m.Policy.key == body.key, m.Policy.deleted_at.is_(None)
        )
    ):
        raise ApiError(409, "conflict", f"a policy with key {body.key!r} exists", pointer="/key")
    sid = await service.resolve_scope(s, actor.ws.id, body.scope_type, body.scope_id)
    p = m.Policy(
        id=new_id(), workspace_id=actor.ws.id, key=body.key, name=body.name, description=body.description, owner_id=actor.person.id,
        scope_type=body.scope_type, scope_id=sid, notify=body.notify, auto_action=body.auto_action, grace_period=body.grace_period, is_active=False,
        context_schema_version=tt_cel.VERSION, updated_at=utcnow(), **f,
    )  # fmt: skip
    s.add(p)
    await s.flush()
    _ev(
        uow,
        actor,
        TargetType.PERSON,
        actor.person.id,
        "policy.created",
        policy=p.key,
        template=body.template,
        after_def=service.definition(p),
    )
    await uow.commit()
    return service.policy_view(p)


@router.get("/policies")
async def list_policies(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.read")
    rows = (
        await uow.session.scalars(
            select(m.Policy)
            .where(m.Policy.workspace_id == actor.ws.id, m.Policy.deleted_at.is_(None))
            .order_by(m.Policy.key)
        )
    ).all()
    tests = {
        r.policy_id: r
        for r in (await uow.session.scalars(select(m.PolicyTestRun).order_by(m.PolicyTestRun.ran_at))).all()
    }
    return {"data": [service.policy_view(p, tests.get(p.id)) for p in rows], "next_cursor": None}


@router.get("/policies/{pid}")
async def get_policy(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.read")
    p = await _policy(actor, uow, pid)
    last = await uow.session.scalar(
        select(m.PolicyTestRun)
        .where(m.PolicyTestRun.policy_id == p.id)
        .order_by(m.PolicyTestRun.ran_at.desc())
    )
    return service.policy_view(p, last)


@router.patch("/policies/{pid}")
async def patch_policy(pid: str, body: PolicyPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.write")
    s = uow.session
    p = await _policy(actor, uow, pid)
    data = body.model_dump(exclude_unset=True)
    before_def = service.definition(p)
    if "params" in data or "condition_cel" in data:
        src = {
            "template": p.condition_template_id,
            "params": data.get("params", p.template_params or {}),
            "condition_cel": data.get("condition_cel"),
        }
        if p.condition_template_id and "condition_cel" not in data:
            comp = await service.compile_condition(s, actor.ws.id, src, raw_allowed=_can_raw(actor))
            comp.pop("_template", None)
            p.condition_cel, p.template_params = comp["condition_cel"], comp["template_params"]
        else:
            comp = await service.compile_condition(
                s, actor.ws.id, {"condition_cel": data["condition_cel"]}, raw_allowed=_can_raw(actor)
            )
            p.condition_cel, p.condition_template_id, p.template_params = comp["condition_cel"], None, None
        data.pop("params", None)
        data.pop("condition_cel", None)
    if "scope_type" in data or "scope_id" in data:
        st = data.get("scope_type", p.scope_type)
        p.scope_type, p.scope_id = st, await service.resolve_scope(s, actor.ws.id, st, data.get("scope_id"))
        data.pop("scope_type", None)
        data.pop("scope_id", None)
    for k, v in data.items():
        setattr(p, k, v)
    service.validate_fields(
        {
            "target": p.target,
            "trigger": p.trigger,
            "event_types": p.event_types,
            "schedule": p.schedule,
            "grace_period": p.grace_period,
            "candidate_filter": p.candidate_filter,
            "notify": p.notify,
            "auto_action": p.auto_action,
        }
    )
    p.is_active = (
        False if ({"params", "condition_cel"} & set(body.model_dump(exclude_unset=True))) else p.is_active
    )  # an edited rule must be re-tested before it runs again
    p.updated_at = (
        utcnow()
    )  # the "last edit" mark a dry-run must postdate; toggling `is_active` does not move it
    _ev(
        uow,
        actor,
        TargetType.PERSON,
        actor.person.id,
        "policy.updated",
        policy=p.key,
        fields=sorted(body.model_dump(exclude_unset=True)),
        before_def=before_def,
        after_def=service.definition(p),
    )
    await uow.commit()
    return service.policy_view(p)


@router.get("/policies/{pid}/versions")
async def policy_versions(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Version history, newest first, from the event log: who changed what, with the full definition at each point (restorable)."""
    check(actor, "policy.read")
    p = await _policy(actor, uow, pid)
    rows = (
        await uow.session.scalars(
            select(m.Event)
            .where(
                m.Event.workspace_id == actor.ws.id,
                m.Event.event_type.in_(("policy.created", "policy.updated")),
                m.Event.payload["after"]["policy"].astext == p.key,
            )
            .order_by(m.Event.occurred_at.desc(), m.Event.id.desc())
            .limit(100)
        )
    ).all()
    out = []
    for e in rows:
        after = (e.payload or {}).get("after", {})
        new, old = after.get("after_def"), after.get("before_def") or {}
        changed = (
            sorted(k for k in (new or {}) if (new or {}).get(k) != old.get(k))
            if new and old
            else sorted(new or {})
        )
        out.append(
            {
                "event_id": e.id, "at": e.occurred_at, "actor_id": e.actor_id, "kind": e.event_type.split(".")[1],
                "changed": changed, "definition": new,
            }
        )  # fmt: skip
    return {"data": out, "next_cursor": None}


@router.delete("/policies/{pid}", status_code=204)
async def delete_policy(pid: str, actor: CurrentActor, uow: Uow) -> None:
    check(actor, "policy.write")
    p = await _policy(actor, uow, pid)
    p.deleted_at, p.is_active = utcnow(), False
    _ev(uow, actor, TargetType.PERSON, actor.person.id, "policy.deleted", policy=p.key)
    await uow.commit()


class TestIn(BaseModel):
    window: str = Field(default="30d", pattern=r"^\d+[dhw]$")


@router.post("/policies/{pid}:test")
async def test_policy(pid: str, actor: CurrentActor, uow: Uow, body: TestIn | None = None) -> dict[str, Any]:
    """Dry run, nothing is written except the `PolicyTestRun`. `scheduled`: the current candidates. `on_event`: the targets that had a
    matching event inside the window, judged on their *current* state (an approximation; ADR-0013)."""
    check(actor, "policy.write")
    s = uow.session
    p = await _policy(actor, uow, pid)
    window = (body or TestIn()).window
    now = utcnow()
    only = None
    if p.trigger == PolicyTrigger.ON_EVENT:
        since = now - service.window_delta(window)
        types = [t for t in (p.event_types or [])]
        conds = [
            m.Event.event_type == t if not t.endswith("*") else m.Event.event_type.like(t.rstrip("*") + "%")
            for t in types
        ]
        from sqlalchemy import or_

        evs = (
            (
                await s.scalars(
                    select(m.Event)
                    .where(m.Event.workspace_id == actor.ws.id, m.Event.occurred_at >= since, or_(*conds))
                    .limit(5000)
                )
            ).all()
            if conds
            else []
        )
        only = [
            engine.Ref(TargetType.WORK_ITEM, e.entity_id, item_id=e.entity_id)
            for e in evs
            if e.entity_type == TargetType.WORK_ITEM
        ]
        if p.target not in (TargetType.WORK_ITEM, TargetType.STAGE_PLACEMENT):
            only = [engine.Ref(e.entity_type, e.entity_id) for e in evs if e.entity_type == p.target]
        only = list({r.id: r for r in only}.values())
    res = await engine.evaluate(s, actor.ws, p, now=now, only=only if only is not None else None)
    sample = [mt.target_id for mt in res.matches[:25]]
    run = m.PolicyTestRun(
        id=new_id(),
        policy_id=p.id,
        window=window,
        fired_count=len(res.matches),
        sample_target_ids=sample,
        ran_at=now,
    )
    s.add(run)
    await s.flush()
    _ev(
        uow,
        actor,
        TargetType.PERSON,
        actor.person.id,
        "policy.tested",
        policy=p.key,
        fired=len(res.matches),
        window=window,
    )
    await uow.commit()
    return {
        "policy": p.key,
        "window": window,
        "fired_count": len(res.matches),
        "evaluated": len(res.evaluated),
        "sample_target_ids": sample,
        "errors": res.errors[:10],
        "approximation": p.trigger == PolicyTrigger.ON_EVENT,
        "ran_at": now,
    }


async def _set_active(s: Any, p: m.Policy, on: bool) -> None:
    """Flip `is_active` without moving `updated_at` (the "last edited" mark that a dry-run must postdate)."""
    from sqlalchemy import update

    await s.execute(
        update(m.Policy).where(m.Policy.id == p.id).values(is_active=on, updated_at=m.Policy.updated_at)
    )
    await s.refresh(p)


@router.post("/policies/{pid}:activate")
async def activate_policy(pid: str, actor: CurrentActor, uow: Uow, force: bool = False) -> dict[str, Any]:
    check(actor, "policy.write")
    s = uow.session
    p = await _policy(actor, uow, pid)
    last = await s.scalar(
        select(m.PolicyTestRun)
        .where(m.PolicyTestRun.policy_id == p.id)
        .order_by(m.PolicyTestRun.ran_at.desc())
    )
    if (last is None or last.ran_at < p.updated_at) and not (force and actor.acl.is_admin):
        raise ApiError(
            409,
            "test_required",
            "run a dry-run (`:test`) after the last edit before activating (admins may pass ?force=true)",
            details={"last_edit": p.updated_at.isoformat()},
        )
    await _set_active(s, p, True)
    _ev(uow, actor, TargetType.PERSON, actor.person.id, "policy.activated", policy=p.key)
    await uow.commit()
    return service.policy_view(p, last)


@router.post("/policies/{pid}:deactivate")
async def deactivate_policy(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.write")
    p = await _policy(actor, uow, pid)
    await _set_active(uow.session, p, False)
    _ev(uow, actor, TargetType.PERSON, actor.person.id, "policy.deactivated", policy=p.key)
    await uow.commit()
    return service.policy_view(p)


@router.post("/policies/{pid}:run")
async def run_policy_now(
    pid: str, actor: CurrentActor, uow: Uow, at: datetime | None = None
) -> dict[str, Any]:
    """Evaluate an active policy right now (admin): opens and auto-resolves violations like a scheduled run."""
    check(actor, "policy.admin")
    p = await _policy(actor, uow, pid)
    if not p.is_active:
        raise ApiError(409, "inactive", "activate the policy first")
    res = await engine.evaluate(uow.session, actor.ws, p, now=at)
    out = await applymod.apply(uow, actor.ws, p, res, now=at)
    await uow.commit()
    return {
        "matched": len(res.matches),
        "opened": len(out.opened),
        "auto_resolved": len(out.resolved),
        "pending_grace": out.pending,
        "notified": out.notified,
        "actions": out.actions,
        "errors": res.errors[:10],
    }


# ---------------------------------------------------------------- violations
class Note(BaseModel):
    note: str | None = None


class Reason(BaseModel):
    reason: str = Field(min_length=3)


async def _violation(actor: Actor, uow: Uow, vid: str) -> tuple[m.Violation, str]:
    row = (
        await uow.session.execute(
            visible(
                select(m.Violation, m.Policy.key)
                .join(m.Policy, m.Policy.id == m.Violation.policy_id)
                .where(m.Violation.id == vid, m.Violation.workspace_id == actor.ws.id),
                actor.acl,
                m.Violation,
            )
        )
    ).first()
    if row is None:
        raise ApiError(404, "not_found", "violation not found")
    return row[0], row[1]


def _enum(cls: Any, value: str, name: str) -> Any:
    try:
        return cls(value)
    except ValueError as exc:
        raise ApiError(
            400, "invalid_" + name, f"{name} must be one of {[e.value for e in cls]}", pointer=f"/{name}"
        ) from exc


@router.get("/violations")
async def list_violations(
    actor: CurrentActor, uow: Uow, status: str | None = None, severity: str | None = None, category: str | None = None, policy: str | None = None,
    person: str | None = None, project: str | None = None, target_id: str | None = None, limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    stmt = visible(
        select(m.Violation, m.Policy.key, m.WorkspaceMember.handle)
        .join(m.Policy, m.Policy.id == m.Violation.policy_id)
        .outerjoin(
            m.WorkspaceMember,
            (m.WorkspaceMember.person_id == m.Violation.person_id)
            & (m.WorkspaceMember.workspace_id == actor.ws.id),
        )
        .where(m.Violation.workspace_id == actor.ws.id, m.Violation.deleted_at.is_(None)),
        actor.acl,
        m.Violation,
    )
    if status:
        stmt = stmt.where(
            m.Violation.status.in_([_enum(ViolationStatus, x, "status") for x in status.split(",")])
        )
    if severity:
        stmt = stmt.where(m.Violation.severity == _enum(Severity, severity, "severity"))
    if category:
        stmt = stmt.where(m.Violation.category == _enum(PolicyCategory, category, "category"))
    if policy:
        stmt = stmt.where(m.Policy.key == policy)
    if person:
        stmt = stmt.where(m.WorkspaceMember.handle == (person if person.startswith("@") else "@" + person))
    if project:
        stmt = stmt.where(
            m.Violation.project_id
            == await uow.session.scalar(
                select(m.Project.id).where(
                    m.Project.workspace_id == actor.ws.id, m.Project.key == project.upper()
                )
            )
        )
    if target_id:
        stmt = stmt.where(m.Violation.target_id == target_id)
    try:
        offset = max(0, int(cursor or 0))
    except ValueError as exc:
        raise ApiError(
            400, "invalid_cursor", "cursor must be a number from a previous response", pointer="/cursor"
        ) from exc
    rows = (
        await uow.session.execute(
            stmt.order_by(m.Violation.detected_at.desc(), m.Violation.id)
            .limit(min(limit, 500) + 1)
            .offset(offset)
        )
    ).all()
    size = min(limit, 500)
    names = await Names.load(
        uow.session,
        actor.ws.id,
        [(v.target_type, v.target_id) for v, _, _ in rows[:size]]
        + [("team", v.team_id) for v, _, _ in rows[:size]]
        + [("project", v.project_id) for v, _, _ in rows[:size]],
    )
    return {
        "data": [
            {
                **service.violation_view(v, pk),
                "person": h,
                "target": names.label(v.target_type, v.target_id),
                "team": names.label("team", v.team_id),
                "project": names.label("project", v.project_id),
            }
            for v, pk, h in rows[:size]
        ],
        "next_cursor": str(offset + size) if len(rows) > size else None,
    }


async def _transition(
    uow: Uow,
    actor: Actor,
    vid: str,
    new: ViolationStatus,
    *,
    perm: str | None,
    note: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    v, pk = await _violation(actor, uow, vid)
    subject = v.person_id == actor.person.id
    if perm and not (actor.acl.can(perm) or (new == ViolationStatus.ACKNOWLEDGED and subject)):
        raise ApiError(403, "forbidden", f"missing permission {perm!r}", details={"permission": perm})
    if v.status not in OPEN or (
        new == ViolationStatus.ACKNOWLEDGED and v.status == ViolationStatus.ACKNOWLEDGED
    ):
        raise ApiError(409, "invalid_transition", f"a {v.status.value} violation cannot become {new.value}")
    before = v.status
    v.status = new
    v.updated_at = (
        utcnow()
    )  # waive / false-positive suppression compares this with when the condition began to hold
    if new == ViolationStatus.ACKNOWLEDGED:
        pass
    elif new == ViolationStatus.WAIVED:
        v.waived_by, v.waive_reason = actor.person.id, reason
    else:
        v.resolved_at, v.resolved_by, v.resolution_note = utcnow(), actor.person.id, note
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=v.target_type, entity_id=v.target_id, event_type=f"violation.{new.value}",
        payload={"before": {"status": before.value}, "after": {"violation_id": v.id, "status": new.value, "policy": pk, "note": note or reason}},
    )  # fmt: skip
    await uow.commit()
    return service.violation_view(v, pk)


@router.post("/violations/{vid}:acknowledge")
async def acknowledge(vid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.ACKNOWLEDGED, perm="violation.resolve", note=None
    )


@router.post("/violations/{vid}:resolve")
async def resolve(vid: str, body: Note, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.RESOLVED, perm="violation.resolve", note=body.note
    )


@router.post("/violations/{vid}:waive")
async def waive(vid: str, body: Reason, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.WAIVED, perm="violation.waive", note=None, reason=body.reason
    )


@router.post("/violations/{vid}:false-positive")
async def false_positive(vid: str, body: Reason, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.FALSE_POSITIVE, perm="violation.resolve", note=body.reason
    )


# ---------------------------------------------------------------- habits
def habit_view(h: m.Habit, handle: str | None, actor: Actor | None = None) -> dict[str, Any]:
    d = row_dict(h)
    if actor is not None and (h.subject_id == actor.person.id or not actor.acl.can("habit.coach")):
        d.pop("coaching_note", None)  # ADR-0014: coaching notes are manager-only; the subject sees status
    d["subject"] = handle or h.subject_id
    return d


@router.get("/habit-definitions")
async def habit_definitions(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.HabitDefinition)
            .where((m.HabitDefinition.workspace_id == actor.ws.id) | m.HabitDefinition.workspace_id.is_(None))
            .order_by(m.HabitDefinition.key)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.get("/habits")
async def list_habits(
    actor: CurrentActor,
    uow: Uow,
    subject: str | None = None,
    key: str | None = None,
    status: str | None = None,
    window: str | None = None,
) -> dict[str, Any]:
    stmt = visible(
        select(m.Habit).where(m.Habit.workspace_id == actor.ws.id, m.Habit.deleted_at.is_(None)),
        actor.acl,
        m.Habit,
    )
    if key:
        stmt = stmt.where(m.Habit.key == key)
    if status:
        stmt = stmt.where(m.Habit.status == _enum(HabitStatus, status, "status"))
    if window:
        if not re.fullmatch(r"\d{1,4}[dw]", window):
            raise ApiError(400, "invalid_window", "window looks like 30d or 12w", pointer="/window")
        stmt = stmt.where(m.Habit.window == window)
    handles = {
        pid: h
        for pid, h in (
            await uow.session.execute(
                select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id
                )
            )
        ).all()
    }
    if subject:
        want = subject if subject.startswith("@") else "@" + subject
        stmt = stmt.where(m.Habit.subject_id.in_([p for p, h in handles.items() if h == want]))
    rows = (await uow.session.scalars(stmt.order_by(m.Habit.key, m.Habit.subject_id))).all()
    names = await Names.load(uow.session, actor.ws.id, [(h.subject_type, h.subject_id) for h in rows])
    return {
        "data": [
            habit_view(h, handles.get(h.subject_id) or names.label(h.subject_type, h.subject_id), actor)
            for h in rows
        ],
        "next_cursor": None,
    }


@router.get("/habits/{hid}/series")
async def habit_series(
    hid: str, actor: CurrentActor, uow: Uow, points: int = Query(8, ge=2, le=26)
) -> dict[str, Any]:
    """Sparkline data. Visibility is the habit's own (a wellbeing habit is as hidden here as in the list)."""
    row = await uow.session.scalar(
        visible(
            select(m.Habit).where(
                m.Habit.id == hid, m.Habit.workspace_id == actor.ws.id, m.Habit.deleted_at.is_(None)
            ),
            actor.acl,
            m.Habit,
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "habit not found")
    return {"data": await habits.series(uow.session, actor.ws.id, row, points=points), "next_cursor": None}


class HabitPatch(BaseModel):
    status: HabitStatus | None = None
    coaching_note: str | None = None


@router.patch("/habits/{hid}")
async def patch_habit(hid: str, body: HabitPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    h = await uow.session.scalar(
        visible(
            select(m.Habit).where(m.Habit.id == hid, m.Habit.workspace_id == actor.ws.id), actor.acl, m.Habit
        )
    )
    if h is None:
        raise ApiError(404, "not_found", "habit not found")
    check(actor, "habit.coach")
    before = h.status
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(h, k, v)
    _ev(
        uow,
        actor,
        h.subject_type,
        h.subject_id,
        "habit.status_changed",
        habit=h.key,
        before=before.value,
        status=h.status.value,
        note=bool(data.get("coaching_note")),
    )
    await uow.commit()
    return habit_view(h, None, actor)


# ---------------------------------------------------------------- metrics, notifications, operations
@router.get("/metric-snapshots")
async def metric_snapshots(
    actor: CurrentActor, uow: Uow, scope_type: ScopeType, scope_id: str, from_: Annotated[date | None, Query(alias="from")] = None, to: date | None = None, scheme: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    sid = scope_id
    aggregate = scope_type in (ScopeType.WORKSPACE, ScopeType.TEAM, ScopeType.PROCESS)
    if aggregate and not (actor.acl.is_admin or actor.acl.manager_all or actor.acl.can("habit.view_team")):
        raise ApiError(
            403,
            "forbidden",
            "missing permission 'habit.view_team'",
            details={"permission": "habit.view_team"},
        )
    if scope_type == ScopeType.WORKSPACE:
        sid = actor.ws.id
    elif scope_type == ScopeType.PROJECT:
        p = await uow.session.scalar(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == actor.ws.id,
                    (m.Project.key == scope_id.upper()) | (m.Project.id == scope_id),
                ),
                actor.acl,
                m.Project,
            )
        )
        if p is None:
            raise ApiError(404, "not_found", "project not found")
        sid = p.id
    elif scope_type == ScopeType.PERSON:
        handle = scope_id if scope_id.startswith("@") else "@" + scope_id
        row = await uow.session.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == actor.ws.id,
                (m.WorkspaceMember.handle == handle) | (m.WorkspaceMember.person_id == scope_id),
            )
        )
        if row is None:
            raise ApiError(404, "not_found", "person not found")
        if not (
            row.person_id == actor.person.id
            or actor.acl.is_admin
            or actor.acl.manager_all
            or row.person_id in actor.acl.managed_ids
            or actor.acl.can("habit.view_team")
        ):
            raise ApiError(404, "not_found", "person not found")
        sid = row.person_id
    elif scope_type == ScopeType.TEAM:
        t = await uow.session.scalar(
            select(m.Team).where(
                m.Team.workspace_id == actor.ws.id, (m.Team.slug == scope_id) | (m.Team.id == scope_id)
            )
        )
        if t is None:
            raise ApiError(404, "not_found", "team not found")
        sid = t.id
    elif scope_type == ScopeType.PROCESS:
        sid = scope_id if ":" in scope_id else f"{actor.ws.id}:{scope_id}"
    sch = None
    if scheme:
        sch = await uow.session.scalar(
            select(m.WeightScheme.id).where(
                m.WeightScheme.workspace_id == actor.ws.id,
                (m.WeightScheme.id == scheme) | (m.WeightScheme.name == scheme),
            )
        )
        if sch is None:
            raise ApiError(400, "unknown_scheme", f"unknown weight scheme {scheme!r}", pointer="/scheme")
    stmt = select(m.MetricSnapshot).where(
        m.MetricSnapshot.scope_type == scope_type,
        m.MetricSnapshot.scope_id == sid,
        m.MetricSnapshot.weight_scheme_id.is_(None)
        if sch is None
        else m.MetricSnapshot.weight_scheme_id == sch,
    )
    if from_:
        stmt = stmt.where(m.MetricSnapshot.date >= from_)
    if to:
        stmt = stmt.where(m.MetricSnapshot.date <= to)
    rows = (await uow.session.scalars(stmt.order_by(m.MetricSnapshot.date))).all()
    # wellbeing habit scores belong to the person and their line managers only (object model §13.3)
    own_view = scope_type == ScopeType.PERSON and (sid == actor.person.id or sid in actor.acl.managed_ids)
    wellbeing = (
        set()
        if own_view
        else set(
            (
                await uow.session.scalars(
                    select(m.HabitDefinition.key).where(
                        m.HabitDefinition.category == PolicyCategory.WELLBEING
                    )
                )
            ).all()
        )
    )

    def clean(metrics: dict[str, Any]) -> dict[str, Any]:
        hs = metrics.get("habit_scores")
        if wellbeing and isinstance(hs, dict):
            metrics = {**metrics, "habit_scores": {k: v for k, v in hs.items() if k not in wellbeing}}
        return metrics

    return {
        "data": [
            {
                "date": r.date,
                "scope_type": r.scope_type.value,
                "scope_id": r.scope_id,
                "weight_scheme_id": r.weight_scheme_id,
                "metrics": clean(r.metrics),
            }
            for r in rows
        ],
        "next_cursor": None,
    }


@router.get("/notifications/mine")
async def my_notifications(actor: CurrentActor, uow: Uow, unread: bool = False) -> dict[str, Any]:
    stmt = select(m.Notification).where(
        m.Notification.workspace_id == actor.ws.id, m.Notification.person_id == actor.person.id
    )
    if unread:
        stmt = stmt.where(m.Notification.read_at.is_(None))
    rows = (await uow.session.scalars(stmt.order_by(m.Notification.created_at.desc()).limit(200))).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/notifications/{nid}:read")
async def read_notification(nid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    n = await uow.session.scalar(
        select(m.Notification).where(m.Notification.id == nid, m.Notification.person_id == actor.person.id)
    )
    if n is None:
        raise ApiError(404, "not_found", "notification not found")
    n.read_at = utcnow()
    _ev(uow, actor, TargetType.PERSON, actor.person.id, "notification.read", notification=nid)
    await uow.commit()
    return row_dict(n)


@router.post("/governance:recompute")
async def recompute(
    request: Request, actor: CurrentActor, uow: Uow, at: datetime | None = None
) -> dict[str, Any]:
    """Admin: sweep every active scheduled policy, then habits, metric snapshots and key results -- what the schedule does overnight."""
    check(actor, "policy.admin")
    maker = request.app.state.session_factory  # the runner opens its own units of work
    swept = await runner.run_workspace(maker, actor.ws.id, now=at)
    out = await runner.nightly(maker, actor.ws.id, now=at)
    return {"policies_swept": swept, **out}


_ = (EventSource, timedelta)
