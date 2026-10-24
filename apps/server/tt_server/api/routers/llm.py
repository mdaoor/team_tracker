"""Server endpoints the LLM gateway and its callers use (Phase 2b "Core server additions")."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import TargetType, ValueSource

from tt_server.acl.permissions import TOKEN_SCOPES
from tt_server.acl.visibility import visible
from tt_server.api.authz import needs, needs_review
from tt_server.api.deps import CurrentActor, Uow, hash_token, new_token
from tt_server.api.errors import ApiError
from tt_server.api.routers.overlay import _check_target, _dimension, _target_id
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.overlay import classify as classify_svc
from tt_server.domain.overlay import review as review_svc
from tt_server.domain.overlay import suggestions

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["llm"])


@router.get("/items/search")
async def search_items(
    actor: CurrentActor,
    uow: Uow,
    q: str = Query(min_length=1),
    project: str | None = None,
    assignee: str | None = None,
    state_category: str | None = None,
    limit: int = Query(default=8, ge=1, le=25),
) -> dict[str, Any]:
    """Fuzzy title search with trigram similarity scores (pg_trgm) -- the agent's `search_items` tool."""
    score = func.similarity(m.WorkItem.title, q).label("score")
    stmt = (
        select(m.WorkItem.key, m.WorkItem.title, m.Project.key, m.WorkflowState.category, score)
        .join(m.Project, m.Project.id == m.WorkItem.project_id)
        .join(m.WorkflowState, m.WorkflowState.id == m.WorkItem.state_id)
        .where(
            m.Project.workspace_id == actor.ws.id,
            m.WorkItem.deleted_at.is_(None),
            or_(m.WorkItem.title.op("%")(q), m.WorkItem.title.ilike(f"%{q}%")),
        )
    )
    stmt = visible(stmt, actor.acl, m.WorkItem)
    if project:
        stmt = stmt.where(func.lower(m.Project.key) == project.lower())
    if assignee:
        pid = (
            actor.person.id
            if assignee == "me"
            else await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id, m.WorkspaceMember.handle == assignee
                )
            )
        )
        stmt = stmt.where(m.WorkItem.assignee_id == pid)
    if state_category:
        stmt = stmt.where(m.WorkflowState.category.in_(state_category.split(",")))
    rows = (await uow.session.execute(stmt.order_by(score.desc(), m.WorkItem.key).limit(limit))).all()
    return {
        "data": [
            {"key": k, "title": t, "project_key": p, "state_category": c.value, "score": round(float(s), 3)}
            for k, t, p, c, s in rows
        ],
        "next_cursor": None,
    }


# ---------------------------------------------------------------- review items (suggestions)
async def _owned_row(actor: CurrentActor, uow: Uow, rid: str) -> dict[str, Any]:
    rows = await review_svc.list_queue(uow.session, actor.ws.id, actor.person.id)
    row = next((r for r in rows if r["ref"] == f"review_item:{rid}"), None)
    if row is None:
        raise ApiError(404, "not_found", "review item not found")
    return row


