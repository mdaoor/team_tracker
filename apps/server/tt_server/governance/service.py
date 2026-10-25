"""Policy service: built-in catalogue, template resolution, save-time validation, dry-runs, lifecycle of violations (§8.3, §8.4)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import tt_cel
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_filter import FilterError
from tt_filter import parse as parse_filter
from tt_schema.base import new_id
from tt_schema.enums import (
    PolicyCategory,
    PolicyTrigger,
    ReportingKind,
    ScopeType,
    Severity,
    TargetType,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import SYSTEM_EMAIL, system_person
from tt_server.domain.errors import DomainError, Forbidden, Invalid, NotFound
from tt_server.governance import habits as habitmod

TARGETS = {
    TargetType.WORK_ITEM,
    TargetType.ACTIVITY,
    TargetType.PERSON,
    TargetType.PROJECT,
    TargetType.STAGE_PLACEMENT,
    TargetType.DAILY_LOG,
}
AUTO_ACTIONS = {"set", "move_stage", "create_item", "notify"}
NOTIFY_KEYS = {"roles", "reporting_line", "channel", "person"}
GRACE = re.compile(r"^\d+[mhdw]$")
META_KEYS = (
    "target",
    "trigger",
    "severity",
    "schedule",
    "event_types",
    "detail",
    "candidate_filter",
    "grouping",
    "example",
)


# ---------------------------------------------------------------------------------------------------- catalogue
def _row_params(t: tt_cel.Template) -> dict[str, Any]:
    return {
        "parameters": [p.__dict__ for p in t.params],
        **{k: getattr(t, k if k != "detail" else "detail") for k in ("target", "trigger", "severity", "schedule", "detail", "candidate_filter", "grouping", "example")},
        "event_types": list(t.event_types),
    }  # fmt: skip


def template_from_row(row: m.PolicyTemplate) -> tt_cel.Template:
    """A `tt_cel.Template` from a stored row (built-in or workspace)."""
    p: dict[str, Any] = row.params if isinstance(row.params, dict) else {"parameters": row.params or []}
    return tt_cel.Template(
        key=row.key, name=row.name, description=row.description or "", category=row.category.value, severity=p.get("severity", "warning"),
        target=p.get("target", "work_item"), trigger=p.get("trigger", "scheduled"), cel=row.cel_source, detail=p.get("detail", ""),
        params=tuple(tt_cel.Param(**x) for x in p.get("parameters", [])), schedule=p.get("schedule"), event_types=tuple(p.get("event_types", [])),
        candidate_filter=p.get("candidate_filter"), grouping=p.get("grouping"), example=p.get("example", ""),
    )  # fmt: skip


async def ensure_builtins(s: AsyncSession) -> None:
    """Idempotently load the YAML built-ins into `policy_templates` and `habit_definitions` (workspace_id NULL)."""
    have = {
        r.key: r
        for r in (
            await s.scalars(select(m.PolicyTemplate).where(m.PolicyTemplate.is_builtin.is_(True)))
        ).all()
    }
    for key, t in tt_cel.builtin_templates().items():
        row = have.get(key)
        vals = dict(
            name=t.name,
            category=PolicyCategory(t.category),
            cel_source=t.cel,
            params=_row_params(t),
            description=t.description,
        )
        if row is None:
            s.add(
                m.PolicyTemplate(
                    id=new_id(),
                    key=key,
                    is_builtin=True,
                    published_scope="builtin",
                    workspace_id=None,
                    **vals,
                )
            )
        else:
            for k, v in vals.items():
                if getattr(row, k) != v:
                    setattr(row, k, v)
    await habitmod.ensure_definitions(s)
    await s.flush()


async def find_template(s: AsyncSession, ws_id: str, ref: str) -> m.PolicyTemplate:
    rows = (
        await s.scalars(
            select(m.PolicyTemplate).where(
                (m.PolicyTemplate.id == ref) | (m.PolicyTemplate.key == ref),
                (m.PolicyTemplate.workspace_id == ws_id) | m.PolicyTemplate.workspace_id.is_(None),
                m.PolicyTemplate.deleted_at.is_(None),
            )
        )
    ).all()
    if not rows:
        raise NotFound("not_found", f"unknown policy template {ref!r}", pointer="/template")
    return sorted(rows, key=lambda r: r.workspace_id is None)[
        0
    ]  # a workspace copy shadows the built-in of the same key


def template_view(row: m.PolicyTemplate) -> dict[str, Any]:
    t = template_from_row(row)
    return {
        "id": row.id, "key": row.key, "name": row.name, "description": row.description, "category": row.category.value,
        "is_builtin": row.is_builtin, "published_scope": row.published_scope, "workspace_id": row.workspace_id,
        "target": t.target, "trigger": t.trigger, "severity": t.severity, "schedule": t.schedule, "event_types": list(t.event_types),
        "params": [p.__dict__ for p in t.params], "cel_source": row.cel_source, "detail": t.detail, "example": t.example,
        "candidate_filter": t.candidate_filter, "grouping": t.grouping,
    }  # fmt: skip


async def copy_template(
    s: AsyncSession, row: m.PolicyTemplate, to_ws: str, *, actor: str
) -> m.PolicyTemplate:
    """§14.2: a shared template is *copied* into the target workspace; later edits of the source never reach the copy."""
    copy = m.PolicyTemplate(
        id=new_id(), key=row.key, name=row.name, category=row.category, cel_source=row.cel_source, params=_deep(row.params),
        description=row.description, workspace_id=to_ws, is_builtin=False, published_scope="workspace", created_by=actor,
    )  # fmt: skip
    s.add(copy)
    await s.flush()
    return copy


def _deep(v: Any) -> Any:
    import copy

    return copy.deepcopy(v)


# ---------------------------------------------------------------------------------------------------- validation
def _cel_error(exc: tt_cel.CelError, pointer: str) -> DomainError:
    return Invalid("invalid_condition", exc.message, pointer=pointer, details=exc.as_dict())


def validate_fields(f: dict[str, Any]) -> None:
    if f["target"] not in TARGETS:
        raise Invalid(
            "invalid_target",
            f"target must be one of {sorted(t.value for t in TARGETS)}",
            pointer="/target",
        )
    if f["trigger"] == PolicyTrigger.ON_EVENT and not f.get("event_types"):
        raise Invalid("invalid_trigger", "on_event policies need event_types", pointer="/event_types")
    if f["trigger"] == PolicyTrigger.SCHEDULED:
        if not f.get("schedule") or not croniter.is_valid(f["schedule"]):
            raise Invalid(
                "invalid_schedule",
                "scheduled policies need a valid 5-field cron expression",
                pointer="/schedule",
            )
        if len(f["schedule"].split()) != 5:
            raise Invalid("invalid_schedule", "use a 5-field cron expression", pointer="/schedule")
    if f.get("grace_period") and not GRACE.match(f["grace_period"]):
        raise Invalid(
            "invalid_grace_period", "grace_period looks like 30m, 12h, 2d or 1w", pointer="/grace_period"
        )
    if f.get("candidate_filter"):
        try:
            parse_filter(f["candidate_filter"])
        except FilterError as exc:
            raise Invalid(
                "invalid_filter", exc.message, pointer="/candidate_filter" + (exc.pointer or "")
            ) from exc
    bad = set(f.get("notify") or {}) - NOTIFY_KEYS
    if bad:
        raise Invalid("invalid_notify", f"unknown notify keys {sorted(bad)}", pointer="/notify")
    n = f.get("notify") or {}
    for k in ("roles", "reporting_line"):
        v = n.get(k)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise Invalid("invalid_notify", f"notify.{k} must be a list of names", pointer=f"/notify/{k}")
    bad_kinds = [k for k in n.get("reporting_line") or [] if k not in {x.value for x in ReportingKind}]
    if bad_kinds:
        raise Invalid(
            "invalid_notify",
            f"unknown reporting line kinds {bad_kinds}",
            pointer="/notify/reporting_line",
        )
    bad = set(f.get("auto_action") or {}) - AUTO_ACTIONS
    if bad:
        raise Invalid(
            "invalid_auto_action",
            f"unknown auto_action keys {sorted(bad)}; allowed {sorted(AUTO_ACTIONS)}",
            pointer="/auto_action",
        )


async def resolve_scope(s: AsyncSession, ws_id: str, scope_type: ScopeType, ref: str | None) -> str | None:
    if scope_type in (ScopeType.WORKSPACE, ScopeType.NONE) or not ref:
        return None
    model: Any = {
        ScopeType.PROJECT: m.Project,
        ScopeType.TEAM: m.Team,
        ScopeType.PRODUCT: m.Product,
        ScopeType.PROCESS: m.Process,
    }.get(scope_type)
    if model is None:
        raise Invalid(
            "invalid_scope", f"policies cannot be scoped to a {scope_type.value}", pointer="/scope_type"
        )
    alt = model.slug if scope_type == ScopeType.TEAM else model.key
    row = await s.scalar(
        select(model.id).where(
            (model.id == ref) | (alt == ref) | (alt == ref.upper()), model.workspace_id == ws_id
        )
    )
    if row is None:
        raise NotFound("not_found", f"unknown {scope_type.value} {ref!r}", pointer="/scope_id")
    return str(row)


async def compile_condition(
    s: AsyncSession, ws_id: str, body: dict[str, Any], *, raw_allowed: bool
) -> dict[str, Any]:
    """Resolve `template + params` (tier 1) or raw `condition_cel` (tier 2, `policy.admin`) into the stored fields."""
    ref, params = body.get("template"), body.get("params") or {}
    if ref:
        row = await find_template(s, ws_id, ref)
        t = template_from_row(row)
        try:
            cel = tt_cel.render(t.cel, t.params, params)
            tt_cel.compile(cel)
            values = tt_cel.check_params(t.params, params)
            tt_cel.compile_detail(t.detail, t.params, params)
        except tt_cel.CelError as exc:
            raise _cel_error(
                exc,
                f"/{exc.field.replace('.', '/')}"
                if exc.field and exc.field.startswith("params")
                else "/params",
            ) from exc
        return {
            "condition_cel": cel, "condition_template_id": row.id, "template_params": values, "target": t.target, "trigger": t.trigger,
            "event_types": list(t.event_types) or None, "schedule": t.schedule, "severity": t.severity, "category": row.category.value,
            "candidate_filter": t.candidate_filter, "_template": t,
        }  # fmt: skip
    raw: str | None = body.get("condition_cel")
    if not raw:
        raise Invalid(
            "invalid_condition",
            "give either `template` (+ `params`) or `condition_cel`",
            pointer="/condition_cel",
        )
    if not raw_allowed:
        raise Forbidden(
            "forbidden", "missing permission 'policy.admin'", details={"permission": "policy.admin"}
        )
    try:
        tt_cel.compile(raw)
    except tt_cel.CelError as exc:
        raise _cel_error(exc, "/condition_cel") from exc
    return {"condition_cel": raw, "condition_template_id": None, "template_params": None}


# ---------------------------------------------------------------------------------------------------- views
def policy_view(p: m.Policy, last_test: m.PolicyTestRun | None = None) -> dict[str, Any]:
    return {
        "id": p.id, "key": p.key, "name": p.name, "description": p.description, "scope_type": p.scope_type.value, "scope_id": p.scope_id,
        "target": p.target.value, "trigger": p.trigger.value, "event_types": p.event_types, "schedule": p.schedule,
        "condition_cel": p.condition_cel, "condition_template_id": p.condition_template_id, "template_params": p.template_params,
        "candidate_filter": p.candidate_filter, "context_schema_version": p.context_schema_version, "severity": p.severity.value,
        "category": p.category.value, "notify": p.notify, "auto_action": p.auto_action, "grace_period": p.grace_period, "is_active": p.is_active,
        "owner_id": p.owner_id, "updated_at": p.updated_at,
        "last_test": {"fired_count": last_test.fired_count, "ran_at": last_test.ran_at, "window": last_test.window} if last_test else None,
    }  # fmt: skip


DEFINITION_FIELDS = (
    "name", "description", "scope_type", "scope_id", "condition_cel", "condition_template_id", "template_params", "candidate_filter",
    "severity", "notify", "auto_action", "grace_period", "schedule", "event_types",
)  # fmt: skip


def definition(p: m.Policy) -> dict[str, Any]:
    """The editable definition of a policy as plain JSON: what version history stores and what a restore writes back."""
    out: dict[str, Any] = {}
    for f in DEFINITION_FIELDS:
        v = getattr(p, f)
        out[f] = v.value if hasattr(v, "value") else v
    return out


def violation_view(v: m.Violation, policy_key: str | None = None) -> dict[str, Any]:
    return {
        "id": v.id, "policy_id": v.policy_id, "policy": policy_key, "target_type": v.target_type.value, "target_id": v.target_id,
        "person_id": v.person_id, "team_id": v.team_id, "project_id": v.project_id, "product_id": v.product_id, "severity": v.severity.value,
        "category": v.category.value, "detected_at": v.detected_at, "evidence": v.evidence, "status": v.status.value, "resolved_at": v.resolved_at,
        "resolved_by": v.resolved_by, "resolution_note": v.resolution_note, "waived_by": v.waived_by, "waive_reason": v.waive_reason,
    }  # fmt: skip


def window_delta(w: str) -> timedelta:
    n, unit = int(w[:-1]), w[-1]
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "w": timedelta(weeks=n)}[unit]


def due_now(policy: m.Policy, now: datetime) -> bool:
    """`schedule` is a 5-field cron; the dispatcher runs every minute and fires a policy whose expression matches this minute."""
    if policy.trigger != PolicyTrigger.SCHEDULED or not policy.schedule:
        return False
    return bool(croniter.match(policy.schedule, now.astimezone(UTC).replace(second=0, microsecond=0)))


__all__ = ["Severity", "ensure_builtins", "find_template"]


async def ensure_system(uow: UnitOfWork) -> m.Person:
    """The bot actor of governance writes. Creating it is bookkeeping, not a domain fact, so it is committed on its own (no event)."""
    s = uow.session
    existing = await s.scalar(select(m.Person).where(m.Person.email == SYSTEM_EMAIL))
    if existing is not None:
        return existing
    sysp = await system_person(s)
    await uow.commit_bookkeeping()
    return sysp
