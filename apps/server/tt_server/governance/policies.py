"""Policy and policy-template lifecycle behind `/policies` and `/policy-templates` (Phase 8).

Orchestration only: compile and validate, persist, audit. Permission checks stay in the routers. Every function
leaves the commit to the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import tt_cel
from sqlalchemy import or_, select, update
from tt_schema.base import new_id, utcnow
from tt_schema.enums import PolicyCategory, PolicyTrigger, Severity, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.errors import Conflict, NotFound
from tt_server.governance import apply as applymod
from tt_server.governance import engine, service
from tt_server.governance.policy_templates import _audit as _audit
from tt_server.governance.policy_templates import copy_template as copy_template
from tt_server.governance.policy_templates import create_template as create_template
from tt_server.governance.policy_templates import list_templates as list_templates
from tt_server.governance.policy_templates import publish_template as publish_template

# ---------------------------------------------------------------- policies


async def get_policy(uow: UnitOfWork, ws_id: str, pid: str) -> m.Policy:
    """A live policy of the workspace by id or key; 404 when absent."""
    p = await uow.session.scalar(
        select(m.Policy).where(
            (m.Policy.id == pid) | (m.Policy.key == pid),
            m.Policy.workspace_id == ws_id,
            m.Policy.deleted_at.is_(None),
        )
    )
    if p is None:
        raise NotFound("not_found", "policy not found")
    return p


async def last_test_run(uow: UnitOfWork, policy_id: str) -> m.PolicyTestRun | None:
    """The policy's most recent dry run."""
    return await uow.session.scalar(
        select(m.PolicyTestRun)
        .where(m.PolicyTestRun.policy_id == policy_id)
        .order_by(m.PolicyTestRun.ran_at.desc())
    )


async def list_policies(uow: UnitOfWork, ws_id: str) -> list[tuple[m.Policy, m.PolicyTestRun | None]]:
    """Live policies by key, each with its latest dry run."""
    rows = (
        await uow.session.scalars(
            select(m.Policy)
            .where(m.Policy.workspace_id == ws_id, m.Policy.deleted_at.is_(None))
            .order_by(m.Policy.key)
        )
    ).all()
    tests = {
        r.policy_id: r
        for r in (await uow.session.scalars(select(m.PolicyTestRun).order_by(m.PolicyTestRun.ran_at))).all()
    }
    return [(p, tests.get(p.id)) for p in rows]


async def create_policy(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, data: dict[str, Any], *, raw_allowed: bool
) -> m.Policy:
    """Create an inactive policy from a template or a raw condition; explicit fields override the template's."""
    s = uow.session
    comp = await service.compile_condition(s, ws.id, data, raw_allowed=raw_allowed)
    comp.pop("_template", None)
    over = {
        k: data[k]
        for k in ("target", "trigger", "event_types", "schedule", "candidate_filter", "severity", "category")
        if data[k] is not None
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
        {
            **f,
            "notify": data["notify"],
            "auto_action": data["auto_action"],
            "grace_period": data["grace_period"],
        }
    )
    if await s.scalar(
        select(m.Policy.id).where(
            m.Policy.workspace_id == ws.id, m.Policy.key == data["key"], m.Policy.deleted_at.is_(None)
        )
    ):
        raise Conflict("conflict", f"a policy with key {data['key']!r} exists", pointer="/key")
    sid = await service.resolve_scope(s, ws.id, data["scope_type"], data["scope_id"])
    p = m.Policy(
        id=new_id(),
        workspace_id=ws.id,
        key=data["key"],
        name=data["name"],
        description=data["description"],
        owner_id=actor_id,
        scope_type=data["scope_type"],
        scope_id=sid,
        notify=data["notify"],
        auto_action=data["auto_action"],
        grace_period=data["grace_period"],
        is_active=False,
        context_schema_version=tt_cel.VERSION,
        updated_at=utcnow(),
        **f,
    )
    s.add(p)
    await s.flush()
    _audit(
        uow,
        ws,
        actor_id,
        "policy.created",
        policy=p.key,
        template=data["template"],
        after_def=service.definition(p),
    )
    return p


async def patch_policy(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Policy, data: dict[str, Any], *, raw_allowed: bool
) -> m.Policy:
    """Edit a policy. Changing the condition deactivates it until it is dry-run again; every edit moves `updated_at`."""
    s = uow.session
    sent = set(data)
    data = dict(data)
    before_def = service.definition(p)
    if "params" in data or "condition_cel" in data:
        src = {
            "template": p.condition_template_id,
            "params": data.get("params", p.template_params or {}),
            "condition_cel": data.get("condition_cel"),
        }
        if p.condition_template_id and "condition_cel" not in data:
            comp = await service.compile_condition(s, ws.id, src, raw_allowed=raw_allowed)
            comp.pop("_template", None)
            p.condition_cel, p.template_params = comp["condition_cel"], comp["template_params"]
        else:
            comp = await service.compile_condition(
                s, ws.id, {"condition_cel": data["condition_cel"]}, raw_allowed=raw_allowed
            )
            p.condition_cel, p.condition_template_id, p.template_params = comp["condition_cel"], None, None
        data.pop("params", None)
        data.pop("condition_cel", None)
    if "scope_type" in data or "scope_id" in data:
        st = data.get("scope_type", p.scope_type)
        p.scope_type, p.scope_id = st, await service.resolve_scope(s, ws.id, st, data.get("scope_id"))
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
        False if ({"params", "condition_cel"} & sent) else p.is_active
    )  # re-test before it runs again
    p.updated_at = (
        utcnow()
    )  # the "last edit" mark a dry-run must postdate; toggling `is_active` does not move it
    _audit(
        uow,
        ws,
        actor_id,
        "policy.updated",
        policy=p.key,
        fields=sorted(sent),
        before_def=before_def,
        after_def=service.definition(p),
    )
    return p


