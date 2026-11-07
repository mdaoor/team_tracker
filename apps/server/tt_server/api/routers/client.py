"""Endpoints serving reporter clients: ContextPack, glossary, translator rules, feedback, activities.

Thin HTTP layer: parse, authorise, call `domain.translator` / `domain.capture.activity_feed`, commit.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, Field
from tt_schema.enums import ActivityStatus

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.domain import translator as svc
from tt_server.domain.capture import activity_feed
from tt_server.domain.common import row_dict
from tt_server.domain.contextpack import build_pack, etag_of

router = APIRouter(prefix="/api/v1/w/{ws}")


@router.get("/context-pack", tags=["client"])
async def context_pack(
    actor: CurrentActor,
    uow: Uow,
    response: Response,
    if_none_match: str | None = Header(default=None),
    for_: str | None = Query(default=None, alias="for"),
) -> Any:
    member = await svc.pack_member(uow, actor.ws, actor.member, actor.acl.is_admin, for_)
    pack = await build_pack(uow.session, actor.ws, member)
    tag = etag_of(pack)
    headers = {"ETag": tag, "Cache-Control": "private, max-age=60"}
    if if_none_match and if_none_match.strip() == tag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return pack


# ------------------------------------------------------------------ glossary


class GlossaryIn(BaseModel):
    phrase: str = Field(min_length=1)
    resolves_to_type: Literal["project", "item", "person"]
    resolves_to_id: str
    project_id: str | None = None
    source: Literal["manual", "learned"] = "manual"


class GlossaryPatch(BaseModel):
    phrase: str | None = None
    resolves_to_type: Literal["project", "item", "person"] | None = None
    resolves_to_id: str | None = None


@router.get("/glossary", tags=["client"])
async def list_glossary(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {"data": await svc.list_glossary(uow, actor.ws.id), "next_cursor": None}


@router.post("/glossary", status_code=201, tags=["client"], dependencies=[needs("translator_rule.write")])
async def create_glossary(body: GlossaryIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await svc.create_glossary(uow, actor.ws.id, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(row)


@router.patch("/glossary/{entry_id}", tags=["client"], dependencies=[needs("translator_rule.write")])
async def patch_glossary(entry_id: str, body: GlossaryPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await svc.patch_glossary(
        uow, actor.ws.id, actor.person.id, entry_id, body.model_dump(exclude_none=True)
    )
    await uow.commit()
    return row_dict(row)


@router.delete(
    "/glossary/{entry_id}", status_code=204, tags=["client"], dependencies=[needs("translator_rule.write")]
)
async def delete_glossary(entry_id: str, actor: CurrentActor, uow: Uow) -> Response:
    await svc.delete_glossary(uow, actor.ws.id, actor.person.id, entry_id)
    await uow.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ translator rules


class RuleIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    scope_type: Literal["workspace", "project", "team"] = "workspace"
    scope_id: str | None = None
    is_active: bool = True
    position: int = 0


class RulePatch(BaseModel):
    text: str | None = None
    is_active: bool | None = None
    position: int | None = None


@router.get("/translator-rules", tags=["client"])
async def list_rules(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {"data": await svc.list_rules(uow, actor.ws.id), "next_cursor": None}


@router.post(
    "/translator-rules", status_code=201, tags=["client"], dependencies=[needs("translator_rule.write")]
)
async def create_rule(body: RuleIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await svc.create_rule(uow, actor.ws.id, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(row)


@router.patch("/translator-rules/{rule_id}", tags=["client"], dependencies=[needs("translator_rule.write")])
async def patch_rule(rule_id: str, body: RulePatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await svc.patch_rule(uow, actor.ws.id, actor.person.id, rule_id, body.model_dump(exclude_none=True))
    await uow.commit()
    return row_dict(row)


@router.delete(
    "/translator-rules/{rule_id}",
    status_code=204,
    tags=["client"],
    dependencies=[needs("translator_rule.write")],
)
async def delete_rule(rule_id: str, actor: CurrentActor, uow: Uow) -> Response:
    await svc.delete_rule(uow, actor.ws.id, actor.person.id, rule_id)
    await uow.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ feedback + activities


class FeedbackIn(BaseModel):
    client_id: str | None = None
    field: str
    raw: str | None = None
    previous: Any = None
    corrected: Any = None


@router.post("/capture/feedback", status_code=201, tags=["client"], dependencies=[needs("capture.write")])
async def capture_feedback(
    items: list[FeedbackIn], request: Request, actor: CurrentActor, uow: Uow
) -> dict[str, Any]:
    stored = await svc.store_feedback(uow, actor.ws.id, actor.person.id, [f.model_dump() for f in items])
    await uow.commit()
    return {"stored": stored}


@router.get("/activities", tags=["client"])
async def list_activities(
    actor: CurrentActor,
    uow: Uow,
    status: ActivityStatus | None = None,
    person: str = "me",
    limit: int = 50,
    team: str | None = None,
) -> dict[str, Any]:
    """My activities, one person's, or (`team=mine`) the active work of the people I manage or lead (Phase 14 "team now")."""
    data = await activity_feed.list_activities(
        uow, actor.ws.id, actor.person.id, actor.acl, status=status, person=person, limit=limit, team=team
    )
    return {"data": data, "next_cursor": None}
