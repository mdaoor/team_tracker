"""ContextPack assembly (Phase 2 §2.1): what a client needs to translate a line, personalised per actor."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import utcnow
from tt_schema.enums import ActivityStatus, Priority, ProjectKind, StateCategory, TargetType

from tt_server.db import models as m
from tt_server.domain.contextpack_roles import role_context

SCHEMA_VERSION = 1
MAX_ITEMS, MAX_TAGS, MAX_PEOPLE = 200, 300, 300
from tt_schema.enums import RiskStatus  # noqa: E402

OPEN_RISK_STATUSES = [r for r in RiskStatus if r not in (RiskStatus.CLOSED, RiskStatus.OCCURRED)]
DEFAULT_REQUIRED = ["action", "item", "project"]


async def _my_projects(session: AsyncSession, ws_id: str, person_id: str, now: datetime) -> list[m.Project]:
    member_of = select(m.ProjectMember.project_id).where(m.ProjectMember.person_id == person_id)
    my_teams = select(m.Membership.team_id).where(
        m.Membership.person_id == person_id, m.Membership.left_at.is_(None)
    )
    touched = select(m.WorkItem.project_id).where(
        or_(m.WorkItem.assignee_id == person_id, m.WorkItem.reporter_id == person_id),
        m.WorkItem.deleted_at.is_(None),
    )
    recent = select(m.Activity.project_id).where(
        m.Activity.person_id == person_id, m.Activity.started_at >= now - timedelta(days=30)
    )
    stmt = (
        select(m.Project)
        .where(
            m.Project.workspace_id == ws_id,
            m.Project.deleted_at.is_(None),
            m.Project.kind != ProjectKind.INBOX,
            or_(
                m.Project.id.in_(member_of),
                m.Project.team_id.in_(my_teams),
                m.Project.id.in_(touched),
                m.Project.id.in_(recent),
                m.Project.owner_id == person_id,
            ),
        )
        .order_by(m.Project.key)
    )
    return list((await session.scalars(stmt)).all())


async def build_pack(
    session: AsyncSession, ws: m.Workspace, member: m.WorkspaceMember, *, today: date | None = None
) -> dict[str, Any]:
    """Assemble the per-person context pack (role, assignments, activity, plan) that clients and the LLM consume."""
    now = utcnow()
    today = today or now.date()
    person_id = member.person_id
    projects = await _my_projects(session, ws.id, person_id, now)
    pids = [p.id for p in projects]
    pkey = {p.id: p.key for p in projects}

    entries = (
        await session.scalars(
            select(m.GlossaryEntry).where(
                m.GlossaryEntry.workspace_id == ws.id, m.GlossaryEntry.deleted_at.is_(None)
            )
        )
    ).all()
    glossary: dict[str, str] = {}
    aliases_by_project: dict[str, list[str]] = {}
    for e in entries:
        target = await _glossary_target(session, e)
        if target:
            glossary[e.phrase] = target
        if e.resolves_to_type == "project":
            aliases_by_project.setdefault(e.resolves_to_id, []).append(e.phrase)
    manual = (ws.settings or {}).get("manual_mode", {})
    required_default = manual.get("required_fields", DEFAULT_REQUIRED)
    per_project_required = {
        p.key: (p.settings or {}).get("manual_mode", {}).get("required_fields")
        for p in projects
        if (p.settings or {}).get("manual_mode", {}).get("required_fields")
    }
    processes = (
        await session.scalars(
            select(m.Process)
            .where(m.Process.workspace_id == ws.id, m.Process.deleted_at.is_(None))
            .order_by(m.Process.key)
        )
    ).all()
    stages: dict[str, list[str]] = {}
    for proc in processes:
        stages[proc.key] = list(
            (
                await session.scalars(
                    select(m.Stage.key)
                    .where(m.Stage.process_id == proc.id, m.Stage.deleted_at.is_(None))
                    .order_by(m.Stage.position)
                )
            ).all()
        )
    process_key = {proc.id: proc.key for proc in processes}
    project_rows = []
    for p in projects:
        jira_cfg = (p.settings or {}).get("jira", {})
        project_rows.append(
            {
                "key": p.key,
                "name": p.name,
                "aliases": sorted(
                    {*(p.settings or {}).get("aliases", []), *aliases_by_project.get(p.id, [])}
                ),
                "process": process_key.get(p.default_process_id) if p.default_process_id else None,
                "default_stage": (p.settings or {}).get("default_stage")
                or (
                    stages.get(process_key.get(p.default_process_id, ""), [None])[0]
                    if p.default_process_id
                    else None
                ),
                "jira": (p.external_refs or {}).get("jira"),
                "jira_enabled": bool(
                    jira_cfg.get("enabled", (p.external_refs or {}).get("jira") is not None)
                ),
                "jira_auto_send": bool(jira_cfg.get("auto_send", False)),
            }
        )

    assigned = exists().where(
        m.Assignment.work_item_id == m.WorkItem.id,
        m.Assignment.person_id == person_id,
        m.Assignment.unassigned_at.is_(None),
    )
    item_rows = (
        await session.execute(
            select(m.WorkItem.key, m.WorkItem.title, m.WorkItem.project_id)
            .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
            .where(
                m.WorkItem.deleted_at.is_(None),
                m.WorkflowState.category.in_([StateCategory.OPEN, StateCategory.ACTIVE]),
                or_(m.WorkItem.assignee_id == person_id, assigned),
            )
            .order_by(m.WorkItem.updated_at.desc(), m.WorkItem.id)
            .limit(MAX_ITEMS + 1)
        )
    ).all()
    my_open_items = []
    for key, title, project_id in item_rows[:MAX_ITEMS]:
        proj = pkey.get(project_id) or await session.scalar(
            select(m.Project.key).where(m.Project.id == project_id)
        )
        my_open_items.append({"key": key, "title": title, "stage": None, "project": proj})
    running = (
        await session.execute(
            select(m.WorkItem.key, m.WorkItem.title)
            .join(m.Activity, m.Activity.work_item_id == m.WorkItem.id)
            .where(m.Activity.person_id == person_id, m.Activity.status == ActivityStatus.ACTIVE)
            .order_by(m.Activity.started_at.desc())
            .limit(1)
        )
    ).first()
    my_current = {"key": running[0], "title": running[1]} if running else None

    # people: team-mates, project members, and co-assignees on my projects
    my_teams = select(m.Membership.team_id).where(
        m.Membership.person_id == person_id, m.Membership.left_at.is_(None)
    )
    mates = select(m.Membership.person_id).where(
        m.Membership.team_id.in_(my_teams), m.Membership.left_at.is_(None)
    )
    proj_members = select(m.ProjectMember.person_id).where(m.ProjectMember.project_id.in_(pids))
    co_assignees = select(m.WorkItem.assignee_id).where(
        m.WorkItem.project_id.in_(pids), m.WorkItem.assignee_id.is_not(None)
    )
    people_rows = (
        await session.execute(
            select(m.WorkspaceMember.handle, m.Person.display_name, m.Person.id)
            .join(m.Person, m.Person.id == m.WorkspaceMember.person_id)
            .where(
                m.WorkspaceMember.workspace_id == ws.id,
                m.WorkspaceMember.deleted_at.is_(None),
                or_(m.Person.id.in_(mates), m.Person.id.in_(proj_members), m.Person.id.in_(co_assignees)),
            )
            .order_by(m.WorkspaceMember.handle)
            .limit(MAX_PEOPLE)
        )
    ).all()
    team_names = dict(
        (
            await session.execute(
                select(m.Membership.person_id, m.Team.slug)
                .join(m.Team, m.Team.id == m.Membership.team_id)
                .where(m.Membership.left_at.is_(None), m.Team.workspace_id == ws.id)
                .order_by(m.Membership.joined_at.desc())
            )
        ).all()
    )
    people = [{"handle": h, "name": n, "team": team_names.get(pid)} for h, n, pid in people_rows]

    tag_rows = (
        await session.execute(
            select(m.Tag.name, func.count(m.TagLink.id))
            .join(m.TagLink, m.TagLink.tag_id == m.Tag.id)
            .join(
                m.WorkItem,
                and_(m.TagLink.target_type == TargetType.WORK_ITEM, m.WorkItem.id == m.TagLink.target_id),
            )
            .where(
                m.Tag.workspace_id == ws.id,
                m.Tag.is_provisional.is_(False),
                m.Tag.deleted_at.is_(None),
                m.WorkItem.project_id.in_(pids),
                m.TagLink.created_at >= now - timedelta(days=90),
            )
            .group_by(m.Tag.name)
            .order_by(func.count(m.TagLink.id).desc(), m.Tag.name)
            .limit(MAX_TAGS)
        )
    ).all()
    tags = [t for t, _ in tag_rows]

    milestones = [
        {"name": ms.name, "project": pkey[ms.project_id], "due": ms.due_date.isoformat()}
        for ms in (
            await session.scalars(
                select(m.Milestone)
                .where(
                    m.Milestone.project_id.in_(pids),
                    m.Milestone.completed_at.is_(None),
                    m.Milestone.deleted_at.is_(None),
                    m.Milestone.due_date >= today - timedelta(days=7),
                )
                .order_by(m.Milestone.due_date)
            )
        ).all()
    ]
    iterations = [
        {"name": it.name, "project": pkey[it.project_id], "state": it.state.value}
        for it in (
            await session.scalars(
                select(m.Iteration)
                .where(
                    m.Iteration.project_id.in_(pids),
                    m.Iteration.state != "closed",
                    m.Iteration.deleted_at.is_(None),
                )
                .order_by(m.Iteration.start_date)
            )
        ).all()
    ]

    team_ids = select(m.Membership.team_id).where(
        m.Membership.person_id == person_id, m.Membership.left_at.is_(None)
    )
    rules = (
        await session.scalars(
            select(m.TranslatorRule)
            .where(
                m.TranslatorRule.workspace_id == ws.id,
                m.TranslatorRule.is_active.is_(True),
                m.TranslatorRule.deleted_at.is_(None),
                or_(
                    m.TranslatorRule.scope_type == "workspace",
                    and_(m.TranslatorRule.scope_type == "project", m.TranslatorRule.scope_id.in_(pids)),
                    and_(m.TranslatorRule.scope_type == "team", m.TranslatorRule.scope_id.in_(team_ids)),
                ),
            )
            .order_by(m.TranslatorRule.position, m.TranslatorRule.created_at)
        )
    ).all()

    fields = (
        await session.scalars(
            select(m.CustomFieldDefinition)
            .where(
                m.CustomFieldDefinition.workspace_id == ws.id,
                m.CustomFieldDefinition.deleted_at.is_(None),
                or_(
                    m.CustomFieldDefinition.project_id.is_(None), m.CustomFieldDefinition.project_id.in_(pids)
                ),
            )
            .order_by(m.CustomFieldDefinition.key)
        )
    ).all()

    dev_key = str(((ws.settings or {}).get("development") or {}).get("project_key") or "DEV").upper()
    dev_exists = (
        await session.scalar(
            select(m.Project.id).where(
                m.Project.workspace_id == ws.id, m.Project.key == dev_key, m.Project.deleted_at.is_(None)
            )
        )
        is not None
    )
    team_slugs = dict(
        (await session.execute(select(m.Team.id, m.Team.slug).where(m.Team.workspace_id == ws.id))).all()
    )
    teams_to_projects: dict[str, list[str]] = {}
    for p in projects:
        if p.team_id and p.team_id in team_slugs:
            teams_to_projects.setdefault(team_slugs[p.team_id], []).append(p.key)
    domains = ((ws.settings or {}).get("calendar") or {}).get("client_domains") or {}
    capacity = member.capacity_hours_per_week

    truncated = len(item_rows) > MAX_ITEMS or len(tag_rows) >= MAX_TAGS or len(people_rows) >= MAX_PEOPLE
    return {
        **await role_context(session, ws, person_id),
        "context_schema_version": SCHEMA_VERSION,
        "actor": member.handle,
        "workspace": ws.slug,
        "generated_at": now.isoformat(),
        "projects": project_rows,
        "my_open_items": my_open_items,
        "my_current_activity": my_current,
        "people": people,
        "tags": tags,
        "milestones": milestones,
        "iterations": iterations,
        "stages": stages,
        "priorities": [p.value for p in Priority],
        "risks": [
            {"key": r.key, "title": r.title, "project": pkey[r.project_id]}
            for r in (
                await session.scalars(
                    select(m.Risk)
                    .where(
                        m.Risk.project_id.in_(pids),
                        m.Risk.deleted_at.is_(None),
                        m.Risk.status.in_(OPEN_RISK_STATUSES),
                    )
                    .order_by(m.Risk.key)
                )
            ).all()
        ],
        "required_dimensions": [
            {"key": d.key, "name": d.name, "when": d.is_required_for}
            for d in (
                await session.scalars(
                    select(m.Dimension).where(
                        m.Dimension.workspace_id == ws.id,
                        m.Dimension.deleted_at.is_(None),
                        m.Dimension.is_required_for.is_not(None),
                    )
                )
            ).all()
        ],
        "manager_rules": [r.text for r in rules],
        "glossary": glossary,
        "custom_fields": [
            {
                "key": f.key,
                "name": f.name,
                "type": f.field_type.value,
                "options": f.options or [],
                "required": f.is_required,
            }
            for f in fields
        ],
        "manual_mode": {"required_fields": required_default, "per_project": per_project_required},
        "truncated": truncated,
        # Phase 15 (ADR-0054/0043): everything a personal tracker needs to suggest a project without the server seeing a calendar
        "development_project": {"key": dev_key, "tag": "development", "exists": dev_exists},
        "calendar_mapping_hints": {
            "teams": [{"team": t, "projects": sorted(ks)} for t, ks in sorted(teams_to_projects.items())],
            "client_domains": [
                {"domain": str(d).lower(), "project": str(k)} for d, k in sorted(domains.items())
            ],
        },
        "my_capacity_hours_per_week": float(capacity) if capacity is not None else None,
    }


async def _glossary_target(session: AsyncSession, e: m.GlossaryEntry) -> str | None:
    if e.resolves_to_type == "project":
        key = await session.scalar(select(m.Project.key).where(m.Project.id == e.resolves_to_id))
        return f"PROJECT:{key}" if key else None
    if e.resolves_to_type == "item":
        key = await session.scalar(select(m.WorkItem.key).where(m.WorkItem.id == e.resolves_to_id))
        return f"ITEM:{key}" if key else None
    if e.resolves_to_type == "person":
        handle = await session.scalar(
            select(m.WorkspaceMember.handle).where(
                m.WorkspaceMember.person_id == e.resolves_to_id,
                m.WorkspaceMember.workspace_id == e.workspace_id,
            )
        )
        return f"PERSON:{handle}" if handle else None
    return None


def etag_of(pack: dict[str, Any]) -> str:
    """Compute a stable strong ETag of a pack, ignoring its volatile generated_at stamp."""
    stable = {k: v for k, v in pack.items() if k != "generated_at"}
    return (
        '"' + hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:32] + '"'
    )
