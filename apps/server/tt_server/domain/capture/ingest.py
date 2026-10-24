"""Capture ingestion pipeline (Phase 1 §1.4): ClientBatch -> items, activities, time, evidence, events.

Capture never blocks: every unresolvable reference degrades to the Inbox, `is_provisional`, a `ReviewItem`
or a warning. The only rejection is a schema violation, handled at the API layer (422).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id, utcnow
from tt_schema.client import ClientAction, ClientBatch, ClientMessage, ClientTaskRef
from tt_schema.enums import (
    ActivityKind,
    ActivityStatus,
    AssignmentRole,
    CaptureSource,
    EstimationUnit,
    EventSource,
    EvidenceKind,
    ItemTypeKey,
    StateCategory,
    TargetType,
    ValueSource,
)

from tt_server.acl.context import load_context
from tt_server.acl.visibility import visible
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import aware, emit, zone
from tt_server.domain.items import service as items
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import process as proc
from tt_server.domain.overlay import risks as risks_svc
from tt_server.domain.overlay import weights
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions, ensure_value, sync_item_mirrors
from tt_server.domain.projects.service import item_type, pick_state, workflow_states

log = structlog.get_logger()

CONFIDENCE_FLOOR = 0.7
MATCH_THRESHOLD = 0.6
MATCH_GAP = 0.1
RECENCY_BONUS = 0.1
RECENCY_DAYS = 7


@dataclass
class Ctx:
    uow: UnitOfWork
    ws: m.Workspace
    msg: ClientMessage
    client_version: str
    correlation_id: str
    actor: m.WorkspaceMember
    subject: m.WorkspaceMember  # whose work this is (differs from actor for on_behalf_of)
    actor_person: m.Person
    subject_person: m.Person
    at: datetime
    warnings: list[str] = field(default_factory=list)
    review_ids: list[str] = field(default_factory=list)
    resolution: dict[str, Any] = field(default_factory=dict)
    events: list[Any] = field(default_factory=list)

    @property
    def session(self) -> AsyncSession:
        return self.uow.session

    def emit(
        self, event_type: str, entity_type: TargetType, entity_id: str, payload: dict[str, Any] | None = None
    ) -> None:
        self.events.append(
            emit(
                self.uow,
                workspace_id=self.ws.id,
                actor_id=self.actor.person_id,
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                payload=payload,
                source=EventSource.CLIENT,
                occurred_at=self.at,
                correlation_id=self.correlation_id,
                raw_input=self.msg.raw_input,
            )
        )

    def conf_ok(self, name: str) -> bool:
        c = (self.msg.llm_confidence or {}).get(name)
        return c is None or c >= CONFIDENCE_FLOOR


# ---------------------------------------------------------------- resolution helpers


async def _member_by_handle(session: AsyncSession, ws_id: str, handle: str) -> m.WorkspaceMember | None:
    handle = handle if handle.startswith("@") else "@" + handle
    return await session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws_id,
            func.lower(m.WorkspaceMember.handle) == handle.lower(),
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )


async def _can_act_for(session: AsyncSession, ws_id: str, actor_pid: str, subject_pid: str) -> bool:
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


async def _state_category(session: AsyncSession, state_id: str) -> StateCategory:
    cat = await session.scalar(select(m.WorkflowState.category).where(m.WorkflowState.id == state_id))
    return cat or StateCategory.OPEN


async def _find_item(ctx: Ctx, ref: ClientTaskRef) -> m.WorkItem | None:
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
    if ref.id:
        return await session.scalar(base.where(m.WorkItem.id == ref.id))  # type: ignore[no-any-return]
    if ref.key:
        return await session.scalar(base.where(func.lower(m.WorkItem.key) == ref.key.lower()))  # type: ignore[no-any-return]
    return None


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
        found = await _find_item(ctx, ref)
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


# ---------------------------------------------------------------- activities


async def _activities(
    session: AsyncSession, person_id: str, *, status: ActivityStatus | None = None, item_id: str | None = None
) -> list[m.Activity]:
    stmt = select(m.Activity).where(m.Activity.person_id == person_id, m.Activity.deleted_at.is_(None))
    if status:
        stmt = stmt.where(m.Activity.status == status)
    if item_id:
        stmt = stmt.where(m.Activity.work_item_id == item_id)
    return list((await session.scalars(stmt.order_by(m.Activity.started_at.desc()))).all())


async def _current_activity(ctx: Ctx, item: m.WorkItem | None) -> m.Activity | None:
    pid = ctx.subject.person_id
    if item is not None:
        live = [
            a
            for a in await _activities(ctx.session, pid, item_id=item.id)
            if a.status != ActivityStatus.ENDED
        ]
        if live:
            return live[0]
        any_ = await _activities(ctx.session, pid, item_id=item.id)
        return any_[0] if any_ else None
    live = await _activities(ctx.session, pid, status=ActivityStatus.ACTIVE)
    return live[0] if len(live) == 1 else None


async def _pause_all_active(ctx: Ctx, *, except_id: str | None = None) -> None:
    for act in await _activities(ctx.session, ctx.subject.person_id, status=ActivityStatus.ACTIVE):
        if act.id != except_id:
            act.status = ActivityStatus.PAUSED
            ctx.emit(
                "activity.paused",
                TargetType.ACTIVITY,
                act.id,
                {"reason": "auto_switch", "work_item_id": act.work_item_id},
            )


async def _new_activity(
    ctx: Ctx,
    item: m.WorkItem,
    kind: ActivityKind,
    *,
    status: ActivityStatus = ActivityStatus.ACTIVE,
    note: str | None = None,
    ended: bool = False,
) -> m.Activity:
    act = m.Activity(
        id=new_id(),
        person_id=ctx.subject.person_id,
        work_item_id=item.id,
        project_id=item.project_id,
        kind=kind,
        status=ActivityStatus.ENDED if ended else status,
        started_at=ctx.at,
        ended_at=ctx.at if ended else None,
        note=note,
        client_meta={"client": ctx.client_version},
    )
    ctx.session.add(act)
    await ctx.session.flush()
    return act


def _end(ctx: Ctx, act: m.Activity) -> None:
    act.status = ActivityStatus.ENDED
    act.ended_at = ctx.at
    ctx.emit("activity.ended", TargetType.ACTIVITY, act.id, {"work_item_id": act.work_item_id})


async def _move_state(ctx: Ctx, item: m.WorkItem, category: StateCategory, *, terminal: bool = False) -> bool:
    project = await ctx.session.get(m.Project, item.project_id)
    assert project is not None  # noqa: S101
    states = await workflow_states(ctx.session, project.workflow_id)
    target = pick_state(states, category, terminal=terminal)
    if target.id == item.state_id:
        return False
    old, new = await items.set_state(ctx.uow, item, target, at=ctx.at)
    ctx.emit(
        "item.state_changed",
        TargetType.WORK_ITEM,
        item.id,
        {"before": {"category": old.value}, "after": {"category": new.value}},
    )
    if old == StateCategory.DONE and new != StateCategory.DONE:
        ctx.emit("item.reopened", TargetType.WORK_ITEM, item.id, {})
    return True


# ---------------------------------------------------------------- actions


def _parse_evidence_kind(raw: Any) -> EvidenceKind:
    try:
        return EvidenceKind(str(raw))
    except ValueError:
        return EvidenceKind.NOTE


async def _evidence_target(ctx: Ctx, item: m.WorkItem) -> m.Activity:
    act = await _current_activity(ctx, item)
    if act is None:
        act = await _new_activity(ctx, item, ActivityKind.OTHER, ended=True)
    return act


def _add_evidence(
    ctx: Ctx,
    act: m.Activity,
    item: m.WorkItem,
    *,
    kind: EvidenceKind,
    title: str | None,
    body: str | None,
    url: str | None,
) -> None:
    row = m.Evidence(
        id=new_id(),
        activity_id=act.id,
        work_item_id=item.id,
        person_id=ctx.subject.person_id,
        kind=kind,
        title=title,
        body=body,
        url=url,
        captured_at=ctx.at,
        source=CaptureSource.MANUAL,
    )
    ctx.session.add(row)
    ctx.emit(
        "evidence.added",
        TargetType.WORK_ITEM,
        item.id,
        # title/url/body (trimmed) let policies and stage rules read what was attached, e.g. `auto_when: ... title.contains("merged")`
        {
            "after": {
                "evidence_id": row.id,
                "kind": kind.value,
                "title": title,
                "url": url,
                "body": (body or "")[:500] or None,
            }
        },
    )


async def apply_action(ctx: Ctx, item: m.WorkItem, created: bool) -> m.Activity | None:
    msg, action = ctx.msg, ctx.msg.action
    activity: m.Activity | None = None
    if action == ClientAction.START:
        await _pause_all_active(ctx)
        activity = await _new_activity(ctx, item, ActivityKind.WORKING)
        ctx.emit("activity.started", TargetType.ACTIVITY, activity.id, {"work_item_id": item.id})
        if item.actual_start is None:
            item.actual_start = ctx.at
        cat = await _state_category(ctx.session, item.state_id)
        if cat == StateCategory.DONE:
            await _move_state(ctx, item, StateCategory.ACTIVE)
        elif cat == StateCategory.OPEN:
            await _move_state(ctx, item, StateCategory.ACTIVE)
    elif action == ClientAction.PAUSE:
        activity = await _current_activity(ctx, item if not created or msg.item else None)
        if activity and activity.status == ActivityStatus.ACTIVE:
            activity.status = ActivityStatus.PAUSED
            ctx.emit(
                "activity.paused",
                TargetType.ACTIVITY,
                activity.id,
                {"reason": "manual", "work_item_id": item.id},
            )
    elif action == ClientAction.RESUME:
        activity = await _current_activity(ctx, item)
        if activity and activity.status == ActivityStatus.PAUSED:
            await _pause_all_active(ctx, except_id=activity.id)
            activity.status = ActivityStatus.ACTIVE
            ctx.emit("activity.resumed", TargetType.ACTIVITY, activity.id, {"work_item_id": item.id})
        elif not activity or activity.status == ActivityStatus.ENDED:
            await _pause_all_active(ctx)
            activity = await _new_activity(ctx, item, ActivityKind.WORKING)
            ctx.emit("activity.started", TargetType.ACTIVITY, activity.id, {"work_item_id": item.id})
    elif action == ClientAction.DONE:
        for act in await _activities(ctx.session, ctx.subject.person_id, item_id=item.id):
            if act.status != ActivityStatus.ENDED:
                activity = activity or act
                _end(ctx, act)
        if await _move_state(ctx, item, StateCategory.DONE, terminal=True):
            ctx.emit("item.finished", TargetType.WORK_ITEM, item.id, {})
    elif action == ClientAction.BLOCKED:
        for act in await _activities(ctx.session, ctx.subject.person_id, item_id=item.id):
            if act.status != ActivityStatus.ENDED and act.kind != ActivityKind.BLOCKED:
                _end(ctx, act)
        await _pause_all_active(ctx)
        activity = await _new_activity(ctx, item, ActivityKind.BLOCKED, note=msg.note)
        ctx.emit(
            "activity.blocked",
            TargetType.ACTIVITY,
            activity.id,
            {"work_item_id": item.id, "reason": msg.note},
        )
        ctx.emit("item.blocked", TargetType.WORK_ITEM, item.id, {"reason": msg.note})
    elif action == ClientAction.NOTE:
        activity = await _evidence_target(ctx, item)
        _add_evidence(ctx, activity, item, kind=EvidenceKind.NOTE, title=None, body=msg.note, url=None)
    elif action == ClientAction.EVIDENCE:
        activity = await _evidence_target(ctx, item)
        for entry in msg.evidence or []:
            _add_evidence(
                ctx,
                activity,
                item,
                kind=_parse_evidence_kind(entry.get("kind")),
                title=entry.get("title"),
                body=entry.get("body"),
                url=entry.get("url"),
            )
    # estimate / plan / log_time / create / update are handled by the field steps below
    if activity is None:
        activity = await _current_activity(ctx, item)
    return activity


async def apply_fields(ctx: Ctx, item: m.WorkItem, activity: m.Activity | None) -> None:
    """Step 7: estimate, planned_end, minutes — honoured for any action."""
    msg = ctx.msg
    if msg.estimate:
        try:
            value = Decimal(str(msg.estimate.get("value")))
            unit = EstimationUnit(str(msg.estimate.get("unit", "hours")))
            est = await items.record_estimate(
                ctx.uow, item, person_id=ctx.subject.person_id, value=value, unit=unit
            )
            ctx.emit(
                "estimate.set",
                TargetType.WORK_ITEM,
                item.id,
                {
                    "after": {
                        "estimate_id": est.id,
                        "kind": est.kind.value,
                        "value": str(value),
                        "unit": unit.value,
                    }
                },
            )
        except (InvalidOperation, ValueError):
            ctx.warnings.append("estimate_ignored")
    if msg.planned_end:
        local = aware(msg.planned_end).astimezone(zone(ctx.subject_person.time_zone)).date()
        before = item.planned_end
        item.planned_end = local
        ctx.emit(
            "item.planned_end_set",
            TargetType.WORK_ITEM,
            item.id,
            {
                "before": {"planned_end": before.isoformat() if before else None},
                "after": {"planned_end": local.isoformat()},
            },
        )
    if msg.minutes is not None:
        if msg.minutes <= 0:
            ctx.warnings.append("minutes_ignored")
        else:
            day: date = aware(ctx.at).astimezone(zone(ctx.subject_person.time_zone)).date()
            te = m.TimeEntry(
                id=new_id(),
                person_id=ctx.subject.person_id,
                work_item_id=item.id,
                date=day,
                minutes=msg.minutes,
                activity_id=activity.id if activity else None,
                source=CaptureSource.MANUAL,
            )
            ctx.session.add(te)
            ctx.emit(
                "time.logged",
                TargetType.WORK_ITEM,
                item.id,
                {"after": {"time_entry_id": te.id, "minutes": msg.minutes, "date": day.isoformat()}},
            )
    elif msg.action == ClientAction.LOG_TIME:
        ctx.warnings.append("log_time_without_minutes")


# ---------------------------------------------------------------- overlay hints


async def _resolve_ref(ctx: Ctx, ref: ClientTaskRef, project_id: str) -> m.WorkItem | None:
    found = await _find_item(ctx, ref)
    if found or not ref.title:
        return found
    return await ctx.session.scalar(
        select(m.WorkItem).where(
            m.WorkItem.project_id == project_id,
            func.lower(m.WorkItem.title) == ref.title.lower(),
            m.WorkItem.deleted_at.is_(None),
        )
    )


async def apply_hints(ctx: Ctx, item: m.WorkItem, project: m.Project) -> None:
    msg, session = ctx.msg, ctx.session
    changed: list[str] = []

    async def low(name: str, value: Any) -> bool:
        if ctx.conf_ok(name):
            return False
        await add_review(
            ctx,
            "low_confidence",
            item.id,
            {"field": name, "value": value},
            (msg.llm_confidence or {}).get(name),
        )
        return True

    if msg.tags and not await low("tags", msg.tags):
        existing = {
            t
            for t in (
                await session.scalars(
                    select(m.TagLink.tag_id).where(
                        m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id == item.id
                    )
                )
            ).all()
        }
        for name in msg.tags:
            tag, new = await items.get_or_create_tag(
                ctx.uow, ctx.ws, name.strip().lstrip("#"), provisional=True
            )
            if new:
                await add_review(ctx, "provisional_tag", item.id, {"tag_id": tag.id, "name": tag.name})
                ctx.emit(
                    "tag.created",
                    TargetType.WORK_ITEM,
                    item.id,
                    {"after": {"tag_id": tag.id, "name": tag.name}},
                )
            if tag.id not in existing:
                session.add(
                    m.TagLink(id=new_id(), tag_id=tag.id, target_type=TargetType.WORK_ITEM, target_id=item.id)
                )
                ctx.emit("tag.linked", TargetType.WORK_ITEM, item.id, {"after": {"tag_id": tag.id}})
                existing.add(tag.id)
    if msg.priority and not await low("priority", msg.priority.value):
        if item.priority != msg.priority:
            item.priority, item.priority_source = msg.priority, ValueSource.RULE
            ctx.emit(
                "item.priority_changed",
                TargetType.WORK_ITEM,
                item.id,
                {"after": {"priority": msg.priority.value}},
            )
            changed.append("priority")
    if msg.item_type and not await low("item_type", msg.item_type.value):
        t = await item_type(session, ctx.ws.id, ItemTypeKey(msg.item_type), project.id)
        if t.id != item.type_id:
            item.type_id = t.id
            changed.append("type_id")
    if msg.parent and not await low("parent", msg.parent.model_dump()):
        parent = await _resolve_ref(ctx, msg.parent, project.id)
        if parent is None or parent.id == item.id:
            await add_review(
                ctx, "unresolved_parent", item.id, {"parent": msg.parent.model_dump(exclude_none=True)}
            )
        else:
            try:
                await items.validate_nesting(session, ctx.ws, item.type_id, parent)
                await items.move_item(session, item, parent)
                ctx.emit(
                    "item.parent_changed", TargetType.WORK_ITEM, item.id, {"after": {"parent_id": parent.id}}
                )
            except Exception:  # invalid nesting / cycle -> review, never block
                await add_review(ctx, "invalid_parent", item.id, {"parent": parent.key})
    for field_name, model, fk in (
        ("milestone", m.Milestone, "milestone_id"),
        ("iteration", m.Iteration, "iteration_id"),
    ):
        value = getattr(msg, field_name)
        if value and not await low(field_name, value):
            row = await session.scalar(
                select(model).where(
                    model.project_id == project.id,
                    func.lower(model.name) == value.lower(),
                    model.deleted_at.is_(None),
                )
            )
            if row is None:
                await add_review(ctx, f"unknown_{field_name}", item.id, {field_name: value})
            else:
                setattr(item, fk, row.id)
                changed.append(fk)
    if msg.assignees and not await low("assignees", msg.assignees):
        for entry in msg.assignees:
            handle = entry.get("person", "")
            member = await _member_by_handle(session, ctx.ws.id, handle) if handle else None
            if member is None:
                await add_review(
                    ctx, "unknown_person", item.id, {"person": handle, "role": entry.get("role")}
                )
                continue
            try:
                role = AssignmentRole(entry.get("role", "contributor"))
            except ValueError:
                role = AssignmentRole.CONTRIBUTOR
            current = await session.scalar(
                select(m.Assignment).where(
                    m.Assignment.work_item_id == item.id,
                    m.Assignment.person_id == member.person_id,
                    m.Assignment.role == role,
                    m.Assignment.unassigned_at.is_(None),
                )
            )
            if current:
                continue
            if role == AssignmentRole.OWNER:
                for old in (
                    await session.scalars(
                        select(m.Assignment).where(
                            m.Assignment.work_item_id == item.id,
                            m.Assignment.role == AssignmentRole.OWNER,
                            m.Assignment.unassigned_at.is_(None),
                        )
                    )
                ).all():
                    old.unassigned_at = ctx.at
                item.assignee_id = member.person_id
                changed.append("assignee_id")
            session.add(
                m.Assignment(
                    id=new_id(),
                    work_item_id=item.id,
                    person_id=member.person_id,
                    role=role,
                    assigned_at=ctx.at,
                )
            )
    if msg.custom_fields and not await low("custom_fields", list(msg.custom_fields)):
        for key, value in msg.custom_fields.items():
            definition = await session.scalar(
                select(m.CustomFieldDefinition).where(
                    m.CustomFieldDefinition.workspace_id == ctx.ws.id,
                    m.CustomFieldDefinition.key == key,
                    or_(
                        m.CustomFieldDefinition.project_id.is_(None),
                        m.CustomFieldDefinition.project_id == project.id,
                    ),
                    m.CustomFieldDefinition.deleted_at.is_(None),
                )
            )
            if definition is None:
                await add_review(ctx, "unknown_custom_field", item.id, {"key": key, "value": value})
                continue
            row = m.CustomFieldValue(id=new_id(), work_item_id=item.id, field_definition_id=definition.id)
            ft = definition.field_type.value
            try:
                if ft == "number":
                    row.value_number = Decimal(str(value))
                elif ft == "date":
                    row.value_date = date.fromisoformat(str(value))
                elif ft in ("text", "url"):
                    row.value_text = str(value)
                else:
                    row.value_json = value
            except (InvalidOperation, ValueError):
                await add_review(ctx, "invalid_custom_field", item.id, {"key": key, "value": value})
                continue
            session.add(row)
    if msg.stage and not await low("stage", msg.stage):
        process = await proc.get_process(session, ctx.ws.id)
        stage = next(
            (
                st
                for st in await proc.stages_of(session, process.id)
                if st.key == msg.stage.lower() or st.name.lower() == msg.stage.lower()
            ),
            None,
        )
        if stage is None:
            await add_review(ctx, "deferred_stage", item.id, {"stage": msg.stage})
        else:  # applied, but unconfirmed: a manager confirms it in the review queue
            await proc.place(
                ctx.uow,
                ctx.ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item.id,
                process=process,
                stage=stage,
                moved_by=ctx.actor.person_id,
                reason="reporter hint",
                source=EventSource.CLIENT,
                strict=False,
                confirmed=False,
                correlation_id=ctx.correlation_id,
            )
    if msg.product and not await low("product", msg.product):
        product = await session.scalar(
            select(m.Product).where(
                m.Product.workspace_id == ctx.ws.id,
                m.Product.deleted_at.is_(None),
                or_(
                    func.lower(m.Product.key) == msg.product.lower(),
                    func.lower(m.Product.name) == msg.product.lower(),
                ),
            )
        )
        if product is None:
            await add_review(ctx, "deferred_product", item.id, {"product": msg.product})
        else:
            dims = await ensure_builtin_dimensions(session, ctx.ws.id)
            value = await ensure_value(
                session,
                dims["product"],
                backing_type="product",
                backing_id=product.id,
                key=product.key,
                name=product.name,
                parent_backing_id=product.parent_product_id,
            )
            await classify_svc.classify(
                ctx.uow,
                ctx.ws,
                target_type=TargetType.WORK_ITEM,
                target_id=item.id,
                dimension=dims["product"],
                value=value,
                source=ValueSource.RULE,
                set_by=ctx.actor.person_id,
                is_confirmed=False,
                confidence=(msg.llm_confidence or {}).get("product"),
                emit_event=False,
            )
    if msg.risks and not await low("risks", msg.risks):
        unresolved = []
        for rk in msg.risks:
            risk = await session.scalar(
                select(m.Risk)
                .join(m.Project, m.Project.id == m.Risk.project_id)
                .where(
                    m.Project.workspace_id == ctx.ws.id,
                    func.lower(m.Risk.key) == rk.lower(),
                    m.Risk.deleted_at.is_(None),
                )
            )
            if risk is None:
                unresolved.append(rk)
            else:
                await risks_svc.link_item(ctx.uow, ctx.ws, risk, item, actor_id=ctx.actor.person_id)
        if unresolved:
            await add_review(ctx, "deferred_risks", item.id, {"risks": unresolved})
    if changed:
        ctx.emit("item.updated", TargetType.WORK_ITEM, item.id, {"fields_changed": sorted(set(changed))})


# ---------------------------------------------------------------- one message


@dataclass
class MessageResult:
    client_id: str
    status: str
    item: dict[str, Any] | None = None
    activity_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    review_item_ids: list[str] = field(default_factory=list)
    error_code: str | None = None
    resolution: dict[str, Any] = field(default_factory=dict)
    jira: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "status": self.status,
            "item": self.item,
            "activity_id": self.activity_id,
            "warnings": self.warnings,
            "review_item_ids": self.review_item_ids,
            "error_code": self.error_code,
            "resolution": self.resolution,
            "jira": self.jira,
        }


async def _process(
    uow: UnitOfWork,
    ws: m.Workspace,
    msg: ClientMessage,
    client_version: str,
    correlation_id: str,
    mode: str | None = None,
) -> MessageResult:
    session = uow.session
    dup = await session.scalar(
        select(m.Event).where(m.Event.client_id == msg.client_id, m.Event.workspace_id == ws.id)
    )
    if dup is not None:
        stored = dict((dup.payload or {}).get("result") or {})
        stored.update(client_id=msg.client_id, status="deduplicated", deduplicated=True)
        return MessageResult(**{k: v for k, v in stored.items() if k in MessageResult.__dataclass_fields__})
    actor = await _member_by_handle(session, ws.id, msg.actor)
    if actor is None:
        return MessageResult(msg.client_id, "failed", error_code="unknown_actor")
    actor_person = await session.get(m.Person, actor.person_id)
    subject, warnings = actor, []
    if msg.on_behalf_of:
        target = await _member_by_handle(session, ws.id, msg.on_behalf_of)
        if target and await _can_act_for(session, ws.id, actor.person_id, target.person_id):
            subject = target
        else:
            warnings.append("on_behalf_of_ignored")
    subject_person = await session.get(m.Person, subject.person_id)
    assert actor_person and subject_person  # noqa: S101
    ctx = Ctx(
        uow=uow,
        ws=ws,
        msg=msg,
        client_version=client_version,
        correlation_id=correlation_id,
        actor=actor,
        subject=subject,
        actor_person=actor_person,
        subject_person=subject_person,
        at=aware(msg.at),
        warnings=warnings,
    )
    uow.context.update(actor_id=actor.person_id, correlation_id=correlation_id)

    project = await resolve_project(ctx)
    item, info = await resolve_item(ctx, project)
    created = item is None
    if (
        item is None
        and msg.item is None
        and msg.action
        in {
            ClientAction.PAUSE,
            ClientAction.RESUME,
            ClientAction.DONE,
            ClientAction.NOTE,
            ClientAction.EVIDENCE,
            ClientAction.LOG_TIME,
        }
    ):
        current = await _current_activity(ctx, None)
        if current:
            item = await session.get(m.WorkItem, current.work_item_id)
            info = {"method": "matched", "by": "current_activity", "score": 1.0}
            created = False
    if item is None:
        title = (
            (msg.item.title if msg.item and msg.item.title else None)
            or msg.note
            or msg.raw_input
            or "Untitled"
        )
        team = await session.scalar(
            select(m.Membership.team_id)
            .where(m.Membership.person_id == subject.person_id, m.Membership.left_at.is_(None))
            .order_by(m.Membership.joined_at)
        )
        item = await items.create_item(
            uow,
            ws,
            project,
            title=title[:500],
            reporter_id=actor.person_id,
            assignee_id=subject.person_id,
            team_id=team,
            provisional=project.kind.value == "inbox",
            source={"client": client_version, "raw": msg.raw_input},
        )
        ctx.emit(
            "item.created",
            TargetType.WORK_ITEM,
            item.id,
            {"after": {"key": item.key, "title": item.title, "project": project.key}},
        )
    else:
        ctx.emit(
            "item.matched", TargetType.WORK_ITEM, item.id, {"after": {"key": item.key}, "resolution": info}
        )
    ctx.resolution["item"] = info

    activity = await apply_action(ctx, item, created)
    await apply_fields(ctx, item, activity)
    if created and ctx.resolution["project"].get("unresolved"):
        await add_review(
            ctx, "unclassified_project", item.id, {"phrase": ctx.resolution["project"]["unresolved"]}
        )
    await apply_hints(ctx, item, project)
    await sync_item_mirrors(uow, ws, item, set_by=actor.person_id)
    await weights.recompute_item(uow, ws, item, actor_id=actor.person_id)
    await session.flush()

    result = MessageResult(
        msg.client_id,
        "applied_with_warnings" if ctx.warnings else "applied",
        item={"id": item.id, "key": item.key, "title": item.title, "project_key": project.key},
        activity_id=activity.id if activity else None,
        warnings=ctx.warnings,
        review_item_ids=ctx.review_ids,
        resolution=ctx.resolution,
    )
    extra: dict[str, Any] = {}
    if mode:
        extra["mode"] = mode
    if msg.jira:  # the push itself is a job (Phase 10); a `send` opts this item in
        extra["jira"] = msg.jira
        if msg.jira.get("send"):
            item.external_refs = {**(item.external_refs or {}), "jira_opt_in": "true"}
            result.jira = {"status": "deferred"}
    primary = ctx.events[0]
    primary.client_id = msg.client_id
    primary.payload = {**primary.payload, **extra, "result": result.as_dict()}
    return result


async def _inline_jira_push(session_factory: async_sessionmaker[AsyncSession], result: MessageResult) -> None:
    """A reporter who asked for Jira gets the key back when Jira answers within a short bound; otherwise the queued job finishes it."""
    from tt_server.integrations.jira import sync as jira_sync
    from tt_server.settings import get_settings

    st = get_settings()
    if not (st.features.jira_sync and result.jira and result.item and st.jira_inline_timeout_s > 0):
        return
    res = await jira_sync.push_bounded(session_factory, result.item["id"], st.jira_inline_timeout_s)
    if res is None:
        return
    if res.status in ("pushed", "unchanged") and res.key:
        result.jira = {"status": "pushed", "key": res.key}
    elif res.status == "failed":
        result.jira = {"status": "failed", "error": res.reason}


async def ingest_batch(
    session_factory: async_sessionmaker[AsyncSession],
    ws_id: str,
    batch: ClientBatch,
    batch_id: str | None = None,
    agent_run_id: str | None = None,
) -> list[MessageResult]:
    """One transaction per message; a failing message never fails the batch."""
    batch_id = batch_id or new_id()
    results: list[MessageResult] = []
    for msg in batch.messages:
        for attempt in (1, 2):
            try:
                uow = UnitOfWork(session_factory)
                async with uow:
                    if agent_run_id:  # written on behalf of the LLM gateway: every event carries the run id
                        uow.context["agent_run_id"] = agent_run_id
                    ws = await uow.session.get(m.Workspace, ws_id)
                    assert ws is not None  # noqa: S101
                    result = await _process(uow, ws, msg, batch.client_version, batch_id, batch.mode)
                    if result.status == "failed":
                        results.append(result)
                        break
                    await uow.commit()
                    results.append(result)
                await _inline_jira_push(session_factory, result)
                break
            except IntegrityError:
                if (
                    attempt == 1
                ):  # a concurrent delivery of the same client_id won the race: the retry sees it and deduplicates
                    continue
                log.warning("capture_message_failed", client_id=msg.client_id, error="integrity")
                results.append(MessageResult(msg.client_id, "failed", error_code="internal_error"))
            except Exception as exc:
                log.warning("capture_message_failed", client_id=msg.client_id, error=repr(exc))
                results.append(MessageResult(msg.client_id, "failed", error_code="internal_error"))
                break
    return results
