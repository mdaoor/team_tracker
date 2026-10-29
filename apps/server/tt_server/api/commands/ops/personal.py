"""Ops open to any role: context notes and the daily log. (Phase 14)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import (
    ScopeType,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    core,
    core_tenancy,
)
from tt_server.db import models as m
from tt_server.domain.errors import NotFound

# ------------------------------------------------------------------ personal (any role)


class ContextNoteAdd(BaseModel):
    title: str
    body: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope: str | None = Field(default=None, description="project key / team slug the note applies to")
    pinned: bool = False


@command(
    "context_note.add", "personal", "Add a context note for a project, team or the workspace", ContextNoteAdd, permission="context_note.write", modes=("manager", "coach", "pm", "admin", "scrum_master"),
    example_args={"title": "Freeze", "body": "Code freeze from Dec 1", "scope_type": "workspace"}, example_text="note: code freeze from Dec 1",
)  # fmt: skip
async def context_note_add(ctx: Ctx, a: ContextNoteAdd) -> Outcome:
    sid = ctx.actor.ws.id
    if a.scope_type == ScopeType.PROJECT and a.scope:
        sid = (await core._project(ctx.uow, ctx.actor.ws.id, a.scope)).id
    elif a.scope_type == ScopeType.TEAM and a.scope:
        sid = (await _team(ctx, a.scope)).id
    body = core_tenancy.ContextNoteIn(
        scope_type=a.scope_type, scope_id=sid, title=a.title, body=a.body, pinned=a.pinned
    )
    return _res(await core_tenancy.create_note(body, ctx.actor, ctx.uow))


class DailyLogConfirm(BaseModel):
    day: date | None = None


@command(
    "dailylog.confirm", "personal", "Confirm your daily log", DailyLogConfirm, permission=None, modes=("reporter", "manager", "scrum_master", "coach", "pm", "admin"),
    example_args={}, example_text="confirm my day",
)  # fmt: skip
async def dailylog_confirm(ctx: Ctx, a: DailyLogConfirm) -> Outcome:
    from tt_server.api.routers import capture
    from tt_server.domain.capture import dailylog

    day = a.day or utcnow().date()
    row = await ctx.uow.session.scalar(select(m.DailyLog).where(m.DailyLog.workspace_id == ctx.actor.ws.id, m.DailyLog.person_id == ctx.actor.person.id, m.DailyLog.date == day))  # fmt: skip
    if row is None:
        raise NotFound("not_found", f"no daily log for {day}", pointer="/args/day")
    _ = capture
    await dailylog.confirm(ctx.uow, ctx.actor.ws, row, ctx.actor.person.id)
    return Outcome(result={"id": row.id, "date": day.isoformat(), "status": "confirmed"})
