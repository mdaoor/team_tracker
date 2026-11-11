"""Workspace bootstrap (dev only) and personal API tokens."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from tt_server.api.deps import Uow, _authenticate
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain.tenancy import service as tenancy
from tt_server.domain.tokens import mint_member_token

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
    result = mint_member_token(
        uow,
        member,
        name=body.name,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
        presenting_scopes=presenting.scopes if presenting is not None else None,
    )
    await uow.commit()
    return result