async def policy_versions(uow: UnitOfWork, ws_id: str, p: m.Policy) -> list[dict[str, Any]]:
    """Version history, newest first, from the event log: who changed what, with the full definition at each point."""
    rows = (
        await uow.session.scalars(
            select(m.Event)
            .where(
                m.Event.workspace_id == ws_id,
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
                "event_id": e.id,
                "at": e.occurred_at,
                "actor_id": e.actor_id,
                "kind": e.event_type.split(".")[1],
                "changed": changed,
                "definition": new,
            }
        )
    return out


def delete_policy(uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Policy) -> None:
    """Soft-delete and deactivate a policy."""
    p.deleted_at, p.is_active = utcnow(), False
    _audit(uow, ws, actor_id, "policy.deleted", policy=p.key)


async def _event_candidates(
    uow: UnitOfWork, ws_id: str, p: m.Policy, window: str, now: datetime
) -> list[engine.Ref]:
    """Targets that had a matching event inside the window (on_event policies are judged on their current state)."""
    since = now - service.window_delta(window)
    conds = [
        m.Event.event_type == t if not t.endswith("*") else m.Event.event_type.like(t.rstrip("*") + "%")
        for t in (p.event_types or [])
    ]
    evs = (
        (
            await uow.session.scalars(
                select(m.Event)
                .where(m.Event.workspace_id == ws_id, m.Event.occurred_at >= since, or_(*conds))
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
    return list({r.id: r for r in only}.values())


async def dry_run(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Policy, window: str
) -> dict[str, Any]:
    """Dry run, nothing is written except the `PolicyTestRun`.

    `scheduled`: the current candidates. `on_event`: the targets that had a matching event inside the window,
    judged on their *current* state (an approximation; ADR-0013).
    """
    s = uow.session
    now = utcnow()
    only = (
        await _event_candidates(uow, ws.id, p, window, now) if p.trigger == PolicyTrigger.ON_EVENT else None
    )
    res = await engine.evaluate(s, ws, p, now=now, only=only)
    sample = [mt.target_id for mt in res.matches[:25]]
    s.add(
        m.PolicyTestRun(
            id=new_id(),
            policy_id=p.id,
            window=window,
            fired_count=len(res.matches),
            sample_target_ids=sample,
            ran_at=now,
        )
    )
    await s.flush()
    _audit(uow, ws, actor_id, "policy.tested", policy=p.key, fired=len(res.matches), window=window)
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


async def _set_active(uow: UnitOfWork, p: m.Policy, on: bool) -> None:
    """Flip `is_active` without moving `updated_at` (the "last edited" mark that a dry-run must postdate)."""
    await uow.session.execute(
        update(m.Policy).where(m.Policy.id == p.id).values(is_active=on, updated_at=m.Policy.updated_at)
    )
    await uow.session.refresh(p)


async def activate_policy(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Policy, *, force: bool
) -> m.PolicyTestRun | None:
    """Activate once a dry run postdates the last edit (`force` skips that; the router lets admins use it)."""
    last = await last_test_run(uow, p.id)
    if (last is None or last.ran_at < p.updated_at) and not force:
        raise Conflict(
            "test_required",
            "run a dry-run (`:test`) after the last edit before activating (admins may pass ?force=true)",
            details={"last_edit": p.updated_at.isoformat()},
        )
    await _set_active(uow, p, True)
    _audit(uow, ws, actor_id, "policy.activated", policy=p.key)
    return last


async def deactivate_policy(uow: UnitOfWork, ws: m.Workspace, actor_id: str, p: m.Policy) -> m.Policy:
    """Stop a policy from running."""
    await _set_active(uow, p, False)
    _audit(uow, ws, actor_id, "policy.deactivated", policy=p.key)
    return p


async def run_policy_now(
    uow: UnitOfWork, ws: m.Workspace, p: m.Policy, *, at: datetime | None
) -> dict[str, Any]:
    """Evaluate an active policy right now: opens and auto-resolves violations like a scheduled run."""
    if not p.is_active:
        raise Conflict("inactive", "activate the policy first")
    res = await engine.evaluate(uow.session, ws, p, now=at)
    out = await applymod.apply(uow, ws, p, res, now=at)
    return {
        "matched": len(res.matches),
        "opened": len(out.opened),
        "auto_resolved": len(out.resolved),
        "pending_grace": out.pending,
        "notified": out.notified,
        "actions": out.actions,
        "errors": res.errors[:10],
    }
