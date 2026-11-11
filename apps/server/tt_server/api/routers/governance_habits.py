"""Habit, metric snapshot, notification and admin-sweep endpoints (Phase 8)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import HabitStatus, ScopeType

from tt_server.acl.visibility import visible
from tt_server.api.authz import check
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain.common import emit, emit_admin, row_dict
from tt_server.domain.names import Names
from tt_server.governance import habits, runner, snapshots
from tt_server.governance.violations import parse_enum

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["governance"])


def habit_view(h: m.Habit, handle: str | None, actor: Actor | None = None) -> dict[str, Any]:
    """A habit row with its subject's handle; coaching notes are manager-only (ADR-0014)."""
    d = row_dict(h)
    if actor is not None and (h.subject_id == actor.person.id or not actor.acl.can("habit.coach")):
        d.pop("coaching_note", None)  # the subject sees status, not the note
    d["subject"] = handle or h.subject_id
    return d


@router.get("/habit-definitions")
async def habit_definitions(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.HabitDefinition)
            .where((m.HabitDefinition.workspace_id == actor.ws.id) | m.HabitDefinition.workspace_id.is_(None))
            .order_by(m.HabitDefinition.key)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.get("/habits")
async def list_habits(
    actor: CurrentActor,
    uow: Uow,
    subject: str | None = None,
    key: str | None = None,
    status: str | None = None,
    window: str | None = None,
) -> dict[str, Any]:
    stmt = visible(
        select(m.Habit).where(m.Habit.workspace_id == actor.ws.id, m.Habit.deleted_at.is_(None)),
        actor.acl,
        m.Habit,
    )
    if key:
        stmt = stmt.where(m.Habit.key == key)
    if status:
        stmt = stmt.where(m.Habit.status == parse_enum(HabitStatus, status, "status"))
    if window:
        if not re.fullmatch(r"\d{1,4}[dw]", window):
            raise ApiError(400, "invalid_window", "window looks like 30d or 12w", pointer="/window")
        stmt = stmt.where(m.Habit.window == window)
    handles = {
        pid: h
        for pid, h in (
            await uow.session.execute(
                select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id
                )
            )
        ).all()
    }
    if subject:
        want = subject if subject.startswith("@") else "@" + subject
        stmt = stmt.where(m.Habit.subject_id.in_([p for p, h in handles.items() if h == want]))
    rows = (await uow.session.scalars(stmt.order_by(m.Habit.key, m.Habit.subject_id))).all()
    names = await Names.load(uow.session, actor.ws.id, [(h.subject_type, h.subject_id) for h in rows])
    return {
        "data": [
            habit_view(h, handles.get(h.subject_id) or names.label(h.subject_type, h.subject_id), actor)
            for h in rows
        ],
        "next_cursor": None,
    }


@router.get("/habits/{hid}/series")
async def habit_series(
    hid: str, actor: CurrentActor, uow: Uow, points: int = Query(8, ge=2, le=26)
) -> dict[str, Any]:
    """Sparkline data. Visibility is the habit's own (a wellbeing habit is as hidden here as in the list)."""
    row = await uow.session.scalar(
        visible(
            select(m.Habit).where(
                m.Habit.id == hid, m.Habit.workspace_id == actor.ws.id, m.Habit.deleted_at.is_(None)
            ),
            actor.acl,
            m.Habit,
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "habit not found")
    return {"data": await habits.series(uow.session, actor.ws.id, row, points=points), "next_cursor": None}


class HabitPatch(BaseModel):
    status: HabitStatus | None = None
    coaching_note: str | None = None


@router.patch("/habits/{hid}")
async def patch_habit(hid: str, body: HabitPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    h = await uow.session.scalar(
        visible(
            select(m.Habit).where(m.Habit.id == hid, m.Habit.workspace_id == actor.ws.id), actor.acl, m.Habit
        )
    )
    if h is None:
        raise ApiError(404, "not_found", "habit not found")
    check(actor, "habit.coach")
    before = h.status
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(h, k, v)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=h.subject_type,
        entity_id=h.subject_id,
        event_type="habit.status_changed",
        payload={
            "after": {
                "habit": h.key,
                "before": before.value,
                "status": h.status.value,
                "note": bool(data.get("coaching_note")),
            }
        },
    )
    await uow.commit()
    return habit_view(h, None, actor)


# ---------------------------------------------------------------- metrics, notifications, operations
@router.get("/metric-snapshots")
async def metric_snapshots(
    actor: CurrentActor, uow: Uow, scope_type: ScopeType, scope_id: str, from_: Annotated[date | None, Query(alias="from")] = None, to: date | None = None, scheme: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    data = await snapshots.metric_series(
        uow,
        actor.ws,
        actor.acl,
        actor.person.id,
        scope_type=scope_type,
        scope_id=scope_id,
        from_=from_,
        to=to,
        scheme=scheme,
    )
    return {"data": data, "next_cursor": None}


@router.get("/notifications/mine")
async def my_notifications(actor: CurrentActor, uow: Uow, unread: bool = False) -> dict[str, Any]:
    stmt = select(m.Notification).where(
        m.Notification.workspace_id == actor.ws.id, m.Notification.person_id == actor.person.id
    )
    if unread:
        stmt = stmt.where(m.Notification.read_at.is_(None))
    rows = (await uow.session.scalars(stmt.order_by(m.Notification.created_at.desc()).limit(200))).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/notifications/{nid}:read")
async def read_notification(nid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    n = await uow.session.scalar(
        select(m.Notification).where(m.Notification.id == nid, m.Notification.person_id == actor.person.id)
    )
    if n is None:
        raise ApiError(404, "not_found", "notification not found")
    n.read_at = utcnow()
    emit_admin(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        event_type="notification.read",
        payload={"after": {"notification": nid}},
    )
    await uow.commit()
    return row_dict(n)


@router.post("/governance:recompute")
async def recompute(
    request: Request, actor: CurrentActor, uow: Uow, at: datetime | None = None
) -> dict[str, Any]:
    """Admin: sweep every active scheduled policy, then habits, metric snapshots and key results -- what the schedule does overnight."""
    check(actor, "policy.admin")
    maker = request.app.state.session_factory  # the runner opens its own units of work
    swept = await runner.run_workspace(maker, actor.ws.id, now=at)
    out = await runner.nightly(maker, actor.ws.id, now=at)
    return {"policies_swept": swept, **out}
