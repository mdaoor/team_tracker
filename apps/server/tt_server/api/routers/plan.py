"""Top-down project plan API (plan P1 §1.2): one transaction to create, one read for the editor and the roadmap slides."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tt_server.api.authz import check_project, needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.routers.core import _project
from tt_server.domain.plan import linkage
from tt_server.domain.plan import service as plan

_ = linkage  # imported for its hook registration

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["plan"])


@router.post("/projects/{key}/plan", status_code=201, dependencies=[needs("project.write", project="key")])
async def create_plan(key: str, body: plan.PlanIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    p = await _project(uow, actor.ws.id, key)
    uow.context["actor_id"] = actor.person.id
    out = await plan.create_plan(uow, actor.ws, p, body, actor_id=actor.person.id)
    await uow.commit()
    return out


@router.get("/projects/{key}/plan")
async def get_plan(key: str, actor: CurrentActor, uow: Uow, as_of: str | None = None) -> dict[str, Any]:
    from datetime import date

    p = await _project(uow, actor.ws.id, key)
    await check_project(actor, uow, "item.read", p)
    today = date.fromisoformat(as_of) if as_of else plan.today_in(actor.person.time_zone)
    return await plan.get_plan(uow.session, p, today=today, acl=actor.acl)
