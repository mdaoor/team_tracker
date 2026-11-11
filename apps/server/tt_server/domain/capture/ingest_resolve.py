"""Ingestion resolution: who acts for whom, which project and which item a message refers to, review items."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.client import ClientAction, ClientMessage, ClientTaskRef
from tt_schema.enums import (
    StateCategory,
    TargetType,
    Visibility,
)

from tt_server.acl.context import load_context
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.domain.capture.ingest_context import (
    MATCH_GAP,
    MATCH_THRESHOLD,
    RECENCY_BONUS,
    RECENCY_DAYS,
    Ctx,
)
from tt_server.domain.common import aware, system_person
from tt_server.domain.items import service as items

# ---------------------------------------------------------------- resolution helpers


async def member_by_handle(session: AsyncSession, ws_id: str, handle: str) -> m.WorkspaceMember | None:
    """The active workspace member with this `@handle` (case-insensitive), or None."""
    handle = handle if handle.startswith("@") else "@" + handle
    return await session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws_id,
            func.lower(m.WorkspaceMember.handle) == handle.lower(),
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )


async def can_act_for(session: AsyncSession, ws_id: str, actor_pid: str, subject_pid: str) -> bool:
    """Whether `actor_pid` may record work for `subject_pid`: themselves or someone they manage."""
    if actor_pid == subject_pid:
        return True
    line = await session.scalar(
        select(m.ReportingLine.id).where(
            m.ReportingLine.workspace_id == ws_id,
            m.ReportingLine.manager_id == actor_pid,
            m.ReportingLine.person_id == subject_pid,
            m.ReportingLine.deleted_at.is_(None),
        )
    )
    if line:
        return True
    mine = select(m.Membership.team_id).where(
        m.Membership.person_id == actor_pid, m.Membership.left_at.is_(None)
    )
    shared = await session.scalar(
        select(m.Membership.id).where(
            m.Membership.person_id == subject_pid,
            m.Membership.left_at.is_(None),
            m.Membership.team_id.in_(mine),
        )
    )
    return shared is not None


# ---------------------------------------------------------------- development project (ADR-0055)

DEV_TAG = "development"
DEFAULT_DEV_KEY = "DEV"


def dev_project_key(ws: m.Workspace) -> str:
    """The workspace setting `development.project_key` (default `DEV`)."""
    cfg = (ws.settings or {}).get("development") or {}
    return str(cfg.get("project_key") or DEFAULT_DEV_KEY).upper()


def is_dev_project(project: m.Project) -> bool:
    """True for the per-workspace development project flagged in its settings."""
    return bool((project.settings or {}).get("development"))


async def ensure_dev_project(ctx: Ctx, key: str) -> m.Project:
    """Create the per-workspace development project on first use: private, owned by the system bot (no human gains rights)."""
    from tt_server.domain.projects.service import create_project

    bot = await system_person(ctx.session)
    project = await create_project(
        ctx.uow,
        ctx.ws,
        key=key,
        name="Development",
        owner_id=bot.id,
        actor_id=ctx.actor.person_id,
        visibility=Visibility.PRIVATE,
        settings={"development": True},
    )
    await items.get_or_create_tag(ctx.uow, ctx.ws, DEV_TAG, provisional=False)
    return project


_STOP = frozenset("a an and the of to for in on at with from by is it this that my our be as or".split())


def title_phrase(title: str, *, limit: int = 6) -> str:
    """The glossary phrase a title stands for when the reporter named no project: its first significant words, lowercased.

    Resolving the review row to a project teaches this phrase, so the next line that says the same thing lands there."""
    words = [w for w in re.findall(r"[\w'-]+", title.lower()) if w not in _STOP]
    return " ".join(words[:limit])


def calendar_key(msg: ClientMessage) -> tuple[str, str, str] | None:
    """The (calendar, event, instance start) triple that identifies a calendar-sourced message, or None (ADR-0054)."""
    ref = msg.source.calendar_ref if msg.source else None
    if ref is None:
        return None
    start = aware(ref.instance_start).isoformat() if ref.instance_start else ""
    return ref.provider, ref.event_id, start


async def resolve_project(ctx: Ctx) -> m.Project:
    """key -> name -> aliases -> glossary -> the subject's Inbox."""
    session, ws = ctx.session, ctx.ws
    name = (ctx.msg.project or "").strip()
    projects = list(
        (
            await session.scalars(
                select(m.Project).where(m.Project.workspace_id == ws.id, m.Project.deleted_at.is_(None))
            )
        ).all()
    )
    lowered = name.lower()

    def by_key_or_name(text: str) -> m.Project | None:
        t = text.lower()
        for p in projects:
            if p.key.lower() == t:
                return p
        for p in projects:
            if p.name.lower() == t:
                return p
        return None

    if name:
        hit = by_key_or_name(name)
        if hit:
            ctx.resolution["project"] = {"method": "key_or_name", "key": hit.key}
            return hit
        for p in projects:
            if lowered in [str(a).lower() for a in (p.settings or {}).get("aliases", [])]:
                ctx.resolution["project"] = {"method": "alias", "key": p.key}
                return p
        if lowered == dev_project_key(ws).lower():  # first use of the development project creates it
            hit = await ensure_dev_project(ctx, dev_project_key(ws))
            ctx.resolution["project"] = {"method": "development", "key": hit.key, "created": True}
            return hit
        entry = await session.scalar(
            select(m.GlossaryEntry).where(
                m.GlossaryEntry.workspace_id == ws.id,
                func.lower(m.GlossaryEntry.phrase) == lowered,
                m.GlossaryEntry.resolves_to_type == "project",
                m.GlossaryEntry.deleted_at.is_(None),
            )
        )
        if entry:
            hit = next((p for p in projects if p.id == entry.resolves_to_id), None)
            if hit:
                entry.hits += 1
                ctx.resolution["project"] = {"method": "glossary", "key": hit.key}
                return hit
        glossary = {str(k).lower(): v for k, v in ((ws.settings or {}).get("glossary") or {}).items()}
        target = glossary.get(lowered)
        if target:
            hit = by_key_or_name(str(target))
            if hit:
                ctx.resolution["project"] = {"method": "glossary", "key": hit.key}
                return hit
    inbox = next((p for p in projects if p.id == ctx.subject.inbox_project_id), None)
    if inbox is None:
        raise RuntimeError("member has no inbox project")
    ctx.resolution["project"] = {"method": "inbox", "key": inbox.key, "unresolved": name or None}
    return inbox


