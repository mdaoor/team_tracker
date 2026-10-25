"""Violation endpoints: list and the acknowledge / resolve / waive / false-positive transitions (Phase 8)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from tt_schema.enums import ViolationStatus

from tt_server.api.deps import CurrentActor, Uow
from tt_server.governance import violations

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["governance"])


class Note(BaseModel):
    note: str | None = None


class Reason(BaseModel):
    reason: str = Field(min_length=3)


@router.get("/violations")
async def list_violations(
    actor: CurrentActor, uow: Uow, status: str | None = None, severity: str | None = None, category: str | None = None, policy: str | None = None,
    person: str | None = None, project: str | None = None, target_id: str | None = None, limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
) -> dict[str, Any]:  # fmt: skip
    return await violations.list_violations(
        uow,
        actor.ws.id,
        actor.acl,
        status=status,
        severity=severity,
        category=category,
        policy=policy,
        person=person,
        project=project,
        target_id=target_id,
        limit=limit,
        cursor=cursor,
    )


async def _transition(
    uow: Uow,
    actor: CurrentActor,
    vid: str,
    new: ViolationStatus,
    *,
    perm: str | None,
    note: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    out = await violations.transition(
        uow, actor.ws, actor.acl, actor.person.id, vid, new, perm=perm, note=note, reason=reason
    )
    await uow.commit()
    return out


@router.post("/violations/{vid}:acknowledge")
async def acknowledge(vid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.ACKNOWLEDGED, perm="violation.resolve", note=None
    )


@router.post("/violations/{vid}:resolve")
async def resolve(vid: str, body: Note, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.RESOLVED, perm="violation.resolve", note=body.note
    )


@router.post("/violations/{vid}:waive")
async def waive(vid: str, body: Reason, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.WAIVED, perm="violation.waive", note=None, reason=body.reason
    )


@router.post("/violations/{vid}:false-positive")
async def false_positive(vid: str, body: Reason, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return await _transition(
        uow, actor, vid, ViolationStatus.FALSE_POSITIVE, perm="violation.resolve", note=body.reason
    )
