"""Demo workspace seed: the `acme` workspace built from `tools/gen_snapshot.py`, through the services.

Structure (workspace, members, teams, products, projects, risks, items, placements, tags) goes through the
domain/overlay services; work-session rows (activities, time, evidence) go through the capture ingestion
pipeline with historical timestamps. Item *keys* are deterministic (`WEB-101` ...); ids are fresh UUIDs.
"""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import tempfile
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.client import ClientBatch
from tt_schema.enums import (
    EstimationUnit,
    EventSource,
    ItemTypeKey,
    MembershipRole,
    MilestoneKind,
    Priority,
    ProductLinkRole,
    ProjectStatus,
    ReportingKind,
    RiskCategory,
    RiskStatus,
    StateCategory,
    TargetType,
    TeamKind,
    ValueSource,
)
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.capture.ingest import ingest_batch
from tt_server.domain.common import emit
from tt_server.domain.items import service as items
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import risks as risk_svc
from tt_server.domain.overlay import weights
from tt_server.domain.overlay.products import create_product, link
from tt_server.domain.projects.service import create_project, item_type, pick_state, workflow_states
from tt_server.domain.tenancy import service as tenancy

AS_OF = datetime(2026, 10, 23, 18, 30, tzinfo=timezone(timedelta(hours=3)))
GEN_SNAPSHOT = Path(__file__).resolve().parents[1] / "gen_snapshot.py"

# Not in the snapshot: project owners (members of the team) and the reporting graph for the review queue.
PROJECT_OWNER = {"WEB": "@sara", "MOB": "@youssef", "DATA": "@nour", "GOV": "@mahmoud"}
TEAM_LEAD = {"platform": "@mahmoud", "squad-a": "@youssef", "squad-b": "@sara", "data": "@nour"}
PROJECT_SETTINGS = {"DATA": {"default_stage": "discovery"}}
PRIORITY = {
    "low": Priority.LOW,
    "medium": Priority.MEDIUM,
    "high": Priority.HIGH,
    "critical": Priority.CRITICAL,
}
METHODOLOGY = {"agile": "agile", "waterfall": "waterfall", "kanban": "kanban"}
RISK_STATUS = {s.value: s for s in RiskStatus}


def load_snapshot() -> dict[str, Any]:
    """Run tools/gen_snapshot.py (seeded RNG, deterministic) without leaving snapshot.json behind."""
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                snapshot: dict[str, Any] = runpy.run_path(str(GEN_SNAPSHOT))["snapshot"]
        finally:
            os.chdir(cwd)
    return snapshot


def _dt(d: str | date) -> datetime:
    day = date.fromisoformat(d) if isinstance(d, str) else d
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)


