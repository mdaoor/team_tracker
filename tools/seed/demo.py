"""Demo workspace seed: the `acme` workspace built from the data of `tools/gen_snapshot.py`.

`build_demo()` is pure: it returns schema-package models in dependency order. Ids are UUID v5 of a
natural key, so tests can look rows up deterministically (`det_id("work_item", "WEB-101")`).

TODO(P3): route through the ingestion + overlay services (this P0 version inserts directly via
SQLAlchemy and writes no events, no Classification mirrors and no dimension rows).
"""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.capture import Activity, Evidence, TimeEntry
from tt_schema.enums import (
    ActivityKind,
    ActivityStatus,
    CaptureSource,
    EstimationUnit,
    EvidenceKind,
    ItemTypeKey,
    MembershipRole,
    Methodology,
    MilestoneKind,
    Priority,
    ProjectStatus,
    RiskCategory,
    RiskStatus,
    StageCategory,
    StateCategory,
    TargetType,
    TeamKind,
)
from tt_schema.items import Tag, TagLink, WorkItem
from tt_schema.process import Process, Stage, StagePlacement
from tt_schema.products import Product, ProductLink
from tt_schema.projects import Milestone, Project, Risk, RiskLink, Workflow, WorkflowState, WorkItemType
from tt_schema.tenancy import Membership, Person, Team, Workspace, WorkspaceMember
from tt_server.db import models
from tt_server.db.uow import UnitOfWork

NAMESPACE = uuid.UUID("7e0a6b1e-0000-4000-8000-00000000acae")
AS_OF = datetime(2026, 10, 23, 18, 30, tzinfo=timezone(timedelta(hours=3)))
GEN_SNAPSHOT = Path(__file__).resolve().parents[1] / "gen_snapshot.py"

# who owns each project (not in the snapshot; chosen so every owner is a member of the project's team)
PROJECT_OWNER = {"WEB": "@sara", "MOB": "@youssef", "DATA": "@nour", "GOV": "@mahmoud"}
PROJECT_ID_KEYS = {"WEB", "MOB", "DATA", "GOV"}
STAGE_CATEGORY: dict[str, tuple[StageCategory, StateCategory]] = {
    "idea": (StageCategory.IDEATION, StateCategory.OPEN),
    "discovery": (StageCategory.PLANNING, StateCategory.OPEN),
    "design": (StageCategory.PLANNING, StateCategory.OPEN),
    "build": (StageCategory.EXECUTION, StateCategory.ACTIVE),
    "test": (StageCategory.VALIDATION, StateCategory.ACTIVE),
    "staging": (StageCategory.RELEASE, StateCategory.ACTIVE),
    "production": (StageCategory.OPERATE, StateCategory.DONE),
}
PRIORITY = {
    "low": Priority.LOW,
    "medium": Priority.MEDIUM,
    "high": Priority.HIGH,
    "critical": Priority.CRITICAL,
}
RISK_STATUS = {s.value: s for s in RiskStatus}
METHODOLOGY = {m.value: m for m in Methodology}
ACTIVITY_KIND = {k.value: k for k in ActivityKind}


def det_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{key}"))


def ltree_label(row_id: str) -> str:
    """ltree labels allow only [A-Za-z0-9_]; ids are stored in paths with '-' replaced by '_'."""
    return row_id.replace("-", "_")


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


