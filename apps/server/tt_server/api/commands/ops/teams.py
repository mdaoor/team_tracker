"""Teams and reporting lines. (Phase 14)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import (
    MembershipRole,
    ReportingKind,
    TargetType,
    TeamKind,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    core_tenancy,
)
from tt_server.db import models as m
from tt_server.domain.common import emit

# ------------------------------------------------------------------ teams & people


class TeamCreate(BaseModel):
    name: str
    kind: TeamKind = TeamKind.VIRTUAL
    lead: str | None = Field(default=None, description="@handle")
    members: list[str] = Field(default_factory=list, description="@handles")


@command(
    "team.create", "teams", "Create a team (org, virtual or ad hoc) with members", TeamCreate, token_scope="admin", permission="team.write", preview=True,
    example_args={"name": "payments", "kind": "virtual", "lead": "@me", "members": ["@sara", "@ali"]}, example_text="create virtual team payments with @sara @ali led by me",
)  # fmt: skip
async def team_create(ctx: Ctx, a: TeamCreate) -> Outcome:
    people = [await _person(ctx, h) for h in a.members]
    lead = await _person(ctx, a.lead) if a.lead else ctx.actor.person.id
    if ctx.preview:
        return Outcome(
            preview={
                "affected": len(people),
                "will_create": [f"{a.kind.value} team {a.name}"] + [f"member {h}" for h in a.members],
            }
        )
    team = await core_tenancy.create_team(
        core_tenancy.TeamIn(name=a.name, slug=_slug(a.name), kind=a.kind, lead_id=lead), ctx.actor, ctx.uow
    )
    for pid in dict.fromkeys(people):
        await core_tenancy.add_membership(
            team["id"], core_tenancy.MembershipIn(person_id=pid), ctx.actor, ctx.uow
        )
    emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.TEAM, entity_id=team["id"], event_type="team.members_changed", payload={"after": {"added": len(people)}})  # fmt: skip
    return Outcome(result={**team, "members": len(people)})


class TeamMembers(BaseModel):
    team: str = Field(description="team slug")
    members: list[str] = Field(min_length=1, description="@handles")


@command(
    "team.add_members", "teams", "Add people to a team", TeamMembers, token_scope="admin", permission="team.write", scope="workspace",
    example_args={"team": "payments", "members": ["@sara"]}, example_text="add @sara to team payments",
)  # fmt: skip
async def team_add_members(ctx: Ctx, a: TeamMembers) -> Outcome:
    team = await _team(ctx, a.team)
    n = 0
    for h in a.members:
        pid = await _person(ctx, h)
        live = await ctx.uow.session.scalar(select(m.Membership.id).where(m.Membership.team_id == team.id, m.Membership.person_id == pid, m.Membership.left_at.is_(None)))  # fmt: skip
        if live is None:
            await core_tenancy.add_membership(
                team.id,
                core_tenancy.MembershipIn(person_id=pid, role=MembershipRole.MEMBER),
                ctx.actor,
                ctx.uow,
            )
            n += 1
    emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.TEAM, entity_id=team.id, event_type="team.members_changed", payload={"after": {"added": n}})  # fmt: skip
    return Outcome(result={"team": team.slug, "added": n})


@command(
    "team.remove_members", "teams", "Remove people from a team", TeamMembers, token_scope="admin", permission="team.write", destructive=True, preview=True,
    example_args={"team": "payments", "members": ["@ali"]}, example_text="remove @ali from team payments",
)  # fmt: skip
async def team_remove_members(ctx: Ctx, a: TeamMembers) -> Outcome:
    team = await _team(ctx, a.team)
    ids = [await _person(ctx, h) for h in a.members]
    rows = (await ctx.uow.session.scalars(select(m.Membership).where(m.Membership.team_id == team.id, m.Membership.person_id.in_(ids), m.Membership.left_at.is_(None)))).all()  # fmt: skip
    if ctx.preview:
        return Outcome(
            preview={
                "affected": len(rows),
                "will_delete": [f"membership of {h}" for h in a.members][: len(rows)],
            }
        )
    for r in rows:
        r.left_at = utcnow()
    emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.TEAM, entity_id=team.id, event_type="team.members_changed", payload={"after": {"removed": len(rows)}})  # fmt: skip
    return Outcome(result={"team": team.slug, "removed": len(rows)})


class ReportingLineSet(BaseModel):
    person: str
    manager: str
    kind: ReportingKind = ReportingKind.LINE
    is_primary: bool = False


@command(
    "reporting_line.set", "teams", "Set who a person reports to", ReportingLineSet, token_scope="admin", permission="reporting_line.write", modes=("admin", "manager"),
    example_args={"person": "@sara", "manager": "@mia", "is_primary": True}, example_text="@sara reports to @mia",
)  # fmt: skip
async def reporting_line_set(ctx: Ctx, a: ReportingLineSet) -> Outcome:
    body = core_tenancy.ReportingLineIn(
        person_id=await _person(ctx, a.person),
        manager_id=await _person(ctx, a.manager),
        kind=a.kind,
        is_primary=a.is_primary,
    )
    return _res(await core_tenancy.add_reporting_line(body, ctx.actor, ctx.uow))
