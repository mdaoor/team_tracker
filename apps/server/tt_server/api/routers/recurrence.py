"""Recurrence rules (plan P1): CRUD, preview, run-now. The 15-minute job does the materialising (`domain/recurrence.py`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import TargetType

from tt_server.api.authz import check_project, needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import _item, _project
from tt_server.db import models as m
from tt_server.domain import recurrence as rec
from tt_server.domain.common import emit_after, row_dict

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["recurrence"])


def _ev(uow: Uow, actor: Actor, rule: m.RecurrenceRule, event: str, **after: Any) -> None:
    emit_after(
        uow, workspace_id=actor.ws.id, actor_id=actor.person.id, entity_type=TargetType.PROJECT, entity_id=rule.project_id,
        event_type=event, rule=rule.id, **after,
    )  # fmt: skip


class RuleIn(BaseModel):
    template_item: str
    rrule: str = Field(min_length=5, max_length=500)
    time_zone: str = "UTC"
    assign_to: dict[str, Any] = Field(
        default_factory=dict
    )  # {"handle": "@someone"}; default: the template's assignee
    lead_days: int = Field(default=0, ge=0, le=60)


class RulePatch(BaseModel):
    rrule: str | None = Field(default=None, min_length=5, max_length=500)
    time_zone: str | None = None
    assign_to: dict[str, Any] | None = None
    lead_days: int | None = Field(default=None, ge=0, le=60)
    is_active: bool | None = None


def _view(r: m.RecurrenceRule) -> dict[str, Any]:
    return row_dict(r)


@router.post(
    "/projects/{key}/recurrence-rules", status_code=201, dependencies=[needs("project.write", project="key")]
)
async def create_rule(key: str, body: RuleIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    tpl = await _item(uow, actor.ws.id, body.template_item)
    if tpl.project_id != p.id:
        raise ApiError(
            400, "invalid_template", "the template item must belong to this project", pointer="/template_item"
        )
    now = utcnow()
    nxt = rec.first_run(body.rrule, body.time_zone, now)  # validates rule and zone
    r = m.RecurrenceRule(
        id=new_id(), project_id=p.id, template_item_id=tpl.id, rrule=body.rrule, time_zone=body.time_zone,
        assign_to=body.assign_to, lead_days=body.lead_days, next_run_at=nxt, is_active=nxt is not None, created_at=now,
    )  # fmt: skip
    uow.session.add(r)
    await uow.session.flush()
    _ev(uow, actor, r, "recurrence.created", rrule=r.rrule, next=nxt.isoformat() if nxt else None)
    await uow.commit()
    return _view(r)


@router.get("/projects/{key}/recurrence-rules")
async def list_rules(key: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    rows = await uow.session.scalars(
        select(m.RecurrenceRule)
        .where(m.RecurrenceRule.project_id == p.id, m.RecurrenceRule.deleted_at.is_(None))
        .order_by(m.RecurrenceRule.created_at)
    )
    return {"data": [_view(r) for r in rows.all()], "next_cursor": None}


async def _rule(uow: Uow, actor: Actor, rid: str) -> m.RecurrenceRule:
    r = await uow.session.scalar(
        select(m.RecurrenceRule).where(m.RecurrenceRule.id == rid, m.RecurrenceRule.deleted_at.is_(None))
    )
    p = await uow.session.get(m.Project, r.project_id) if r else None
    if r is None or p is None or p.workspace_id != actor.ws.id:
        raise ApiError(404, "not_found", "recurrence rule not found")

    await check_project(actor, uow, "project.write", p)
    return r


@router.patch("/recurrence-rules/{rid}")
async def patch_rule(rid: str, body: RulePatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    r = await _rule(uow, actor, rid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    if body.rrule is not None or body.time_zone is not None or body.is_active:
        found = rec.occurrences_after(
            r.rrule, r.time_zone, r.created_at, utcnow(), 1
        )  # same anchor as at creation
        nxt = found[0] if found else None
        r.next_run_at = nxt
        if nxt is None:
            r.is_active = False
    _ev(uow, actor, r, "recurrence.updated", **body.model_dump(exclude_unset=True))
    await uow.commit()
    return _view(r)


@router.delete("/recurrence-rules/{rid}", status_code=204)
async def delete_rule(rid: str, actor: CurrentActor, uow: Uow) -> Response:
    r = await _rule(uow, actor, rid)
    r.deleted_at, r.is_active = utcnow(), False
    _ev(uow, actor, r, "recurrence.deleted")
    await uow.commit()
    return Response(status_code=204)


class PreviewIn(BaseModel):
    rrule: str
    time_zone: str = "UTC"
    count: int = Field(default=5, ge=1, le=24)
    start: datetime | None = None


@router.post("/recurrence-rules:preview", dependencies=[needs("project.create")])
async def preview(body: PreviewIn, actor: CurrentActor) -> dict[str, Any]:
    start = body.start or utcnow()
    out = rec.occurrences_after(body.rrule, body.time_zone, start, start, body.count)
    return {"occurrences": [o.isoformat() for o in out]}