async def state_category(session: AsyncSession, state_id: str) -> StateCategory:
    """The category of a workflow state; `open` when the state is unknown."""
    cat = await session.scalar(select(m.WorkflowState.category).where(m.WorkflowState.id == state_id))
    return cat or StateCategory.OPEN


async def find_item(ctx: Ctx, ref: ClientTaskRef) -> m.WorkItem | None:
    """Look an item up by id or key *as the reporter*: an item they cannot see behaves as if it did not exist (review M1)."""
    session, ws_id = ctx.session, ctx.ws.id
    acl = ctx.__dict__.get("_acl")
    if acl is None:
        acl = ctx.__dict__["_acl"] = await load_context(session, ws_id, ctx.actor_person.id)
    base = visible(
        select(m.WorkItem)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .where(m.Project.workspace_id == ws_id, m.WorkItem.deleted_at.is_(None)),
        acl,
        m.WorkItem,
    )
    found: m.WorkItem | None = None
    if ref.id:
        found = await session.scalar(base.where(m.WorkItem.id == ref.id))
    elif ref.key:
        found = await session.scalar(base.where(func.lower(m.WorkItem.key) == ref.key.lower()))
    return found


def _score(sim: float, word_sim: float, query: str, updated_at: datetime) -> float:
    single_token = len(query.split()) < 2
    base = sim if single_token else 0.5 * sim + 0.5 * word_sim
    bonus = RECENCY_BONUS if (utcnow() - aware(updated_at)).days < RECENCY_DAYS else 0.0
    return base + bonus


async def resolve_item(ctx: Ctx, project: m.Project) -> tuple[m.WorkItem | None, dict[str, Any]]:
    """Return (item or None to create, resolution info). ADR-0002: score >= 0.6 and gap >= 0.1."""
    msg, session = ctx.msg, ctx.session
    ref = msg.item
    if ref and (ref.id or ref.key):
        found = await find_item(ctx, ref)
        if found:
            return found, {"method": "matched", "by": "id" if ref.id else "key", "score": 1.0}
        ctx.warnings.append("item_not_found")
    title = (ref.title if ref else None) or None
    if msg.action == ClientAction.CREATE or not title:
        return None, {"method": "created"}
    person_id = ctx.subject.person_id
    assigned = exists().where(
        m.Assignment.work_item_id == m.WorkItem.id,
        m.Assignment.person_id == person_id,
        m.Assignment.unassigned_at.is_(None),
    )
    stmt = (
        select(
            m.WorkItem,
            func.similarity(m.WorkItem.title, title),
            func.word_similarity(title, m.WorkItem.title),
        )
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(
            m.WorkItem.deleted_at.is_(None),
            m.WorkflowState.category != StateCategory.DONE,
            or_(m.WorkItem.assignee_id == person_id, m.WorkItem.reporter_id == person_id, assigned),
        )
    )
    if ctx.resolution["project"]["method"] != "inbox":
        stmt = stmt.where(m.WorkItem.project_id == project.id)
    else:
        stmt = stmt.join(m.Project, m.Project.id == m.WorkItem.project_id).where(
            m.Project.workspace_id == ctx.ws.id
        )
    rows = (await session.execute(stmt)).all()
    scored = sorted(
        ((_score(float(s), float(w), title, it.updated_at), it) for it, s, w in rows), key=lambda x: -x[0]
    )
    if (
        scored
        and scored[0][0] >= MATCH_THRESHOLD
        and (len(scored) < 2 or scored[0][0] - scored[1][0] >= MATCH_GAP)
    ):
        return scored[0][1], {"method": "matched", "by": "title", "score": round(scored[0][0], 3)}
    return None, {"method": "created", "best_score": round(scored[0][0], 3) if scored else None}


# ---------------------------------------------------------------- review items


async def add_review(
    ctx: Ctx,
    kind: str,
    target_id: str,
    suggestion: dict[str, Any],
    confidence: float | None = None,
    target_type: TargetType = TargetType.WORK_ITEM,
) -> None:
    """Queue a low-confidence suggestion for human review instead of applying it."""
    row = m.ReviewItem(
        id=new_id(),
        workspace_id=ctx.ws.id,
        kind=kind,
        target_type=target_type.value,
        target_id=target_id,
        suggestion=suggestion,
        confidence=Decimal(str(confidence)) if confidence is not None else None,
    )
    ctx.session.add(row)
    ctx.review_ids.append(row.id)
    ctx.emit("review.created", target_type, target_id, {"after": {"review_item_id": row.id, "kind": kind}})