@router.get("/review-items/{rid}", dependencies=[needs_review()])
async def get_review_item(rid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _owned_row(actor, uow, rid)


class ReviewPatch(BaseModel):
    suggestion: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["open", "ignored"] | None = None


@router.patch("/review-items/{rid}", dependencies=[needs_review()])
async def patch_review_item(rid: str, body: ReviewPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Edit the stored suggestion / confidence (or ignore the row) -- only by the manager who owns it."""
    await _owned_row(actor, uow, rid)
    row = await uow.session.get(m.ReviewItem, rid)
    assert row is not None  # noqa: S101
    uow.context["actor_id"] = actor.person.id
    before = {
        "suggestion": row.suggestion,
        "confidence": float(row.confidence) if row.confidence is not None else None,
    }
    if body.suggestion is not None:
        row.suggestion = {**(row.suggestion or {}), **body.suggestion}
    if body.confidence is not None:
        row.confidence = body.confidence  # type: ignore[assignment]
    if body.status is not None:
        row.status = "resolved" if body.status == "ignored" else "open"
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=row.target_id,
        event_type="review.updated",
        payload={"before": before, "after": {"suggestion": row.suggestion, "status": row.status}},
    )
    await uow.commit()
    return (
        await _owned_row(actor, uow, rid)
        if row.status == "open"
        else {"ref": f"review_item:{rid}", "status": row.status}
    )


# ---------------------------------------------------------------- suggested classifications (bulk)
class SuggestedIn(BaseModel):
    target: str  # item key or id
    dimension: str
    value: str
    confidence: float = Field(ge=0, le=1)


class SuggestedBulk(BaseModel):
    suggestions: list[SuggestedIn] = Field(max_length=200)
    agent_run_id: str | None = None


@router.post("/classifications:suggested", dependencies=[needs("classification.write")])
async def bulk_suggested(body: SuggestedBulk, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Store pre-filled, *unconfirmed* classifications; the review queue shows them with confidence."""
    uow.context["actor_id"] = actor.person.id
    if body.agent_run_id:
        uow.context["agent_run_id"] = body.agent_run_id
    written, skipped = 0, []
    for s in body.suggestions:
        try:
            dim = await _dimension(uow, actor.ws.id, s.dimension)
            value = await uow.session.scalar(
                select(m.DimensionValue).where(
                    m.DimensionValue.dimension_id == dim.id,
                    m.DimensionValue.deleted_at.is_(None),
                    m.DimensionValue.is_active.is_(True),
                    (m.DimensionValue.key == s.value) | (m.DimensionValue.id == s.value),
                )
            )
            if value is None:
                raise ApiError(404, "not_found", f"value {s.value!r} not found")
            tid = await _target_id(uow, actor.ws.id, TargetType.WORK_ITEM, s.target)
            await _check_target(actor, uow, "classification.write", TargetType.WORK_ITEM, tid)
            row = await classify_svc.classify(
                uow, actor.ws, target_type=TargetType.WORK_ITEM, target_id=tid, dimension=dim, value=value,
                source=ValueSource.RULE, set_by=actor.person.id, confidence=s.confidence, is_confirmed=False,
            )  # fmt: skip
            if row is None:
                skipped.append(
                    {"target": s.target, "reason": "a manual classification protects this dimension"}
                )
            else:
                written += 1
        except ApiError as exc:
            skipped.append({"target": s.target, "reason": exc.message})
    await uow.commit()
    return {"written": written, "skipped": skipped}


# ---------------------------------------------------------------- run the LLM jobs now (admin)
@router.post("/llm/suggest-reviews", dependencies=[needs("role.admin")])
async def suggest_reviews_now(request: Request, actor: CurrentActor) -> dict[str, Any]:
    client = request.app.state.llm
    if client is None:
        raise ApiError(409, "not_configured", "the LLM gateway is not configured")
    res = await suggestions.run_review_suggestions(make_session_factory_from(request), client, actor.ws.id)
    return {"attempted": res.attempted, "written": res.written, "skipped": res.skipped, "errors": res.errors}


@router.post("/llm/prefill-classifications", dependencies=[needs("role.admin")])
async def prefill_now(request: Request, actor: CurrentActor) -> dict[str, Any]:
    client = request.app.state.llm
    if client is None:
        raise ApiError(409, "not_configured", "the LLM gateway is not configured")
    res = await suggestions.prefill_classifications(make_session_factory_from(request), client, actor.ws.id)
    return {"attempted": res.attempted, "written": res.written, "skipped": res.skipped, "errors": res.errors}


def make_session_factory_from(request: Request) -> Any:
    return request.app.state.session_factory


# ---------------------------------------------------------------- service token for the gateway
class ServiceTokenIn(BaseModel):
    name: str = "llm_gateway"
    expires_in_days: int = Field(default=365, ge=1, le=3650)


@router.post("/auth/service-tokens", status_code=201, dependencies=[needs("role.admin")])
async def create_service_token(body: ServiceTokenIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """A `read`-scoped token the gateway may use for tools when the *core server* initiates the work."""
    secret = new_token()
    scopes = [s for s in TOKEN_SCOPES if s in {"capture", "read"}]
    row = m.ApiToken(
        id=new_id(),
        workspace_id=actor.ws.id,
        person_id=actor.person.id,
        name=f"service:{body.name}",
        hash=hash_token(secret),
        scopes=scopes,
        expires_at=utcnow() + timedelta(days=body.expires_in_days),
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=actor.ws.id,
        actor_id=actor.person.id,
        entity_type=TargetType.PERSON,
        entity_id=actor.person.id,
        event_type="token.created",
        payload={"after": {"token_id": row.id, "name": row.name, "service": True}},
    )
    await uow.commit()
    return {"id": row.id, "token": secret, "scopes": scopes, "expires_at": row.expires_at}
