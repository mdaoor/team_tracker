"""Insights and the daily-log LLM summary (Phase 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import DailyLogStatus, InsightStatus, ScopeType, TargetType

from tt_server.acl.visibility import visible
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.params import enum_param
from tt_server.db import models as m
from tt_server.domain import deckdata, insights
from tt_server.domain.capture import dailylog
from tt_server.domain.common import emit, row_dict
from tt_server.integrations.llm import GatewayBudgetExceeded, GatewayUnavailable

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["presentation"])


# ---------------------------------------------------------------- insights
class InsightGen(BaseModel):
    scope: str = Field(description="project key, or `all` for every visible project")


@router.post("/insights:generate", status_code=201, dependencies=[needs("view.save")])
async def generate(body: InsightGen, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    keys = [body.scope]
    if body.scope == "all":
        keys = [
            p.key
            for p in (
                await uow.session.scalars(
                    select(m.Project).where(
                        m.Project.workspace_id == actor.ws.id, m.Project.deleted_at.is_(None)
                    )
                )
            ).all()
        ]
    out = []
    for k in keys:
        proj, facts = await insights.facts_for_project(
            deckdata.qctx(uow.session, actor.ws, actor.person, actor.member, actor.acl), actor.ws, k
        )
        row, source = await insights.generate_project_insight(
            uow.session, actor.ws, proj, facts, request.app.state.llm, period=utcnow().date().isoformat()
        )
        emit(
            uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PROJECT, entity_id=proj.id,
            event_type="insight.generated", payload={"after": {"insight_id": row.id, "source": source}},
        )  # fmt: skip
        out.append({**row_dict(row), "project": proj.key, "source": source})
    await uow.commit()
    return {"data": out, "next_cursor": None}


@router.get("/insights")
async def list_insights(
    actor: CurrentActor, uow: Uow, project: str | None = None, status: str | None = None
) -> dict[str, Any]:

    projs = (
        await uow.session.scalars(
            visible(
                select(m.Project).where(
                    m.Project.workspace_id == actor.ws.id, m.Project.deleted_at.is_(None)
                ),
                actor.acl,
                m.Project,
            )
        )
    ).all()
    by_id = {p.id: p.key for p in projs if project is None or p.key == project.upper()}
    stmt = select(m.Insight).where(
        m.Insight.scope_type == ScopeType.PROJECT,
        m.Insight.scope_id.in_(list(by_id)),
        m.Insight.deleted_at.is_(None),
    )
    if status:
        stmt = stmt.where(m.Insight.status == enum_param(InsightStatus, status, "status"))
    rows = (await uow.session.scalars(stmt.order_by(m.Insight.generated_at.desc()))).all()
    return {"data": [{**row_dict(r), "project": by_id[r.scope_id]} for r in rows], "next_cursor": None}


class InsightPatch(BaseModel):
    status: InsightStatus


@router.patch("/insights/{iid}")
async def patch_insight(iid: str, body: InsightPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:

    row = await uow.session.get(m.Insight, iid)
    proj = (
        await uow.session.scalar(
            visible(
                select(m.Project).where(m.Project.id == row.scope_id, m.Project.workspace_id == actor.ws.id),
                actor.acl,
                m.Project,
            )
        )
        if row is not None
        else None
    )
    if row is None or proj is None:
        raise ApiError(404, "not_found", "insight not found")
    before = row.status
    row.status = body.status
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PROJECT, entity_id=proj.id,
        event_type="insight.status_changed", payload={"before": {"status": before.value}, "after": {"status": body.status.value, "insight_id": row.id}},
    )  # fmt: skip
    await uow.commit()
    return row_dict(row)


# ---------------------------------------------------------------- daily-log LLM summary (deferred from Phase 1)
@router.post("/daily-logs/{log_id}/summarise")
async def summarise_daily_log(log_id: str, request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await dailylog.get_for(uow, actor.ws.id, log_id)
    if row.person_id != actor.person.id:
        raise ApiError(404, "not_found", "daily log not found")
    if row.status != DailyLogStatus.DRAFT:
        raise ApiError(409, "not_draft", "only a draft daily log can be re-summarised")
    gw = request.app.state.llm
    if gw is None:
        raise ApiError(409, "not_configured", "the LLM gateway is not configured")
    items = (
        await uow.session.execute(
            select(m.WorkItem.key, m.WorkItem.title).where(m.WorkItem.id.in_(row.item_ids or []))
        )
    ).all()
    facts = {
        "date": row.date.isoformat(),
        "minutes_total": row.minutes_total,
        "blockers": row.blockers,
        "items": [{"key": k, "title": t} for k, t in items],
    }

    try:
        ans = await gw.summarise_daily_log(actor.ws.slug, facts)
    except (GatewayUnavailable, GatewayBudgetExceeded) as exc:
        raise ApiError(503, "gateway_unavailable", str(exc)) from exc
    out = ans.output or {}
    allowed = {k for k, _ in items}
    if not out.get("body") or not set(out.get("supporting_ids", [])) <= allowed:
        return {
            **dailylog.to_dict(row),
            "source": "deterministic",
            "warnings": ["summary not grounded in the day's items"],
        }
    row.summary = str(out["body"]).strip()
    emit(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.DAILY_LOG, entity_id=row.id,
        event_type="dailylog.summarised", payload={"after": {"date": row.date.isoformat(), "run_id": ans.run_id}},
    )  # fmt: skip
    await uow.commit()
    return {**dailylog.to_dict(row), "source": "gateway", "warnings": ans.warnings}
