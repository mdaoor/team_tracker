"""Review-queue endpoints: list, resolve, undo, unlearn and bulk actions (Phase 5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from tt_server.api.authz import needs_review
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.overlay_support import PREFIX, begin
from tt_server.domain.overlay import review

router = APIRouter(prefix=PREFIX)


@router.get("/review-queue", tags=["review"], dependencies=[needs_review()])
async def review_queue(actor: CurrentActor, uow: Uow, kind: str | None = None) -> dict[str, Any]:
    return {
        "data": await review.list_queue(uow.session, actor.ws.id, actor.person.id, kind),
        "next_cursor": None,
    }


class ResolveIn(BaseModel):
    ref: str
    choice: dict[str, Any] = Field(default_factory=dict)


@router.post("/review-queue/resolve", tags=["review"], dependencies=[needs_review()])
async def resolve_review(body: ResolveIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    out = await review.resolve(uow, actor.ws, body.ref, body.choice, actor_id=actor.person.id)
    await uow.commit()
    return out


class RefIn(BaseModel):
    ref: str


@router.post("/review-queue/undo", tags=["review"], dependencies=[needs_review()])
async def undo_review(body: RefIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    out = await review.undo(uow, actor.ws, body.ref, actor_id=actor.person.id)
    await uow.commit()
    return out


@router.post("/review-queue/unlearn", tags=["review"], dependencies=[needs_review()])
async def unlearn_review(body: RefIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    out = await review.unlearn(uow, actor.ws, body.ref, actor_id=actor.person.id)
    await uow.commit()
    return out


class BulkReviewIn(BaseModel):
    refs: list[str]
    action: str = "ignore"


@router.post("/review-queue/bulk", tags=["review"], dependencies=[needs_review()])
async def bulk_review(body: BulkReviewIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    out = await review.bulk(uow, actor.ws, body.refs, body.action, actor_id=actor.person.id)
    await uow.commit()
    return out


@router.post("/review-queue/confirm-all-suggested", tags=["review"], dependencies=[needs_review()])
async def confirm_all(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    begin(uow, actor)
    n = await review.confirm_all_suggested(uow, actor.ws, actor.person.id)
    await uow.commit()
    return {"confirmed": n}
