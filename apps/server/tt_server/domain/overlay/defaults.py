"""Deterministic one-click defaults for review rows (Phase 13 R6.1).

The queue must offer a primary button even with no LLM gateway. Order for a missing project:
the item's parent project, then a glossary hit on the heard phrase, then the reporter's most-used project in the last
30 days, then the default project of the reporter's team. The gateway may rank; it never replaces this ladder.
"""

from __future__ import annotations

import difflib
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import utcnow
from tt_schema.enums import ProjectKind

from tt_server.db import models as m


async def _glossary_project(s: AsyncSession, ws_id: str, phrase: str) -> m.Project | None:
    if not phrase:
        return None
    entry = await s.scalar(
        select(m.GlossaryEntry).where(
            m.GlossaryEntry.workspace_id == ws_id,
            m.GlossaryEntry.deleted_at.is_(None),
            func.lower(m.GlossaryEntry.phrase) == phrase.lower(),
            m.GlossaryEntry.resolves_to_type == "project",
        )
    )
    return await s.get(m.Project, entry.resolves_to_id) if entry else None


async def _most_used_project(s: AsyncSession, ws_id: str, person_id: str) -> m.Project | None:
    since = utcnow() - timedelta(days=30)
    row = (
        await s.execute(
            select(m.WorkItem.project_id, func.count().label("n"))
            .join(m.Project, m.Project.id == m.WorkItem.project_id)
            .where(
                m.Project.workspace_id == ws_id,
                m.Project.kind != ProjectKind.INBOX,
                m.Project.deleted_at.is_(None),
                m.WorkItem.reporter_id == person_id,
                m.WorkItem.deleted_at.is_(None),
                m.WorkItem.created_at >= since,
            )
            .group_by(m.WorkItem.project_id)
            .order_by(func.count().desc(), m.WorkItem.project_id)
            .limit(1)
        )
    ).first()
    return await s.get(m.Project, row[0]) if row else None


async def _team_project(s: AsyncSession, ws_id: str, person_id: str) -> m.Project | None:
    team = await s.scalar(
        select(m.Membership.team_id)
        .where(m.Membership.person_id == person_id, m.Membership.left_at.is_(None))
        .order_by(m.Membership.joined_at)
    )
    if not team:
        return None
    return await s.scalar(
        select(m.Project)
        .where(
            m.Project.workspace_id == ws_id,
            m.Project.team_id == team,
            m.Project.kind != ProjectKind.INBOX,
            m.Project.deleted_at.is_(None),
        )
        .order_by(m.Project.created_at)
        .limit(1)
    )


async def suggest_project(s: AsyncSession, ws_id: str, item: m.WorkItem, phrase: str) -> dict[str, Any] | None:
    reporter = item.reporter_id
    if item.parent_id:
        parent = await s.get(m.WorkItem, item.parent_id)
        proj = await s.get(m.Project, parent.project_id) if parent else None
        if proj and proj.kind != ProjectKind.INBOX:
            return {"choice": {"project": proj.key}, "reason": f"under {parent.key if parent else ''}", "confidence": 0.8}
    if proj := await _glossary_project(s, ws_id, phrase):
        return {"choice": {"project": proj.key}, "reason": f"“{phrase}” is in the glossary", "confidence": 0.9}
    if proj := await _most_used_project(s, ws_id, reporter):
        return {"choice": {"project": proj.key}, "reason": "their most-used project this month", "confidence": 0.6}
    if proj := await _team_project(s, ws_id, reporter):
        return {"choice": {"project": proj.key}, "reason": "their team's project", "confidence": 0.5}
    return None


async def suggest_person(s: AsyncSession, ws_id: str, heard: str) -> dict[str, Any] | None:
    handles = list((await s.scalars(select(m.WorkspaceMember.handle).where(m.WorkspaceMember.workspace_id == ws_id))).all())
    wanted = ("@" + heard.lstrip("@")).lower()
    close = difflib.get_close_matches(wanted, [h.lower() for h in handles], n=1, cutoff=0.75)
    if not close:
        return None
    handle = next(h for h in handles if h.lower() == close[0])
    return {"choice": {"handle": handle}, "reason": f"looks like {handle}", "confidence": 0.6}


async def suggest_default(
    s: AsyncSession, ws_id: str, kind: str, item: m.WorkItem, suggestion: dict[str, Any]
) -> dict[str, Any] | None:
    if kind == "unclassified_project":
        return await suggest_project(s, ws_id, item, str(suggestion.get("phrase") or ""))
    if kind == "unknown_person" and (heard := suggestion.get("name") or suggestion.get("handle")):
        return await suggest_person(s, ws_id, str(heard))
    return None
