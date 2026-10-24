"""Phase 9 API: what the governance screens need beyond Phase 8 (editor check, weight preview, manager digest)."""

from __future__ import annotations

from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any

import tt_cel
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import ScopeType

from tt_server.acl.visibility import redact_event, visible
from tt_server.api.authz import check
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import row_dict
from tt_server.api.routers.governance import policy_context
from tt_server.db import models as m
from tt_server.domain.overlay import weights
from tt_server.governance import service
from tt_server.integrations.llm import GatewayBudgetExceeded, GatewayUnavailable

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["governance"])


@router.get("/governance/context-schema")
async def context_schema(version: int = 1) -> dict[str, Any]:
    """Alias of `/meta/policy-context` under the name the editor uses; only v1 exists."""
    if version != tt_cel.VERSION:
        raise ApiError(404, "not_found", f"no context schema version {version}")
    return await policy_context()


class CheckIn(BaseModel):
    cel: str | None = None
    template: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    target: str | None = None


@router.post("/policies:check")
async def check_policy(body: CheckIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Type-check a raw expression, or render + check a template with parameters. Never raises for a bad expression: the editor
    wants positions, so errors come back in the body (`ok: false`)."""
    check(actor, "policy.write")
    errors: list[dict[str, Any]] = []
    cel = body.cel
    try:
        if body.template:
            await service.ensure_builtins(uow.session)
            row = await service.find_template(uow.session, actor.ws.id, body.template)
            t = service.template_from_row(row)
            cel = tt_cel.render(t.cel, t.params, body.params)
            tt_cel.check_params(t.params, body.params)
            tt_cel.compile_detail(t.detail, t.params, body.params)
        if cel is None:
            raise tt_cel.CelError("give `cel` or `template`")
        if not body.template and not actor.acl.can("policy.admin"):
            raise ApiError(
                403, "forbidden", "raw CEL needs 'policy.admin'", details={"permission": "policy.admin"}
            )
        tt_cel.compile(cel)
    except tt_cel.CelError as exc:
        errors.append(exc.as_dict())
    return {"ok": not errors, "cel": cel, "errors": errors}


class PreviewIn(BaseModel):
    formula: dict[str, Any]
    sample: int = Field(default=20, ge=1, le=100)


@router.post("/weight-schemes:preview")
async def preview_scheme(body: PreviewIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Weights the formula would give a sample of visible items next to what they carry now (the server's own `evaluate`)."""
    check(actor, "weight.set")
    weights.validate_formula(body.formula)
    s = uow.session
    items = (
        await s.scalars(
            visible(
                select(m.WorkItem)
                .join(m.Project, m.Project.id == m.WorkItem.project_id)
                .where(m.Project.workspace_id == actor.ws.id, m.WorkItem.deleted_at.is_(None)),
                actor.acl,
                m.WorkItem,
            )
            .order_by(m.WorkItem.key)
            .limit(body.sample)
        )
    ).all()
    out = []
    for it in items:
        w = weights.evaluate(body.formula, await weights.item_context(s, it))
        out.append({"key": it.key, "title": it.title, "current": float(it.weight), "preview": float(w)})
    cur = sum(Decimal(str(r["current"])) for r in out)
    new = sum(Decimal(str(r["preview"])) for r in out)
    return {"data": out, "total_current": float(cur), "total_preview": float(new)}


@router.get("/digest")
async def digest(actor: CurrentActor, uow: Uow, team: str | None = None, days: int = 14) -> dict[str, Any]:
    """The manager digest for a team (default: the whole workspace) and a window: daily logs, notable events, pinned notes, insights."""
    check(actor, "dailylog.review")
    s = uow.session
    days = max(1, min(days, 90))
    since = utcnow() - timedelta(days=days)
    people: set[str] | None = None
    team_row = None
    if team:
        team_row = await s.scalar(
            select(m.Team).where(
                m.Team.workspace_id == actor.ws.id, (m.Team.id == team) | (m.Team.slug == team)
            )
        )
        if team_row is None:
            raise ApiError(404, "not_found", "team not found")
        ids, frontier = {team_row.id}, {team_row.id}
        while frontier:
            kids = (
                set(
                    (
                        await s.scalars(
                            select(m.Team.id).where(
                                m.Team.workspace_id == actor.ws.id, m.Team.parent_team_id.in_(frontier)
                            )
                        )
                    ).all()
                )
                - ids
            )
            ids |= kids
            frontier = kids
        people = set(
            (
                await s.scalars(
                    select(m.Membership.person_id).where(
                        m.Membership.team_id.in_(ids), m.Membership.left_at.is_(None)
                    )
                )
            ).all()
        )
    handles = dict(
        (
            await s.execute(
                select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id
                )
            )
        ).all()
    )
    logs_q = visible(
        select(m.DailyLog).where(m.DailyLog.workspace_id == actor.ws.id, m.DailyLog.date >= since.date()),
        actor.acl,
        m.DailyLog,
    )
    if people is not None:
        logs_q = logs_q.where(m.DailyLog.person_id.in_(people))
    logs = (await s.scalars(logs_q.order_by(m.DailyLog.date.desc(), m.DailyLog.person_id).limit(200))).all()
    ev_q = visible(
        select(m.Event).where(
            m.Event.workspace_id == actor.ws.id,
            m.Event.occurred_at >= since,
            m.Event.event_type.in_(
                [
                    "violation.opened",
                    "stage.moved",
                    "item.completed",
                    "risk.added",
                    "milestone.slipped",
                    "insight.status_changed",
                ]
            ),
        ),
        actor.acl,
        m.Event,
    )
    if people is not None:
        ev_q = ev_q.where(m.Event.actor_id.in_(people))
    events = (await s.scalars(ev_q.order_by(m.Event.occurred_at.desc()).limit(50))).all()
    notes_q = select(m.ContextNote).where(
        m.ContextNote.workspace_id == actor.ws.id,
        m.ContextNote.deleted_at.is_(None),
        m.ContextNote.pinned.is_(True),
    )
    if team_row is not None:
        notes_q = notes_q.where(
            ((m.ContextNote.scope_type == ScopeType.TEAM) & (m.ContextNote.scope_id == team_row.id))
            | (m.ContextNote.scope_type == ScopeType.WORKSPACE)
        )
    notes = (await s.scalars(notes_q.order_by(m.ContextNote.created_at.desc()).limit(20))).all()
    proj_q = visible(
        select(m.Project).where(m.Project.workspace_id == actor.ws.id, m.Project.deleted_at.is_(None)),
        actor.acl,
        m.Project,
    )
    if team_row is not None:
        proj_q = proj_q.where(m.Project.team_id == team_row.id)
    projects = {p.id: p.key for p in (await s.scalars(proj_q)).all()}
    insights = (
        await s.scalars(
            select(m.Insight)
            .where(
                m.Insight.scope_type == ScopeType.PROJECT,
                m.Insight.scope_id.in_(list(projects)),
                m.Insight.generated_at >= since,
                m.Insight.deleted_at.is_(None),
            )
            .order_by(m.Insight.generated_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "team": team_row.slug if team_row else None,
        "days": days,
        "since": since.astimezone(UTC),
        "daily_logs": [{**row_dict(r), "person": handles.get(r.person_id, r.person_id)} for r in logs],
        "unreviewed": sum(1 for r in logs if r.status.value == "confirmed"),
        "events": [redact_event(actor.acl, row_dict(e)) for e in events],
        "notes": [row_dict(n) for n in notes],
        "insights": [{**row_dict(i), "project": projects[i.scope_id]} for i in insights],
    }


class AuthorIn(BaseModel):
    request: str = Field(min_length=3, max_length=2000)


@router.post("/policies:author")
async def author_policy(body: AuthorIn, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """ "Describe the rule": the gateway drafts a template choice or CEL; the server then checks the draft itself. The result is only ever
    a draft for the manager to review: `validated` is true only when this server compiled it."""
    check(actor, "policy.write")
    gw = request.app.state.llm
    if gw is None:
        raise ApiError(409, "not_configured", "the LLM gateway is not configured")
    try:
        ans = await gw.policy_author(actor.ws.slug, body.request)
    except (GatewayUnavailable, GatewayBudgetExceeded) as exc:
        raise ApiError(503, "llm_unavailable", str(exc) or "the LLM gateway is unavailable") from exc
    draft = dict(ans.output or {})
    errors: list[dict[str, Any]] = []
    try:
        if draft.get("mode") == "template" and draft.get("template"):
            await service.ensure_builtins(uow.session)
            t = service.template_from_row(
                await service.find_template(uow.session, actor.ws.id, draft["template"])
            )
            tt_cel.compile(tt_cel.render(t.cel, t.params, draft.get("params") or {}))
        elif draft.get("cel"):
            tt_cel.compile(draft["cel"])
        else:
            raise tt_cel.CelError("the draft has neither a template nor a CEL expression")
    except tt_cel.CelError as exc:
        errors.append(exc.as_dict())
    except ApiError as exc:
        errors.append({"message": exc.message})
    draft["validated"] = not errors
    return {"draft": draft, "errors": errors, "run_id": ans.run_id, "warnings": ans.warnings}
