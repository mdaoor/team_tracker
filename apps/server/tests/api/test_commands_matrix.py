"""Phase 14 review M7: a real role x op authorisation matrix, generated from the registry.

For each of the six role modes (reporter, scrum_master, coach, project_manager, manager, admin) a person holding exactly that role
runs EVERY registered op against objects that exist (`commands_world.py`), through `POST /commands`. The cell is "allowed" when
the op was not refused for lack of permission, "denied" when it was refused with `forbidden`. The outcome must equal what the
permission model says (`acl.permissions.ROLES`), and also the hand-written table below, so neither the code nor the model can
drift without a reviewer seeing this file change. An allowed cell must actually apply, except for the few ops listed in
`NOT_APPLIED` whose arguments depend on the person's own data.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.acl.permissions import ROLES
from tt_server.api.commands import REGISTRY

from tests.api.commands_world import ARGS, World
from tests.api.conftest import Api
from tests.api.test_commands import one

ROUTERS_COVERED = ("commands",)

USERS = {"reporter": "@rita", "scrum_master": "@sam", "coach": "@coco", "project_manager": "@pam", "manager": "@mia", "admin": "@mahmoud"}  # fmt: skip

# Ops whose router (not the registry) names the permission, so `permission=None` in the catalogue.
EFFECTIVE_PERMISSION = {
    "approval.decide": "approval.decide",  # on the approval's project
    "review.resolve": "review_queue.resolve",
    "review.bulk": "review_queue.resolve",
    "violation.ack": "violation.resolve",
}
# People data (a violation or a habit about someone) is invisible without the right to see it, so a person who may not act on it is
# answered `not_found` (ADR-0008: never confirm that a hidden object exists) rather than `forbidden`.
PEOPLE_DATA = frozenset(
    {"violation.ack", "violation.resolve", "violation.waive", "habit.coach_note", "habit.set_status"}
)
# Allowed, but the sample arguments cannot apply for this person (their own data): the code is what the op answers.
NOT_APPLIED = {
    "dailylog.confirm": "not_found",  # the person has no daily log for that day
    "approval.decide": "domain",  # an approver must be on the approval's decision list; any non-forbidden answer proves the ACL passed
}

# What the permission model says each role may run, pinned here after review against `acl/permissions.py`, plans/16-phase-14 and ADR-0006/0047 (a change to a role is a change to this table).
ALL = frozenset(REGISTRY)
EXPECTED_ALLOWED: dict[str, frozenset[str]] = {
    "reporter": frozenset({"dailylog.confirm"}),
    "scrum_master": frozenset(
        {
            "classify",
            "dailylog.confirm",
            "item.assign",
            "item.bulk_set",
            "item.create",
            "item.link",
            "item.merge",
            "item.move_to_iteration",
            "item.reparent",
            "item.set_dates",
            "item.set_estimate",
            "item.set_impact",
            "item.set_priority",
            "item.set_stage",
            "item.set_state",
            "item.split",
            "item.tag",
            "item.update",
            "iteration.close",
            "iteration.create",
            "iteration.start",
            "milestone.complete",
            "milestone.create",
            "review.bulk",
            "review.resolve",
            "stage.move",
            "tag.create",
            "tag.merge",
        }
    ),
    "coach": frozenset({"context_note.add", "dailylog.confirm", "habit.coach_note", "habit.set_status"}),
    "project_manager": frozenset(
        {
            "approval.decide",
            "classify",
            "context_note.add",
            "dailylog.confirm",
            "habit.coach_note",
            "habit.set_status",
            "item.assign",
            "item.bulk_set",
            "item.create",
            "item.link",
            "item.merge",
            "item.move_to_iteration",
            "item.reparent",
            "item.set_dates",
            "item.set_estimate",
            "item.set_impact",
            "item.set_priority",
            "item.set_stage",
            "item.set_state",
            "item.set_weight",
            "item.split",
            "item.tag",
            "item.update",
            "iteration.close",
            "iteration.create",
            "iteration.start",
            "milestone.complete",
            "milestone.create",
            "policy.activate",
            "policy.deactivate",
            "project.add_member",
            "project.baseline.capture",
            "project.create",
            "project.plan.create",
            "project.update",
            "review.bulk",
            "review.resolve",
            "risk.action.add",
            "risk.create",
            "risk.link",
            "stage.move",
            "tag.create",
            "tag.merge",
            "violation.ack",
            "violation.resolve",
            "violation.waive",
        }
    ),
    "manager": frozenset(
        {
            "approval.decide",
            "classify",
            "context_note.add",
            "dailylog.confirm",
            "habit.coach_note",
            "habit.set_status",
            "item.assign",
            "item.bulk_set",
            "item.create",
            "item.link",
            "item.merge",
            "item.reparent",
            "item.set_dates",
            "item.set_estimate",
            "item.set_impact",
            "item.set_priority",
            "item.set_stage",
            "item.set_state",
            "item.set_weight",
            "item.split",
            "item.tag",
            "item.update",
            "policy.activate",
            "policy.deactivate",
            "project.create",
            "review.bulk",
            "review.resolve",
            "risk.action.add",
            "risk.create",
            "risk.link",
            "stage.move",
            "tag.create",
            "tag.merge",
            "violation.ack",
            "violation.resolve",
            "violation.waive",
        }
    ),
    "admin": ALL,  # workspace_admin holds "*"
}


def _perms(role: str) -> frozenset[str]:
    return (
        frozenset({"*"})
        if role == "admin"
        else ROLES["contributor" if role == "reporter" else role].permissions
    )


def derived_allowed(role: str) -> frozenset[str]:
    p = _perms(role)
    out = set()
    for op, spec in REGISTRY.items():
        perm = EFFECTIVE_PERMISSION.get(op, spec.permission)
        if perm is None or "*" in p or perm in p:
            out.add(op)
    return frozenset(out)


async def _people(api: Api, sf: async_sessionmaker[AsyncSession]) -> None:
    for role, handle in USERS.items():
        if role == "admin":
            continue
        r = await api.call("POST", "/members", json={"display_name": handle[1:].title(), "email": f"{handle[1:]}@acme.test", "handle": handle})  # fmt: skip
        assert r.status_code == 201, r.text
        for project in (
            "WEB",
            "DATA",
        ):  # everyone sees the sample objects, so a refusal is a refusal and not a 404
            assert (await api.call("POST", f"/projects/{project}/members", json={"handle": handle})).status_code in (200, 201)  # fmt: skip
        if role != "reporter":
            await api.role(handle, role)


@pytest.mark.parametrize("role", list(USERS))
async def test_role_x_op_matrix(
    api: Api, session_factory: async_sessionmaker[AsyncSession], role: str
) -> None:
    assert set(ARGS) == set(REGISTRY), set(ARGS) ^ set(
        REGISTRY
    )  # a new op must come with sample arguments here
    world = World(api, session_factory)
    await world.setup()
    await _people(api, session_factory)
    who = USERS[role]
    want = derived_allowed(role)
    assert want == EXPECTED_ALLOWED[role], sorted(
        want ^ EXPECTED_ALLOWED[role]
    )  # the model and the written table agree
    bad: list[str] = []
    for op in sorted(REGISTRY):
        args = await ARGS[op](world)
        res = await one(api, op, args, as_=who, confirmed=True)
        refusals = ("forbidden", "insufficient_scope") + (("not_found",) if op in PEOPLE_DATA else ())
        denied = res["status"] == "rejected" and res["errors"][0]["code"] in refusals
        if op in want and denied:
            bad.append(f"{role} should be allowed {op} but was refused: {res['errors']}")
        elif op not in want and not denied:
            bad.append(f"{role} should be refused {op} but got {res['status']} {res.get('errors')}")
        elif op in want and res["status"] != "applied" and op not in NOT_APPLIED:
            bad.append(
                f"{role} passed authorisation for {op} but it did not apply (bad sample args?): {res['errors']}"
            )
    assert not bad, "\n".join(bad)


def test_every_op_is_in_some_roles_row_and_the_reporter_runs_nothing_that_changes_work() -> None:
    assert set().union(*EXPECTED_ALLOWED.values()) == set(REGISTRY)
    assert EXPECTED_ALLOWED["admin"] == ALL
    assert EXPECTED_ALLOWED["reporter"] <= {"dailylog.confirm"}  # a plain member manages nothing
    assert EXPECTED_ALLOWED["coach"] >= {"habit.coach_note", "habit.set_status", "context_note.add"}
    assert not EXPECTED_ALLOWED["coach"] & {
        "item.update",
        "item.merge",
        "stage.move",
        "project.create",
    }  # coaches never edit work


async def test_a_coach_sees_habits_that_are_not_about_wellbeing_and_a_project_manager_covers_the_workspace(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The visibility rules know the Phase 14 roles: `habit.view_team` shows a coach habits (never wellbeing ones), and a
    workspace-wide project_manager has a manager's coverage (the matrix above would otherwise answer 404 to both)."""
    world = World(api, session_factory)
    await world.setup()
    await _people(api, session_factory)
    process_habit = await world.habit()
    wellbeing_habit = await world.habit(category="wellbeing")
    seen = {h["id"] for h in (await api.call("GET", "/habits", as_="@coco")).json()["data"]}
    assert process_habit in seen and wellbeing_habit not in seen
    other = await world.item(project="DATA")
    assert (
        await api.call("POST", f"/items/{other}/state", as_="@pam", json={"state": "doing"})
    ).status_code == 200