async def persist_demo(
    session_factory: async_sessionmaker[AsyncSession], snapshot: dict[str, Any] | None = None
) -> int:
    """Create the demo workspace; returns the number of work items created (items + epics)."""
    snap = snapshot or load_snapshot()
    ids: dict[str, Any] = {}

    # ---- A: workspace, people, teams, reporting lines ------------------------------------------------
    async with UnitOfWork(session_factory) as uow:
        boss = await tenancy.get_or_create_person(
            uow, display_name="Mahmoud A.", email="mahmoud@acme.example"
        )
        ws = await tenancy.create_workspace(
            uow, name="Acme", slug="acme", creator_id=boss.id, settings={"time_zone": "Asia/Riyadh"}
        )
        uow.context.update(actor_id=boss.id, correlation_id=new_id())
        person_id: dict[str, str] = {}
        for p in snap["people"]:
            person = (
                boss
                if p["handle"] == "@mahmoud"
                else await tenancy.get_or_create_person(
                    uow, display_name=p["name"], email=f"{p['handle'][1:]}@acme.example"
                )
            )
            await tenancy.add_member(uow, ws, person, handle=p["handle"], actor_id=boss.id)
            person_id[p["handle"]] = person.id
        team_id: dict[str, str] = {}
        for t in snap["teams"]:
            team = m.Team(
                id=new_id(),
                workspace_id=ws.id,
                name=t,
                slug=t,
                kind=TeamKind.ORG,
                lead_id=person_id[TEAM_LEAD[t]],
            )
            uow.session.add(team)
            team_id[t] = team.id
            emit(
                uow,
                workspace_id=ws.id,
                actor_id=boss.id,
                entity_type=TargetType.TEAM,
                entity_id=team.id,
                event_type="team.created",
                payload={"after": {"slug": t}},
            )
        await uow.session.flush()
        for p in snap["people"]:
            uow.session.add(
                m.Membership(
                    id=new_id(),
                    team_id=team_id[p["team"]],
                    person_id=person_id[p["handle"]],
                    role=MembershipRole.LEAD
                    if TEAM_LEAD[p["team"]] == p["handle"]
                    else MembershipRole.MEMBER,
                    joined_at=AS_OF,
                )
            )
            lead = TEAM_LEAD[p["team"]]
            manager = lead if lead != p["handle"] else "@mahmoud"
            if manager != p["handle"]:
                uow.session.add(
                    m.ReportingLine(
                        id=new_id(),
                        workspace_id=ws.id,
                        person_id=person_id[p["handle"]],
                        manager_id=person_id[manager],
                        kind=ReportingKind.LINE,
                        is_primary=True,
                    )
                )
        emit(
            uow,
            workspace_id=ws.id,
            actor_id=boss.id,
            entity_type=TargetType.PERSON,
            entity_id=boss.id,
            event_type="reporting_line.changed",
            payload={"after": {"seeded": True}},
        )
        await uow.commit()
        ids.update(ws_id=ws.id, boss=boss.id, person=person_id, team=team_id)

    # ---- B: products, projects, milestones -------------------------------------------------------------
    async with UnitOfWork(session_factory) as uow:
        ws = await uow.session.get(m.Workspace, ids["ws_id"])
        assert ws is not None  # noqa: S101
        uow.context.update(actor_id=ids["boss"], correlation_id=new_id())
        boss = ids["boss"]
        product: dict[str, m.Product] = {}
        for p in snap["products"]:
            product[p["key"]] = await create_product(
                uow, ws, key=p["key"], name=p["name"], owner_id=boss, actor_id=boss
            )
        project: dict[str, m.Project] = {}
        for p in snap["projects"]:
            project[p["key"]] = await create_project(
                uow,
                ws,
                key=p["key"],
                name=p["name"],
                owner_id=ids["person"][PROJECT_OWNER[p["key"]]],
                actor_id=boss,
                description=p["summary"],
                team_id=ids["team"][p["team"]],
                methodology=METHODOLOGY[p["methodology"]],
                status=ProjectStatus.ACTIVE,
                settings=PROJECT_SETTINGS.get(p["key"], {}),
            )
            await link(
                uow,
                ws,
                product[p["product"]],
                target_type=TargetType.PROJECT,
                target_id=project[p["key"]].id,
                role=ProductLinkRole.DELIVERS,
                actor_id=boss,
            )
        for ms in snap["milestones"]:
            uow.session.add(
                m.Milestone(
                    id=new_id(),
                    project_id=project[ms["project"]].id,
                    name=ms["name"],
                    due_date=date.fromisoformat(ms["due"]),
                    kind=MilestoneKind.DEADLINE,
                )
            )
        await uow.commit()
        ids.update(project={k: v.id for k, v in project.items()})

    # ---- C: epics, items, tags, stages, risks -----------------------------------------------------------
    created = 0
    async with UnitOfWork(session_factory) as uow:
        ws = await uow.session.get(m.Workspace, ids["ws_id"])
        assert ws is not None  # noqa: S101
        s = uow.session
        boss = ids["boss"]
        uow.context.update(actor_id=boss, correlation_id=new_id())
        process = await proc.get_process(s, ws.id)
        stages = {st.key: st for st in await proc.stages_of(s, process.id)}
        epic_type = await item_type(s, ws.id, ItemTypeKey.EPIC)
        type_id = {
            k.value: (await item_type(s, ws.id, k)).id
            for k in (ItemTypeKey.TASK, ItemTypeKey.STORY, ItemTypeKey.BUG)
        }
        default_scheme = await weights.ensure_default_scheme(s, ws.id)
        _ = default_scheme
        team_of = {p["handle"]: ids["team"][p["team"]] for p in snap["people"]}
        epic: dict[str, m.WorkItem] = {}
        for e in snap["epics"]:
            proj = await s.get(m.Project, ids["project"][e["project"]])
            assert proj is not None  # noqa: S101
            owner = ids["person"][PROJECT_OWNER[e["project"]]]
            epic[e["key"]] = await items.create_item(
                uow,
                ws,
                proj,
                title=e["title"],
                reporter_id=owner,
                assignee_id=owner,
                team_id=proj.team_id,
                type_id=epic_type.id,
                key=e["key"],
                priority_source=ValueSource.RULE,
                default_stage=False,
            )
            created += 1
        tags: dict[str, m.Tag] = {}
        made: dict[str, m.WorkItem] = {}
        for it in snap["items"]:
            proj = await s.get(m.Project, ids["project"][it["project"]])
            assert proj is not None  # noqa: S101
            who = ids["person"][it["assignee"]]
            row = await items.create_item(
                uow,
                ws,
                proj,
                title=it["title"],
                reporter_id=who,
                assignee_id=who,
                team_id=team_of[it["assignee"]],
                type_id=type_id[it["type"]],
                parent=epic.get(it["epic"]) if it["epic"] else None,
                key=it["key"],
                priority=PRIORITY[it["priority"]],
                priority_source=ValueSource.RULE,
                planned_start=date.fromisoformat(it["planned_start"]),
                planned_end=date.fromisoformat(it["planned_end"]),
                default_stage=False,
            )
            if it["estimate_h"] is not None:
                await items.record_estimate(
                    uow, row, person_id=who, value=Decimal(str(it["estimate_h"])), unit=EstimationUnit.HOURS
                )
            states = await workflow_states(s, proj.workflow_id)
            category = {
                "open": StateCategory.OPEN,
                "active": StateCategory.ACTIVE,
                "done": StateCategory.DONE,
            }[it["state"]]
            if category != StateCategory.OPEN:
                at = _dt(it["actual_end"]) if it["actual_end"] else _dt(it["planned_start"])
                await items.set_state(
                    uow, row, pick_state(states, category, terminal=category == StateCategory.DONE), at=at
                )
            await proc.place(
                uow,
                ws,
                target_type=TargetType.WORK_ITEM,
                target_id=row.id,
                process=process,
                stage=stages[it["stage"]],
                moved_by=who,
                reason="seed",
                source=EventSource.IMPORT,
                entered_at=AS_OF - timedelta(days=it["stage_age_days"]),
                sync_state=False,
                strict=False,
            )
            for name in it["tags"]:
                if name not in tags:
                    tags[name], _ = await items.get_or_create_tag(uow, ws, name, provisional=False)
                s.add(
                    m.TagLink(
                        id=new_id(), tag_id=tags[name].id, target_type=TargetType.WORK_ITEM, target_id=row.id
                    )
                )
            made[it["key"]] = row
            await weights.recompute_item(uow, ws, row, actor_id=boss)
            emit(
                uow,
                workspace_id=ws.id,
                actor_id=who,
                entity_type=TargetType.WORK_ITEM,
                entity_id=row.id,
                event_type="item.created",
                payload={"after": {"key": row.key, "seeded": True}},
                source=EventSource.IMPORT,
            )
            created += 1
        # Risks last: the snapshot attaches each risk to specific items, so they are item-scoped (a project-
        # scoped risk would be inherited by every item).
        for r in snap["risks"]:
            linked = [it["key"] for it in snap["items"] if r["key"] in it["risks"]]
            proj = await s.get(m.Project, ids["project"][r["project"]])
            assert proj is not None  # noqa: S101
            risk = await risk_svc.create_risk(
                uow,
                ws,
                proj,
                title=r["title"],
                owner_id=ids["person"][PROJECT_OWNER[r["project"]]],
                actor_id=boss,
                key=r["key"],
                category=RiskCategory.TECHNICAL,
                probability=r["p"],
                impact=r["i"],
                status=RISK_STATUS[r["status"]],
                spread_links=False,
                target_type=TargetType.WORK_ITEM if linked else TargetType.PROJECT,
                target_id=made[linked[0]].id if linked else proj.id,
            )
            for key in linked:
                await risk_svc.link_item(uow, ws, risk, made[key], actor_id=boss)
        await uow.commit()

    # ---- D: work sessions through the ingestion pipeline (historical timestamps) -------------------
    live = {n["item"]: n for n in snap["now"]}
    for it in snap["items"]:
        msgs: list[dict[str, Any]] = []
        touched = _dt(it["last_touched"])
        base = {"actor": it["assignee"], "item": {"key": it["key"]}}
        for n in range(it["evidence_count"]):
            msgs.append(
                {
                    **base,
                    "action": "evidence",
                    "at": touched.isoformat(),
                    "client_id": f"seed-ev-{it['key']}-{n}",
                    "evidence": [{"kind": "note", "title": f"note {n + 1}"}],
                }
            )
        if it["hours_logged"] > 0:
            msgs.append(
                {
                    **base,
                    "action": "log_time",
                    "at": touched.isoformat(),
                    "client_id": f"seed-t-{it['key']}",
                    "minutes": round(it["hours_logged"] * 60),
                }
            )
        if it["key"] in live:
            n = live[it["key"]]
            msgs.append(
                {
                    **base,
                    "action": "blocked" if n["kind"] == "blocked" else "start",
                    "at": (AS_OF - timedelta(minutes=n["since_min"])).isoformat(),
                    "client_id": f"seed-live-{it['key']}",
                    "note": "waiting" if n["kind"] == "blocked" else None,
                }
            )
        if msgs:
            batch = ClientBatch.model_validate(
                {"workspace": "acme", "messages": msgs, "client_version": "seed"}
            )
            results = await ingest_batch(session_factory, ids["ws_id"], batch)
            bad = [r for r in results if r.status == "failed"]
            if bad:
                raise RuntimeError(f"seed ingestion failed for {it['key']}: {bad[0].error_code}")
    # The generator's WIP rule ("GOV/build above 4") becomes a real stage limit so pipelines show the breach.
    async with UnitOfWork(session_factory) as uow:
        build = await uow.session.scalar(
            select(m.Stage)
            .join(m.Process, m.Process.id == m.Stage.process_id)
            .where(m.Process.workspace_id == ids["ws_id"], m.Stage.key == "build")
        )
        if build is not None:
            build.wip_limit = 4
        await uow.commit(allow_unlogged=True)
    return created
