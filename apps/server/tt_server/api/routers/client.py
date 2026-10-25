"""Endpoints serving reporter clients: ContextPack, glossary, translator rules, feedback, activities."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from tt_schema.base import new_id
from tt_schema.enums import ActivityStatus, TargetType

from tt_server.acl.visibility import redact_activity, visible
from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.routers.core import row_dict
from tt_server.db import models as m
from tt_server.domain.common import emit
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
    member = actor.member
    if (
        for_ and for_ != actor.member.handle
    ):  # export the pack another person would get (admin only, air-gapped mode)
        if not actor.acl.is_admin:
            raise ApiError(403, "forbidden", "only a workspace admin may export another person's pack")
        other = await uow.session.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == actor.ws.id,
                m.WorkspaceMember.handle == (for_ if for_.startswith("@") else "@" + for_),
            )
        )
        if other is None:
            raise ApiError(404, "not_found", f"unknown member {for_}")
        member = other
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


async def _entry(uow: Uow, ws_id: str, entry_id: str) -> m.GlossaryEntry:
    e = await uow.session.scalar(
        select(m.GlossaryEntry).where(
            m.GlossaryEntry.id == entry_id,
            m.GlossaryEntry.workspace_id == ws_id,
            m.GlossaryEntry.deleted_at.is_(None),
        )
    )
    if e is None:
        raise ApiError(404, "not_found", "glossary entry not found")
    return e


@router.get("/glossary", tags=["client"])
async def list_glossary(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.GlossaryEntry)
            .where(m.GlossaryEntry.workspace_id == actor.ws.id, m.GlossaryEntry.deleted_at.is_(None))
            .order_by(m.GlossaryEntry.phrase)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post("/glossary", status_code=201, tags=["client"], dependencies=[needs("translator_rule.write")])
async def create_glossary(body: GlossaryIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    clash = await uow.session.scalar(
        select(m.GlossaryEntry.id).where(
            m.GlossaryEntry.workspace_id == actor.ws.id,
            func.lower(m.GlossaryEntry.phrase) == body.phrase.lower(),
            m.GlossaryEntry.deleted_at.is_(None),
        )
    )
    if clash:
        raise ApiError(409, "glossary_exists", "phrase already defined", pointer="/phrase")
    row = m.GlossaryEntry(id=new_id(), workspace_id=actor.ws.id, **body.model_dump())
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="glossary.learned",
        payload={"after": {"phrase": body.phrase, "to": f"{body.resolves_to_type}:{body.resolves_to_id}"}},
    )
    await uow.commit()
    return row_dict(row)


@router.patch("/glossary/{entry_id}", tags=["client"], dependencies=[needs("translator_rule.write")])
async def patch_glossary(entry_id: str, body: GlossaryPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await _entry(uow, actor.ws.id, entry_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="glossary.learned",
        payload={"fields_changed": list(body.model_dump(exclude_none=True))},
    )
    await uow.commit()
    return row_dict(row)


@router.delete(
    "/glossary/{entry_id}", status_code=204, tags=["client"], dependencies=[needs("translator_rule.write")]
)
async def delete_glossary(entry_id: str, actor: CurrentActor, uow: Uow) -> Response:
    from tt_schema.base import utcnow

    row = await _entry(uow, actor.ws.id, entry_id)
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="glossary.learned",
        payload={"before": {"phrase": row.phrase}},
    )
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


async def _rule(uow: Uow, ws_id: str, rule_id: str) -> m.TranslatorRule:
    r = await uow.session.scalar(
        select(m.TranslatorRule).where(
            m.TranslatorRule.id == rule_id,
            m.TranslatorRule.workspace_id == ws_id,
            m.TranslatorRule.deleted_at.is_(None),
        )
    )
    if r is None:
        raise ApiError(404, "not_found", "rule not found")
    return r


@router.get("/translator-rules", tags=["client"])
async def list_rules(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = (
        await uow.session.scalars(
            select(m.TranslatorRule)
            .where(m.TranslatorRule.workspace_id == actor.ws.id, m.TranslatorRule.deleted_at.is_(None))
            .order_by(m.TranslatorRule.position, m.TranslatorRule.created_at)
        )
    ).all()
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


@router.post(
    "/translator-rules", status_code=201, tags=["client"], dependencies=[needs("translator_rule.write")]
)
async def create_rule(body: RuleIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    if (body.scope_type == "workspace") != (body.scope_id is None):
        raise ApiError(
            400,
            "invalid_scope",
            "scope_id is required exactly when scope_type is not 'workspace'",
            pointer="/scope_id",
        )
    row = m.TranslatorRule(id=new_id(), workspace_id=actor.ws.id, **body.model_dump())
    uow.session.add(row)
    await uow.session.flush()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="translator_rule.created",
        payload={"after": {"rule_id": row.id, "text": row.text}},
    )
    await uow.commit()
    return row_dict(row)


@router.patch("/translator-rules/{rule_id}", tags=["client"], dependencies=[needs("translator_rule.write")])
async def patch_rule(rule_id: str, body: RulePatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await _rule(uow, actor.ws.id, rule_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="translator_rule.updated",
        payload={"fields_changed": list(body.model_dump(exclude_none=True))},
    )
    await uow.commit()
    return row_dict(row)


@router.delete(
    "/translator-rules/{rule_id}",
    status_code=204,
    tags=["client"],
    dependencies=[needs("translator_rule.write")],
)
async def delete_rule(rule_id: str, actor: CurrentActor, uow: Uow) -> Response:
    from tt_schema.base import utcnow

    row = await _rule(uow, actor.ws.id, rule_id)
    row.deleted_at = utcnow()
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="translator_rule.deleted",
        payload={"before": {"rule_id": row.id}},
    )
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
    if len(items) > 500:
        raise ApiError(400, "too_many", "at most 500 corrections per request")
    for f in items:
        uow.session.add(
            m.TranslatorFeedback(
                id=new_id(), workspace_id=actor.ws.id, person_id=actor.person.id, **f.model_dump()
            )
        )
    if items:
        emit(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            entity_type=TargetType.PERSON,
            entity_id=actor.person.id,
            event_type="translator_rule.feedback",
            payload={"after": {"count": len(items)}},
        )
    await uow.commit()
    return {"stored": len(items)}


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
    pid = actor.person.id
    group: set[str] | None = None
    if team == "mine":
        led = select(m.Membership.person_id).where(
            m.Membership.team_id.in_(select(m.Team.id).where(m.Team.workspace_id == actor.ws.id, m.Team.lead_id == actor.person.id)),
            m.Membership.left_at.is_(None),
        )  # fmt: skip
        group = set(actor.acl.managed_ids) | set((await uow.session.scalars(led)).all())
        group.discard(actor.person.id)
    elif person != "me":
        pid = (
            await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id, m.WorkspaceMember.handle == person
                )
            )
            or ""
        )
    whose = m.Activity.person_id == pid if group is None else m.Activity.person_id.in_(group)
    stmt = (
        select(m.Activity, m.WorkItem.key, m.WorkItem.title, m.Project.key)
        .join(m.WorkItem, m.WorkItem.id == m.Activity.work_item_id)
        .join(m.Project, m.Project.id == m.Activity.project_id)
        .where(whose, m.Project.workspace_id == actor.ws.id)
    )
    stmt = visible(stmt, actor.acl, m.Activity)
    if status:
        stmt = stmt.where(m.Activity.status == status)
    rows = (
        await uow.session.execute(stmt.order_by(m.Activity.started_at.desc()).limit(min(limit, 200)))
    ).all()
    handles = dict(
        (
            await uow.session.execute(
                select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id,
                    m.WorkspaceMember.person_id.in_({a.person_id for a, *_ in rows}),
                )
            )
        ).all()
    )
    return {
        "data": [
            {
                **redact_activity(actor.acl, row_dict(a)),
                "item_key": k,
                "item_title": t,
                "project_key": pk,
                "person": handles.get(a.person_id),
            }
            for a, k, t, pk in rows
        ],
        "next_cursor": None,
    }
