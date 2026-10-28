"""Insights (Phase 7 §7.3): facts -> gateway summary -> grounding check -> deterministic fallback.

The gateway already drops ids it does not know; this module re-checks against the facts it sent, because the core server is the
trust boundary: a summary that cites something outside its facts is thrown away and the deterministic template is used.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import InsightKind, InsightStatus, ScopeType

from tt_server.acl.context import load_context
from tt_server.db import models as m
from tt_server.db.uow import commit_derived
from tt_server.domain.errors import NotFound
from tt_server.integrations.llm import GatewayBudgetExceeded, GatewayUnavailable, LLMGatewayClient


def project_facts(
    project: dict[str, Any],
    items: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    stage_expected: dict[str, int],
    today: date,
) -> dict[str, Any]:
    """Counts, stuck items, top risk, next milestone and coverage -- every number the summary may mention."""
    mine = [i for i in items if i["project"] == project["key"]]
    done = [i for i in mine if i["state"] == "done"]
    stuck = [
        i
        for i in mine
        if i["state"] != "done"
        and stage_expected.get(i["stage"] or "", 0)
        and (i["stage_age_days"] or 0) > stage_expected[i["stage"]]
    ]
    by_stage = Counter(i["stage"] for i in stuck)
    upcoming = sorted((x for x in milestones if x["project"] == project["key"]), key=lambda x: x["due"])
    nxt = next((x for x in upcoming if x["due"] >= today.isoformat()), upcoming[0] if upcoming else None)
    prisks = sorted(
        (r for r in risks if r["project"] == project["key"] and r["status"] != "closed"),
        key=lambda r: r["p"] * r["i"],
        reverse=True,
    )
    open_items = [i for i in mine if i["state"] != "done"]
    return {
        "project": {"key": project["key"], "name": project["name"]},
        "counts": {"total": len(mine), "done": len(done), "stuck": len(stuck)},
        "stuck_stage": by_stage.most_common(1)[0][0] if by_stage else None,
        "stuck_items": [
            {"key": i["key"], "stage": i["stage"], "days": i["stage_age_days"]} for i in stuck[:8]
        ],
        "next_milestone": nxt,
        "top_risk": {"key": prisks[0]["key"], "score": prisks[0]["p"] * prisks[0]["i"]} if prisks else None,
        "estimate_coverage_pct": round(
            100 * sum(1 for i in open_items if i["estimate_h"] is not None) / len(open_items)
        )
        if open_items
        else None,
        "evidence_coverage_pct": round(
            100 * sum(1 for i in open_items if i["evidence_count"] > 0) / len(open_items)
        )
        if open_items
        else None,
    }


def fallback_summary(facts: dict[str, Any]) -> str:
    """ "{done}/{total} items done ({pct} %). {stuck} items over expected stage time{, mostly in <stage>}. Next milestone ..."""
    c = facts["counts"]
    pct = round(100 * c["done"] / c["total"]) if c["total"] else 0
    parts = [f"{c['done']}/{c['total']} items done ({pct} %)."]
    if c["stuck"]:
        parts.append(
            f"{c['stuck']} items over expected stage time"
            + (f", mostly in {facts['stuck_stage']}." if facts["stuck_stage"] else ".")
        )
    else:
        parts.append("No items over expected stage time.")
    if facts["next_milestone"]:
        parts.append(f"Next milestone {facts['next_milestone']['name']} on {facts['next_milestone']['due']}.")
    if facts["top_risk"]:
        parts.append(f"Top risk {facts['top_risk']['key']} ({facts['top_risk']['score']}).")
    return " ".join(parts)


def fact_ids(facts: dict[str, Any]) -> set[str]:
    """Collect the item keys present in a fact sheet; an insight may cite only these."""
    ids = {facts["project"]["key"]}
    ids |= {i["key"] for i in facts.get("stuck_items", [])}
    if facts.get("top_risk"):
        ids.add(facts["top_risk"]["key"])
    return ids


def grounded(supporting: list[str], facts: dict[str, Any]) -> bool:
    """True when every supporting key an insight cites exists in the facts it was generated from."""
    return set(supporting) <= fact_ids(facts)


