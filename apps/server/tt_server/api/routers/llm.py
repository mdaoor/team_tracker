"""Server endpoints the LLM gateway and its callers use (Phase 2b "Core server additions")."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from tt_schema.enums import TargetType

from tt_server.api.authz import needs, needs_review
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.overlay_support import check_target, target_id_of
from tt_server.domain import llm_tools
from tt_server.domain.errors import DomainError
from tt_server.domain.overlay import review as review_svc
from tt_server.domain.overlay import suggestions
from tt_server.domain.overlay.dimensions import get_dimension
from tt_server.domain.tokens import mint_service_token

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
    rows = await llm_tools.search_items(
        uow.session,
        actor.acl,
        actor.ws.id,
        actor.person.id,
        q=q,
        project=project,
        assignee=assignee,
        state_category=state_category,
        limit=limit,
    )
    return {"data": rows, "next_cursor": None}


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
    row = await llm_tools.edit_review_item(
        uow,
        actor.ws.id,
        actor.person.id,
        rid,
        suggestion=body.suggestion,
        confidence=body.confidence,
        status=body.status,
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
            dim = await get_dimension(uow, actor.ws.id, s.dimension)
            tid = await target_id_of(uow, actor.ws.id, TargetType.WORK_ITEM, s.target)
            await check_target(actor, uow, "classification.write", TargetType.WORK_ITEM, tid)
            row = await llm_tools.store_suggested_classification(
                uow, actor.ws, actor.person.id,
                dimension=dim, value_ref=s.value, target_id=tid, confidence=s.confidence,
            )  # fmt: skip
            if row is None:
                skipped.append(
                    {"target": s.target, "reason": "a manual classification protects this dimension"}
                )
            else:
                written += 1
        except DomainError as exc:
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
    result = mint_service_token(
        uow, actor.ws.id, actor.person.id, name=body.name, expires_in_days=body.expires_in_days
    )
    await uow.commit()
    return result
