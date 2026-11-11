"""Tenancy endpoints: members, people, teams, memberships, reporting lines and context notes."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from tt_schema.enums import (
    ContextKind,
    MembershipRole,
    ReportingKind,
    ScopeType,
    TeamKind,
    Visibility,
)

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.domain.common import row_dict
from tt_server.domain.names import Names
from tt_server.domain.tenancy import admin
from tt_server.domain.tenancy import service as tenancy

router = APIRouter(prefix="/api/v1/w/{ws}")


class MemberIn(BaseModel):
    display_name: str
    email: str
    handle: str
    time_zone: str = "UTC"


@router.post("/members", status_code=201, tags=["tenancy"], dependencies=[needs("member.write")])
async def add_member(body: MemberIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    person = await tenancy.get_or_create_person(
        uow, display_name=body.display_name, email=body.email, time_zone=body.time_zone
    )
    member = await tenancy.add_member(uow, actor.ws, person, handle=body.handle, actor_id=actor.person.id)
    await uow.commit()
    return row_dict(member)


@router.get("/people", tags=["tenancy"])
async def people(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    rows = await admin.list_people(uow, actor.ws.id)
    return {
        "data": [
            {
                "person_id": p.id,
                "handle": mem.handle,
                "display_name": p.display_name,
                "email": p.email,
                "time_zone": p.time_zone,
                "status": mem.status.value,
                "capacity_hours_per_week": float(mem.capacity_hours_per_week)
                if mem.capacity_hours_per_week is not None
                else None,
            }
            for mem, p in rows
        ],
        "next_cursor": None,
    }


class TeamIn(BaseModel):
    name: str
    slug: str
    kind: TeamKind = TeamKind.ORG
    parent_team_id: str | None = None
    lead_id: str | None = None
    description: str | None = None


@router.post("/teams", status_code=201, tags=["tenancy"], dependencies=[needs("team.write")])
async def create_team(body: TeamIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    team = await admin.create_team(uow, actor.ws, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(team)


@router.get("/teams", tags=["tenancy"])
async def list_teams(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {"data": [row_dict(r) for r in await admin.list_teams(uow, actor.ws.id)], "next_cursor": None}


class MembershipIn(BaseModel):
    person_id: str
    role: MembershipRole = MembershipRole.MEMBER
    allocation_pct: int | None = Field(default=None, ge=0, le=100)


@router.post(
    "/teams/{team_id}/memberships", status_code=201, tags=["tenancy"], dependencies=[needs("team.write")]
)
async def add_membership(team_id: str, body: MembershipIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await admin.add_membership(uow, actor.ws, actor.person.id, team_id, body.model_dump())
    await uow.commit()
    return row_dict(row)


@router.get("/teams/{team_id}/members", tags=["tenancy"])
async def team_members(
    team_id: str, actor: CurrentActor, uow: Uow, effective: bool = False
) -> dict[str, Any]:
    rows = await admin.team_memberships(uow, actor.ws.id, team_id, effective=effective)
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}


class ReportingLineIn(BaseModel):
    person_id: str
    manager_id: str
    kind: ReportingKind = ReportingKind.LINE
    scope_type: ScopeType = ScopeType.NONE
    scope_id: str | None = None
    is_primary: bool = False


@router.get("/reporting-lines", tags=["tenancy"], dependencies=[needs("reporting_line.write")])
async def list_reporting_lines(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """The org graph for the people tree (UX 3.3). Managers who can edit lines may read them."""
    rows = await admin.list_reporting_lines(uow, actor.ws.id)
    names = await Names.load(
        uow.session,
        actor.ws.id,
        [("person", r.person_id) for r in rows] + [("person", r.manager_id) for r in rows],
    )
    return {
        "data": [
            {**row_dict(r), "person": names.person(r.person_id), "manager": names.person(r.manager_id)}
            for r in rows
        ],
        "next_cursor": None,
    }


@router.post(
    "/reporting-lines", status_code=201, tags=["tenancy"], dependencies=[needs("reporting_line.write")]
)
async def add_reporting_line(body: ReportingLineIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await admin.add_reporting_line(uow, actor.ws, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(row)


class ContextNoteIn(BaseModel):
    scope_type: ScopeType
    scope_id: str
    title: str
    body: str
    kind: ContextKind = ContextKind.OTHER
    pinned: bool = False
    visibility: Visibility = Visibility.INHERIT
    valid_from: date | None = None
    valid_to: date | None = None


@router.post("/context-notes", status_code=201, tags=["tenancy"], dependencies=[needs("context_note.write")])
async def create_note(body: ContextNoteIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await admin.create_note(uow, actor.ws, actor.person.id, body.model_dump())
    await uow.commit()
    return row_dict(row)


@router.get("/context-notes", tags=["tenancy"])
async def list_notes(
    actor: CurrentActor, uow: Uow, scope_type: ScopeType | None = None, scope_id: str | None = None
) -> dict[str, Any]:
    rows = await admin.list_notes(uow, actor.ws.id, scope_type=scope_type, scope_id=scope_id)
    return {"data": [row_dict(r) for r in rows], "next_cursor": None}
