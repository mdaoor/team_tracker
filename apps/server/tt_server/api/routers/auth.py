"""Workspace bootstrap (dev only) and personal API tokens."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, TargetType

from tt_server.acl.permissions import TOKEN_SCOPES
from tt_server.api.deps import Uow, _authenticate, hash_token, new_token
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.tenancy import service as tenancy

router = APIRouter()


class OwnerIn(BaseModel):
    display_name: str
    email: str
    handle: str
    time_zone: str = "UTC"


class WorkspaceIn(BaseModel):
    name: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,38}$")
    owner: OwnerIn


@router.post("/api/v1/workspaces", status_code=201, tags=["workspaces"])
async def create_workspace(body: WorkspaceIn, request: Request, uow: Uow) -> dict[str, Any]:
    """Bootstrap a workspace with its first member. Dev only until real sign-in lands (Phase 4)."""
    if not request.app.state.settings.dev_auth:
        raise ApiError(403, "forbidden", "workspace creation over the API is disabled")
    person = await tenancy.get_or_create_person(
        uow, display_name=body.owner.display_name, email=body.owner.email, time_zone=body.owner.time_zone
    )
    ws = await tenancy.create_workspace(uow, name=body.name, slug=body.slug, creator_id=person.id)
    member = await tenancy.add_member(uow, ws, person, handle=body.owner.handle, actor_id=person.id)
    await uow.commit()
    return {"id": ws.id, "slug": ws.slug, "owner": {"person_id": person.id, "handle": member.handle}}


class TokenIn(BaseModel):
    workspace: str
    name: str = "cli"
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)
    scopes: list[str] = Field(default_factory=lambda: ["capture", "read"])


@router.post("/auth/tokens", status_code=201, tags=["auth"])
async def create_token(
    body: TokenIn,
    request: Request,
    uow: Uow,
    authorization: Annotated[str | None, Header()] = None,
    x_debug_actor: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    ws = await uow.session.scalar(select(m.Workspace).where(m.Workspace.slug == body.workspace))
    if ws is None:
        raise ApiError(404, "not_found", "workspace not found")
    person, presenting = await _authenticate(request, uow, authorization, x_debug_actor, ws)
    member = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.person_id == person.id
        )
    )
    if member is None:
        raise ApiError(404, "not_found", "workspace not found")
    bad = [x for x in body.scopes if x not in TOKEN_SCOPES]
    if bad or not body.scopes:
        raise ApiError(
            400,
            "invalid_scope",
            f"scopes must be a non-empty subset of {list(TOKEN_SCOPES)}",
            pointer="/scopes",
        )
    if presenting is not None:  # a token can only mint tokens no stronger than itself (scopes are ordered)
        ceiling = max((TOKEN_SCOPES.index(x) for x in presenting.scopes if x in TOKEN_SCOPES), default=-1)
        if any(TOKEN_SCOPES.index(x) > ceiling for x in body.scopes):
            raise ApiError(
                403,
                "scope_ceiling",
                "a token cannot create a token with a wider scope than its own",
                pointer="/scopes",
            )
    secret = new_token()
    row = m.ApiToken(
        id=new_id(),
        workspace_id=ws.id,
        person_id=person.id,
        name=body.name,
        hash=hash_token(secret),
        scopes=body.scopes,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=person.id,
        entity_type=TargetType.PERSON,
        entity_id=person.id,
        event_type="token.created",
        payload={"after": {"token_id": row.id, "name": body.name}},
        source=EventSource.OVERLAY,
    )
    await uow.commit()
    return {"id": row.id, "token": secret, "expires_at": row.expires_at, "scopes": body.scopes}
