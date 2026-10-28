"""Phase 14 acceptance: an op emits the same event types as the web-console endpoint that does the same thing.

Each op with a console equivalent runs twice on fresh objects built by the same builder: once as a command, once through the
endpoint the web console calls. The event types match (the command's own `command.applied` aside), except for the few extras a
command adds on purpose, which are listed with their reason in `COMMAND_EXTRAS`. Ops with no console endpoint are listed in
`COMMAND_ONLY`; the exact types they emit are pinned in `test_commands_events.py`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.api.commands import REGISTRY
from tt_server.db import models as m

from tests.api.commands_world import ARGS, Args, World
from tests.api.conftest import Api
from tests.api.test_commands_events import run_op

ROUTERS_COVERED = ("commands",)

Req = tuple[str, str, dict[str, Any] | None]  # method, path, json
Console = Callable[[World, Args], Awaitable[list[Req]]]


async def _person(w: World, handle: str) -> str:
    async with w.sf() as s:
        return str(
            await s.scalar(select(m.WorkspaceMember.person_id).where(m.WorkspaceMember.handle == handle))
        )


async def _by_name(w: World, model: Any, name: str, project: str = "WEB") -> str:
    async with w.sf() as s:
        proj = await s.scalar(select(m.Project.id).where(m.Project.key == project))
        return str(await s.scalar(select(model.id).where(model.project_id == proj, model.name == name)))


async def _tag(w: World, name: str) -> str:
    async with w.sf() as s:
        return str(await s.scalar(select(m.Tag.id).where(m.Tag.name == name)))


async def _ws_id(w: World) -> str:
    async with w.sf() as s:
        return str(await s.scalar(select(m.Workspace.id).where(m.Workspace.slug == "acme")))


async def _team(w: World, slug: str) -> str:
    async with w.sf() as s:
        return str(await s.scalar(select(m.Team.id).where(m.Team.slug == slug)))


def _simple(
    method: str, path: Callable[[Args], str], body: Callable[[Args], dict[str, Any] | None]
) -> Console:
    async def build(_w: World, a: Args) -> list[Req]:
        return [(method, path(a), body(a))]

    return build


async def _bulk(w: World, a: Args) -> list[Req]:
    return [
        (
            "POST",
            "/items/bulk",
            {"ids": a["keys"], "set": a.get("set") or {k: v for k, v in a.items() if k != "keys"}},
        )
    ]


async def _team_create(w: World, a: Args) -> list[Req]:
    lead = await _person(w, "@mahmoud")
    slug = "".join(c if c.isalnum() else "-" for c in a["name"].lower()).strip("-")
    reqs: list[Req] = [
        ("POST", "/teams", {"name": a["name"], "slug": slug, "kind": a["kind"], "lead_id": lead})
    ]
    return reqs  # the memberships need the new team's id: added by the caller below


async def _team_members(w: World, a: Args) -> list[Req]:
    team = await _team(w, a["team"])
    return [("POST", f"/teams/{team}/memberships", {"person_id": await _person(w, h)}) for h in a["members"]]


async def _reporting(w: World, a: Args) -> list[Req]:
    return [("POST", "/reporting-lines", {"person_id": await _person(w, a["person"]), "manager_id": await _person(w, a["manager"]), "kind": "line", "is_primary": False})]  # fmt: skip


async def _iteration_state(w: World, a: Args, state: str) -> list[Req]:
    return [("POST", f"/iterations/{await _by_name(w, m.Iteration, a['iteration'])}/state", {"state": state})]


async def _milestone_complete(w: World, a: Args) -> list[Req]:
    return [("POST", f"/milestones/{await _by_name(w, m.Milestone, a['milestone'])}/complete", None)]


async def _tag_merge(w: World, a: Args) -> list[Req]:
    return [("POST", f"/tags/{await _tag(w, a['tag'])}/merge-into/{await _tag(w, a['into'])}", None)]


async def _note(w: World, a: Args) -> list[Req]:
    return [("POST", "/context-notes", {"scope_type": "workspace", "scope_id": await _ws_id(w), "title": a["title"], "body": a["body"]})]  # fmt: skip


async def _dailylog(w: World, a: Args) -> list[Req]:
    async with w.sf() as s:
        log = await s.scalar(select(m.DailyLog.id).where(m.DailyLog.date == date.fromisoformat(a["day"])))
    return [("POST", f"/daily-logs/{log}/confirm", None)]


CONSOLE: dict[str, Console] = {
    "project.create": _simple("POST", lambda a: "/projects", lambda a: {"name": a["name"], "key": a["key"]}),
    "project.update": _simple("PATCH", lambda a: f"/projects/{a['project']}", lambda a: {"name": a["name"]}),
    "project.add_member": _simple("POST", lambda a: f"/projects/{a['project']}/members", lambda a: {"handle": a["person"].lstrip("@"), "role": a["role"]}),  # fmt: skip
    "project.plan.create": _simple("POST", lambda a: f"/projects/{a['project']}/plan", lambda a: a["plan"]),
    "project.baseline.capture": _simple("POST", lambda a: f"/projects/{a['project']}/baselines?name={a['name']}", lambda a: None),  # fmt: skip
    "item.create": _simple("POST", lambda a: "/items", lambda a: {"title": a["title"], "project": a["project"]}),
    "item.set_priority": _bulk,
    "item.set_impact": _bulk,
    "item.bulk_set": _bulk,
    "item.set_weight": _simple("PUT", lambda a: f"/items/{a['key']}/weights/default", lambda a: {"value": a["weight"]}),
    "item.set_stage": _simple("PUT", lambda a: f"/items/{a['key']}/stage", lambda a: {"stage": a["stage"]}),
    "stage.move": _simple("PUT", lambda a: f"/items/{a['keys'][0]}/stage", lambda a: {"stage": a["stage"], "reason": a.get("reason")}),  # fmt: skip
    "item.set_state": _simple("POST", lambda a: f"/items/{a['key']}/state", lambda a: {"state": a["state"]}),
    "item.reparent": _simple("POST", lambda a: f"/items/{a['keys'][0]}/move", lambda a: {"parent": a["parent"]}),
    "item.link": _simple("POST", lambda a: f"/items/{a['from']}/links", lambda a: {"to": a["to"], "link_type": a["type"]}),
    "iteration.create": _simple("POST", lambda a: f"/projects/{a['project']}/iterations", lambda a: {k: a[k] for k in ("name", "start_date", "end_date")}),  # fmt: skip
    "iteration.start": lambda w, a: _iteration_state(w, a, "active"),
    "iteration.close": lambda w, a: _iteration_state(w, a, "closed"),
    "milestone.create": _simple("POST", lambda a: f"/projects/{a['project']}/milestones", lambda a: {"name": a["name"], "due_date": a["due_date"]}),  # fmt: skip
    "milestone.complete": _milestone_complete,
    "risk.create": _simple("POST", lambda a: "/risks", lambda a: {k: a[k] for k in ("project", "title", "probability", "impact")}),  # fmt: skip
    "risk.link": _simple("POST", lambda a: f"/risks/{a['risk']}/links", lambda a: {"item": a["item"]}),
    "risk.action.add": _simple("POST", lambda a: f"/risks/{a['risk']}/actions", lambda a: {"description": a["description"]}),  # fmt: skip
    "classify": _simple("POST", lambda a: "/classifications", lambda a: {"target_type": "work_item", "target_id": a["target"], "dimension": a["dimension"], "value": a["value"]}),  # fmt: skip
    "dimension.value.add": _simple("POST", lambda a: f"/dimensions/{a['dimension']}/values", lambda a: {"key": a["name"].lower(), "name": a["name"]}),  # fmt: skip
    "tag.merge": _tag_merge,
    "team.create": _team_create,
    "team.add_members": _team_members,
    "reporting_line.set": _reporting,
    "context_note.add": _note,
    "dailylog.confirm": _dailylog,
    "violation.ack": _simple("POST", lambda a: f"/violations/{a['id']}:acknowledge", lambda a: None),
    "violation.resolve": _simple("POST", lambda a: f"/violations/{a['id']}:resolve", lambda a: {"note": a["note"]}),
    "violation.waive": _simple("POST", lambda a: f"/violations/{a['id']}:waive", lambda a: {"reason": a["reason"]}),
    "habit.coach_note": _simple("PATCH", lambda a: f"/habits/{a['habit']}", lambda a: {"coaching_note": a["note"]}),
    "habit.set_status": _simple("PATCH", lambda a: f"/habits/{a['habit']}", lambda a: {"status": a["status"]}),
    "policy.activate": _simple("POST", lambda a: f"/policies/{a['policy']}:activate", lambda a: None),
    "policy.deactivate": _simple("POST", lambda a: f"/policies/{a['policy']}:deactivate", lambda a: None),
    "review.resolve": _simple("POST", lambda a: "/review-queue/resolve", lambda a: {"ref": a["row"], "choice": a["choice"]}),
    "review.bulk": _simple("POST", lambda a: "/review-queue/bulk", lambda a: {"refs": a["rows"], "action": a["action"]}),
    "approval.decide": _simple("POST", lambda a: f"/approvals/{a['approval']}/decisions", lambda a: {"decision": a["decision"], "comment": a["comment"]}),  # fmt: skip
}  # fmt: skip

# No console endpoint does exactly this (the console reaches the same result through several calls or a different feature).
COMMAND_ONLY = {
    "item.update", "item.set_dates", "item.assign",  # the console edits these with a conditional PATCH carrying the item's ETag
    "item.merge", "item.split", "item.tag", "item.set_estimate", "item.move_to_iteration", "team.remove_members", "tag.create",
}  # fmt: skip

# What a command emits on top of its console twin, on purpose.
COMMAND_EXTRAS: dict[str, frozenset[str]] = {
    "team.create": frozenset({"team.members_changed"}),  # one summary event for the batch of memberships
    "team.add_members": frozenset({"team.members_changed"}),
}


# the second run (the command) must not collide with what the console twin just did to the same project or team
COMMAND_ARGS: dict[str, Callable[[Args], Args]] = {
    "project.add_member": lambda a: {**a, "person": "@ali"},
    "team.add_members": lambda a: {**a, "members": ["@ali"]},
}


async def _run_console(w: World, reqs: list[Req]) -> None:
    for method, path, body in reqs:
        r = await w.api.call(method, path, json=body)
        assert r.status_code < 300, (method, path, body, r.status_code, r.text)


async def _event_ids(sf: async_sessionmaker[AsyncSession]) -> set[str]:
    async with sf() as s:
        return {i for (i,) in (await s.execute(select(m.Event.id))).all()}


async def _types_since(sf: async_sessionmaker[AsyncSession], before: set[str]) -> set[str]:
    async with sf() as s:
        rows = (await s.execute(select(m.Event.id, m.Event.event_type))).all()
    return {t for i, t in rows if i not in before}


def test_every_op_is_either_compared_with_the_console_or_listed_as_command_only() -> None:
    assert set(CONSOLE) | COMMAND_ONLY == set(REGISTRY) and not set(CONSOLE) & COMMAND_ONLY


@pytest.mark.parametrize("op", sorted(CONSOLE))
async def test_op_emits_what_the_console_endpoint_emits(api: Api, session_factory: async_sessionmaker[AsyncSession], op: str) -> None:  # fmt: skip
    w = World(api, session_factory)
    await w.setup()
    # the console twin first, on its own fresh objects ...
    console_args = await ARGS[op](w)
    before = await _event_ids(session_factory)
    reqs = await CONSOLE[op](w, console_args)
    await _run_console(w, reqs)
    if op in ("team.create",):  # the memberships need the new team's id
        team = await _team(
            w, "".join(c if c.isalnum() else "-" for c in console_args["name"].lower()).strip("-")
        )
        await _run_console(w, [("POST", f"/teams/{team}/memberships", {"person_id": await _person(w, h)}) for h in console_args["members"]])  # fmt: skip
    console_types = await _types_since(session_factory, before)
    if op == "iteration.start":  # only one iteration of a project is active at a time
        await _run_console(w, await _iteration_state(w, console_args, "closed"))
    # ... then the command on another set
    cmd_args = await ARGS[op](w)
    cmd_types, _ = await run_op(w, op, COMMAND_ARGS.get(op, lambda a: a)(cmd_args))
    cmd_types.discard("command.applied")
    extras = COMMAND_EXTRAS.get(op, frozenset())
    assert cmd_types - extras == console_types, (
        f"{op}: command {sorted(cmd_types)} console {sorted(console_types)}"
    )
    assert extras <= cmd_types
