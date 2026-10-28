"""Ingestion actions: activities, time and evidence, and applying the message's action and fields to the item."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.client import ClientAction
from tt_schema.enums import (
    ActivityKind,
    ActivityStatus,
    CaptureSource,
    EstimationUnit,
    EvidenceKind,
    StateCategory,
    TargetType,
)

from tt_server.db import models as m
from tt_server.domain.capture.ingest_context import Ctx
from tt_server.domain.capture.ingest_resolve import calendar_key, state_category
from tt_server.domain.common import aware, zone
from tt_server.domain.items import service as items
from tt_server.domain.plan import linkage
from tt_server.domain.projects.service import pick_state, workflow_states

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


async def current_activity(ctx: Ctx, item: m.WorkItem | None) -> m.Activity | None:
    """The subject's activity on `item` (a live one first, else the latest); without an item, their only active activity, or None when there are none or several."""
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
    act = await current_activity(ctx, item)
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
    """Apply the message's lifecycle action (start, pause, done) to the item and its activities."""
    msg, action = ctx.msg, ctx.msg.action
    activity: m.Activity | None = None
    if action == ClientAction.START:
        await _pause_all_active(ctx)
        activity = await _new_activity(ctx, item, ActivityKind.WORKING)
        ctx.emit("activity.started", TargetType.ACTIVITY, activity.id, {"work_item_id": item.id})
        if item.actual_start is None:
            item.actual_start = ctx.at
        cat = await state_category(ctx.session, item.state_id)
        if cat == StateCategory.DONE:
            await _move_state(ctx, item, StateCategory.ACTIVE)
        elif cat == StateCategory.OPEN:
            await _move_state(ctx, item, StateCategory.ACTIVE)
    elif action == ClientAction.PAUSE:
        activity = await current_activity(ctx, item if not created or msg.item else None)
        if activity and activity.status == ActivityStatus.ACTIVE:
            activity.status = ActivityStatus.PAUSED
            ctx.emit(
                "activity.paused",
                TargetType.ACTIVITY,
                activity.id,
                {"reason": "manual", "work_item_id": item.id},
            )
    elif action == ClientAction.RESUME:
        activity = await current_activity(ctx, item)
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
        activity = await current_activity(ctx, item)
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
        await linkage.plan_changed(ctx.uow, ctx.ws, item)
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
                workspace_id=ctx.ws.id,
                client_id=msg.client_id,
                note=msg.note if msg.action == ClientAction.LOG_TIME else None,
            )
            ckey = calendar_key(msg)
            if ckey:
                te.calendar_provider, te.calendar_event_id, te.calendar_instance_start = ckey
            ctx.session.add(te)
            ctx.emit(
                "time.logged",
                TargetType.WORK_ITEM,
                item.id,
                {
                    "after": {"time_entry_id": te.id, "minutes": msg.minutes, "date": day.isoformat()}
                    | (
                        {"calendar_ref": msg.source.calendar_ref.model_dump(mode="json")}
                        if ckey and msg.source and msg.source.calendar_ref
                        else {}
                    ),
                },
            )
    elif msg.action == ClientAction.LOG_TIME:
        ctx.warnings.append("log_time_without_minutes")