async def generate_project_insight(
    session: AsyncSession,
    ws: m.Workspace,
    project: m.Project,
    facts: dict[str, Any],
    gateway: LLMGatewayClient | None,
    *,
    period: str,
) -> tuple[m.Insight, str]:
    """Create an `Insight{summary}` for `project`. Returns the row and its source: `gateway` or `fallback`."""
    body, supporting, source = fallback_summary(facts), sorted(fact_ids(facts)), "fallback"
    if gateway is not None:
        try:
            ans = await gateway.summarise(ws.slug, "project", facts)
            out = ans.output or {}
            sup = [str(x) for x in out.get("supporting_ids", [])]
            if out.get("body") and grounded(sup, facts):
                body, supporting, source = str(out["body"]).strip(), sup, "gateway"
        except (GatewayUnavailable, GatewayBudgetExceeded):
            pass
    row = m.Insight(
        id=new_id(),
        scope_type=ScopeType.PROJECT,
        scope_id=project.id,
        period=period,
        kind=InsightKind.SUMMARY,
        body=body,
        supporting_ids=supporting,
        status=InsightStatus.NEW,
    )
    session.add(row)
    await session.flush()
    return row, source


async def latest_summaries(session: AsyncSession, project_ids: list[str]) -> dict[str, str]:
    """Latest non-dismissed summary body per project id."""
    if not project_ids:
        return {}
    rows = (
        await session.execute(
            select(m.Insight.scope_id, m.Insight.body)
            .where(
                m.Insight.scope_type == ScopeType.PROJECT,
                m.Insight.scope_id.in_(project_ids),
                m.Insight.kind == InsightKind.SUMMARY,
                m.Insight.status != InsightStatus.DISMISSED,
                m.Insight.deleted_at.is_(None),
            )
            .order_by(m.Insight.generated_at)
        )
    ).all()
    return {sid: body for sid, body in rows}  # later rows overwrite earlier ones


async def facts_for_project(qc: Any, ws: m.Workspace, project_key: str) -> tuple[m.Project, dict[str, Any]]:
    """Facts for one project as the viewer in `qc` sees it (so a summary never cites what its requester may not read)."""
    from tt_server.domain import deckdata

    snap = await deckdata.get_snapshot(
        qc,
        ws,
        scope=f"project:{project_key}",
        days=14,
        scheme=None,
        viewer=qc.handle,
        fresh=True,
        cache=False,
    )
    proj = await qc.session.scalar(
        select(m.Project).where(m.Project.workspace_id == ws.id, m.Project.key == project_key.upper())
    )
    pd = next((p for p in snap["projects"] if p["key"] == project_key.upper()), None)
    if proj is None or pd is None:
        raise NotFound("not_found", f"unknown project {project_key!r}", pointer="/scope")
    return proj, project_facts(
        pd, snap["items"], snap["milestones"], snap["risks"], snap["stage_expected_days"], qc.today
    )


async def nightly(factory: Any, gateway: LLMGatewayClient, ws_id: str) -> int:
    """Generate a gateway summary for every project in the workspace, as its first admin. Without a gateway nothing is stored: the
    snapshot computes the deterministic text live."""
    from tt_server.domain import deckdata

    written = 0
    async with factory() as s:
        ws = await s.get(m.Workspace, ws_id)
        if ws is None:
            return 0
        members = (
            await s.scalars(
                select(m.WorkspaceMember)
                .where(m.WorkspaceMember.workspace_id == ws_id)
                .order_by(m.WorkspaceMember.created_at)
            )
        ).all()
        admin = None
        for mem in members:
            ctx = await load_context(s, ws_id, mem.person_id)
            if ctx.is_admin:
                admin, acl = mem, ctx
                break
        if admin is None:
            return 0
        person = await s.get(m.Person, admin.person_id)
        assert person is not None  # noqa: S101
        keys = (
            await s.scalars(
                select(m.Project.key).where(
                    m.Project.workspace_id == ws_id,
                    m.Project.deleted_at.is_(None),
                    m.Project.kind == "standard",
                )
            )
        ).all()
        qc = deckdata.qctx(s, ws, person, admin, acl)
        for k in keys:
            proj, facts = await facts_for_project(qc, ws, k)
            row, source = await generate_project_insight(
                s, ws, proj, facts, gateway, period=qc.today.isoformat()
            )
            if source == "gateway":
                written += 1
            else:
                await s.delete(row)  # a deterministic text is not worth a row; the snapshot recomputes it
        await commit_derived(s)
    return written