def build_demo(snapshot: dict[str, Any] | None = None) -> list[BaseModel]:
    snap = snapshot or load_snapshot()
    out: list[BaseModel] = []
    ws = Workspace(
        id=det_id("workspace", "acme"), name="Acme", slug="acme", settings={"time_zone": "Asia/Riyadh"}
    )
    out.append(ws)

    # people, members, teams
    person_id = {p["handle"]: det_id("person", p["handle"]) for p in snap["people"]}
    for p in snap["people"]:
        out.append(
            Person(id=person_id[p["handle"]], display_name=p["name"], email=f"{p['handle'][1:]}@acme.example")
        )
    for p in snap["people"]:
        out.append(
            WorkspaceMember(
                id=det_id("member", p["handle"]),
                workspace_id=ws.id,
                person_id=person_id[p["handle"]],
                handle=p["handle"],
            )
        )
    team_id = {t: det_id("team", t) for t in snap["teams"]}
    for t in snap["teams"]:
        members = [p for p in snap["people"] if p["team"] == t]
        out.append(
            Team(
                id=team_id[t],
                workspace_id=ws.id,
                name=t,
                slug=t,
                kind=TeamKind.ORG,
                lead_id=person_id[members[0]["handle"]],
            )
        )
    for p in snap["people"]:
        out.append(
            Membership(
                id=det_id("membership", p["handle"]),
                team_id=team_id[p["team"]],
                person_id=person_id[p["handle"]],
                role=MembershipRole.MEMBER,
                joined_at=AS_OF,
            )
        )

    # workflow + item types
    wf = Workflow(id=det_id("workflow", "default"), workspace_id=ws.id, name="Default", is_default=True)
    out.append(wf)
    state_id: dict[str, str] = {}
    for i, (key, cat, initial, terminal) in enumerate(
        [
            ("open", StateCategory.OPEN, True, False),
            ("active", StateCategory.ACTIVE, False, False),
            ("done", StateCategory.DONE, False, True),
        ]
    ):
        state_id[key] = det_id("state", key)
        out.append(
            WorkflowState(
                id=state_id[key],
                workflow_id=wf.id,
                key=key,
                name=key.title(),
                category=cat,
                position=i,
                is_initial=initial,
                is_terminal=terminal,
            )
        )
    type_id: dict[str, str] = {}
    for level, key in enumerate([ItemTypeKey.EPIC, ItemTypeKey.STORY, ItemTypeKey.TASK, ItemTypeKey.BUG]):
        type_id[key.value] = det_id("item_type", key.value)
        out.append(
            WorkItemType(
                id=type_id[key.value],
                workspace_id=ws.id,
                key=key,
                name=key.value.title(),
                level=min(level, 2),
                default_workflow_id=wf.id,
            )
        )

    # delivery process
    proc = Process(
        id=det_id("process", "delivery"), workspace_id=ws.id, key="delivery", name="Delivery", is_default=True
    )
    out.append(proc)
    stage_id: dict[str, str] = {}
    for pos, name in enumerate(snap["stages"]):
        category, maps_to = STAGE_CATEGORY[name]
        stage_id[name] = det_id("stage", name)
        out.append(
            Stage(
                id=stage_id[name],
                process_id=proc.id,
                key=name,
                name=name.title(),
                position=pos,
                category=category,
                maps_to_state_category=maps_to,
                expected_duration_days=snap["stage_expected_days"][name] or None,
            )
        )

    # products, projects, milestones, risks
    owner0 = person_id["@mahmoud"]
    product_id = {p["key"]: det_id("product", p["key"]) for p in snap["products"]}
    for p in snap["products"]:
        out.append(
            Product(
                id=product_id[p["key"]],
                workspace_id=ws.id,
                key=p["key"],
                name=p["name"],
                owner_id=owner0,
                default_process_id=proc.id,
            )
        )
    project_id = {p["key"]: det_id("project", p["key"]) for p in snap["projects"]}
    for p in snap["projects"]:
        out.append(
            Project(
                id=project_id[p["key"]],
                workspace_id=ws.id,
                key=p["key"],
                name=p["name"],
                owner_id=person_id[PROJECT_OWNER[p["key"]]],
                workflow_id=wf.id,
                default_item_type_id=type_id["task"],
                methodology=METHODOLOGY[p["methodology"]],
                status=ProjectStatus.ACTIVE,
                team_id=team_id[p["team"]],
                default_process_id=proc.id,
                description=p["summary"],
            )
        )
        out.append(
            ProductLink(
                id=det_id("product_link", p["key"]),
                product_id=product_id[p["product"]],
                target_type=TargetType.PROJECT,
                target_id=project_id[p["key"]],
            )
        )
    for m in snap["milestones"]:
        out.append(
            Milestone(
                id=det_id("milestone", m["name"]),
                project_id=project_id[m["project"]],
                name=m["name"],
                due_date=date.fromisoformat(m["due"]),
                kind=MilestoneKind.DEADLINE,
            )
        )
    risk_id = {r["key"]: det_id("risk", r["key"]) for r in snap["risks"]}
    for r in snap["risks"]:
        out.append(
            Risk(
                id=risk_id[r["key"]],
                project_id=project_id[r["project"]],
                key=r["key"],
                title=r["title"],
                owner_id=person_id[PROJECT_OWNER[r["project"]]],
                target_type=TargetType.PROJECT,
                target_id=project_id[r["project"]],
                category=RiskCategory.TECHNICAL,
                probability=r["p"],
                impact=r["i"],
                status=RISK_STATUS[r["status"]],
            )
        )

    # epics, then items (epics first: items point at them via parent_id)
    epic_id = {e["key"]: det_id("work_item", e["key"]) for e in snap["epics"]}
    for e in snap["epics"]:
        proj = next(p for p in snap["projects"] if p["key"] == e["project"])
        out.append(
            WorkItem(
                id=epic_id[e["key"]],
                project_id=project_id[e["project"]],
                key=e["key"],
                type_id=type_id["epic"],
                title=e["title"],
                state_id=state_id["active"],
                reporter_id=person_id[PROJECT_OWNER[e["project"]]],
                path=ltree_label(epic_id[e["key"]]),
                team_id=team_id[proj["team"]],
            )
        )
    tag_id: dict[str, str] = {}
    for it in snap["items"]:
        for t in it["tags"]:
            if t not in tag_id:
                tag_id[t] = det_id("tag", t)
                out.append(Tag(id=tag_id[t], workspace_id=ws.id, name=t))
    now_by_item = {n["item"]: n for n in snap["now"]}
    for it in snap["items"]:
        iid = det_id("work_item", it["key"])
        parent = epic_id.get(it["epic"]) if it["epic"] else None
        path = ltree_label(iid) if not parent else f"{ltree_label(parent)}.{ltree_label(iid)}"
        est = Decimal(str(it["estimate_h"])) if it["estimate_h"] is not None else None
        done = it["state"] == "done"
        out.append(
            WorkItem(
                id=iid,
                project_id=project_id[it["project"]],
                key=it["key"],
                type_id=type_id[it["type"]],
                title=it["title"],
                state_id=state_id[it["state"]],
                reporter_id=person_id[it["assignee"]],
                assignee_id=person_id[it["assignee"]],
                team_id=team_id[it["team"]],
                parent_id=parent,
                path=path,
                priority=PRIORITY[it["priority"]],
                weight=Decimal(it["weight"]),
                estimate_value=est,
                estimate_unit=EstimationUnit.HOURS,
                planned_start=date.fromisoformat(it["planned_start"]),
                planned_end=date.fromisoformat(it["planned_end"]),
                actual_end=_dt(it["actual_end"]) if it["actual_end"] else None,
                progress_pct=100 if done else 0,
                closed_at=_dt(it["actual_end"]) if it["actual_end"] else None,
            )
        )
        # stage placement (current, entered stage_age_days ago)
        out.append(
            StagePlacement(
                id=det_id("placement", it["key"]),
                target_type=TargetType.WORK_ITEM,
                target_id=iid,
                process_id=proc.id,
                stage_id=stage_id[it["stage"]],
                entered_at=AS_OF - timedelta(days=it["stage_age_days"]),
            )
        )
        for t in it["tags"]:
            out.append(
                TagLink(
                    id=det_id("tag_link", f"{it['key']}:{t}"),
                    tag_id=tag_id[t],
                    target_type=TargetType.WORK_ITEM,
                    target_id=iid,
                )
            )
        for r in it["risks"]:
            out.append(
                RiskLink(id=det_id("risk_link", f"{it['key']}:{r}"), risk_id=risk_id[r], work_item_id=iid)
            )
        if it["hours_logged"] > 0:
            out.append(
                TimeEntry(
                    id=det_id("time_entry", it["key"]),
                    person_id=person_id[it["assignee"]],
                    work_item_id=iid,
                    date=date.fromisoformat(it["last_touched"]),
                    minutes=round(it["hours_logged"] * 60),
                    source=CaptureSource.IMPORTED,
                )
            )
        live = now_by_item.get(it["key"])
        if live or it["evidence_count"]:
            aid = det_id("activity", it["key"])
            started = AS_OF - timedelta(minutes=live["since_min"]) if live else _dt(it["last_touched"])
            out.append(
                Activity(
                    id=aid,
                    person_id=person_id[it["assignee"]],
                    work_item_id=iid,
                    project_id=project_id[it["project"]],
                    kind=ACTIVITY_KIND[live["kind"]] if live else ActivityKind.WORKING,
                    status=ActivityStatus.ACTIVE if live else ActivityStatus.ENDED,
                    started_at=started,
                    ended_at=None if live else started + timedelta(hours=1),
                )
            )
            for n in range(it["evidence_count"]):
                out.append(
                    Evidence(
                        id=det_id("evidence", f"{it['key']}:{n}"),
                        activity_id=aid,
                        work_item_id=iid,
                        person_id=person_id[it["assignee"]],
                        kind=EvidenceKind.NOTE,
                        title=f"note {n + 1}",
                        captured_at=started,
                        source=CaptureSource.IMPORTED,
                    )
                )
    return out


async def persist_demo(
    session_factory: async_sessionmaker[AsyncSession], rows: list[BaseModel] | None = None
) -> int:
    """Insert the demo rows directly (P0 allow-listed unlogged path); returns the number of rows."""
    rows = rows if rows is not None else build_demo()
    uow = UnitOfWork(session_factory)
    async with uow:
        group: type | None = None
        for row in rows:
            if group is not None and type(row) is not group:
                await uow.session.flush()  # keep FK order between groups
            group = type(row)
            uow.session.add(getattr(models, type(row).__name__)(**row.model_dump()))
        await uow.commit(allow_unlogged=True)
    return len(rows)
