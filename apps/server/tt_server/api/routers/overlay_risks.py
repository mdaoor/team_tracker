"""Overlay endpoints: the risk register and approvals (Phase 3)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from tt_schema.enums import ApprovalStatus, Decision, RiskCategory, RiskResponse, RiskStatus, TargetType

from tt_server.api.authz import check_project
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.overlay_support import PREFIX, begin
from tt_server.api.routers.core import _item, _project
from tt_server.db import models as m
from tt_server.domain.common import row_dict
from tt_server.domain.overlay import approvals, risk_register, risks

router = APIRouter(prefix=PREFIX)


class RiskIn(BaseModel):
    project: str
    title: str
    target_type: TargetType = TargetType.PROJECT
    target_id: str | None = None
    category: RiskCategory = RiskCategory.TECHNICAL
    probability: int = 3
    impact: int = 3
    response_strategy: RiskResponse = RiskResponse.MITIGATE
    description: str | None = None
    owner_id: str | None = None


def _scored(r: m.Risk) -> dict[str, Any]:
    return {**row_dict(r), "score": r.probability * r.impact}


async def _writable_risk(uow: Uow, actor: CurrentActor, ref: str) -> m.Risk:
    """A visible risk the actor may write (`risk.write` on its project)."""
    r = await risk_register.get_risk(uow, actor.ws.id, ref)
    await check_project(actor, uow, "risk.write", await risk_register.project_of(uow, r))
    return r


@router.post("/risks", status_code=201, tags=["overlay"])
async def create_risk(body: RiskIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    p = await _project(uow, actor.ws.id, body.project)
    await check_project(actor, uow, "risk.write", p)
    tid = body.target_id
    if tid and body.target_type == TargetType.WORK_ITEM:
        tid = (await _item(uow, actor.ws.id, tid)).id
    r = await risks.create_risk(
        uow,
        actor.ws,
        p,
        title=body.title,
        owner_id=body.owner_id or actor.person.id,
        actor_id=actor.person.id,
        target_type=body.target_type,
        target_id=tid,
        category=body.category,
        probability=body.probability,
        impact=body.impact,
        response_strategy=body.response_strategy,
        description=body.description,
    )
    await uow.commit()
    return _scored(r)


@router.get("/risks", tags=["overlay"])
async def list_risks(
    actor: CurrentActor, uow: Uow, project: str | None = None, open_only: bool = False
) -> dict[str, Any]:
    rows = await risk_register.list_risks(uow, actor.ws.id, actor.acl, project=project, open_only=open_only)
    return {"data": [{**_scored(r), "project_key": k} for r, k in rows], "next_cursor": None}


class RiskPatch(BaseModel):
    status: RiskStatus | None = None
    probability: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    title: str | None = None
    review_date: date | None = None


@router.patch("/risks/{ref}", tags=["overlay"])
async def patch_risk(ref: str, body: RiskPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    r = await _writable_risk(uow, actor, ref)
    risk_register.patch_risk(uow, actor.ws, actor.person.id, r, body.model_dump(exclude_unset=True))
    await uow.commit()
    return _scored(r)


class RiskLinkIn(BaseModel):
    item: str


@router.post("/risks/{ref}/links", status_code=201, tags=["overlay"])
async def link_risk(ref: str, body: RiskLinkIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    r = await _writable_risk(uow, actor, ref)
    item = await _item(uow, actor.ws.id, body.item)
    n = await risks.link_item(uow, actor.ws, r, item, actor_id=actor.person.id)
    await uow.commit()
    return {"risk": r.key, "item": item.key, "inherited_links_created": n}


@router.delete("/risks/{ref}/links/{item_ref}", tags=["overlay"])
async def unlink_risk(ref: str, item_ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    r = await _writable_risk(uow, actor, ref)
    item = await _item(uow, actor.ws.id, item_ref)
    n = await risks.unlink_item(uow, actor.ws, r, item, actor_id=actor.person.id)
    await uow.commit()
    return {"risk": r.key, "item": item.key, "inherited_links_removed": n}


class ActionIn(BaseModel):
    description: str
    due_date: date | None = None


@router.post("/risks/{ref}/actions", status_code=201, tags=["overlay"])
async def add_risk_action(ref: str, body: ActionIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    r = await _writable_risk(uow, actor, ref)
    a = await risks.add_action(
        uow, actor.ws, r, description=body.description, actor_id=actor.person.id, due_date=body.due_date
    )
    await uow.commit()
    return row_dict(a)


@router.post("/risks/{ref}/actions/{aid}/create-item", status_code=201, tags=["overlay"])
async def action_to_item(ref: str, aid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    r = await _writable_risk(uow, actor, ref)
    item = await risk_register.action_to_item(uow, actor.ws, actor.person.id, r, aid)
    await uow.commit()
    return row_dict(item)


# ------------------------------------------------------------------ approvals


@router.get("/approvals", tags=["overlay"])
async def list_approvals(
    actor: CurrentActor, uow: Uow, status: ApprovalStatus | None = None, mine: bool = False
) -> dict[str, Any]:
    rows = await approvals.list_approvals(
        uow, actor.ws.id, actor.acl, actor.person.id, status=status, mine=mine
    )
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class DecisionIn(BaseModel):
    decision: Decision
    comment: str | None = None


@router.post("/approvals/{aid}/decisions", status_code=201, tags=["overlay"])
async def decide(aid: str, body: DecisionIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    a = await approvals.get_approval(uow, actor.ws.id, aid)
    await check_project(actor, uow, "approval.decide", await approvals.project_of(uow, a))
    await approvals.decide(
        uow, actor.ws, a, approver_id=actor.person.id, decision=body.decision, comment=body.comment
    )
    await uow.commit()
    return row_dict(a)
