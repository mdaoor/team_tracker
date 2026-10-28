"""Phase 14 acceptance: a snapshot of the event types every op emits.

Each of the 51 ops runs as the owner against real objects (`commands_world.py`) and the set of event types stamped with its
`client_id` must equal `PINNED[op]`, so adding, dropping or renaming an event is a visible change in review. The comparison with
the equivalent web-console endpoint is in `test_commands_console_parity.py`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.api.commands import REGISTRY
from tt_server.db import models as m

from tests.api.commands_world import ARGS, Args, World
from tests.api.conftest import Api
from tests.api.test_commands import cmd, send

ROUTERS_COVERED = ("commands",)

PINNED: dict[str, frozenset[str]] = {
    "approval.decide": frozenset({"approval.decided", "approval.resolved", "command.applied"}),
    "classify": frozenset({"classification.changed", "command.applied"}),
    "context_note.add": frozenset({"command.applied", "context_note.created"}),
    "dailylog.confirm": frozenset({"command.applied", "dailylog.confirmed"}),
    "dimension.value.add": frozenset({"command.applied", "dimension_value.created"}),
    "habit.coach_note": frozenset({"command.applied", "habit.status_changed"}),
    "habit.set_status": frozenset({"command.applied", "habit.status_changed"}),
    "item.assign": frozenset({"command.applied", "item.updated"}),
    "item.bulk_set": frozenset({"command.applied", "item.priority_changed", "weight.recomputed"}),
    "item.create": frozenset({"command.applied", "item.created", "stage.moved"}),
    "item.link": frozenset({"command.applied", "item.linked"}),
    "item.merge": frozenset({"command.applied", "item.merged"}),
    "item.move_to_iteration": frozenset({"command.applied", "item.updated"}),
    "item.reparent": frozenset({"command.applied", "item.parent_changed"}),
    "item.set_dates": frozenset({"command.applied", "item.updated"}),
    "item.set_estimate": frozenset({"command.applied", "item.updated"}),
    "item.set_impact": frozenset({"command.applied", "item.impact_changed"}),
    "item.set_priority": frozenset({"command.applied", "item.priority_changed", "weight.recomputed"}),
    "item.set_stage": frozenset({"command.applied", "stage.moved"}),
    "item.set_state": frozenset({"command.applied", "item.state_changed"}),
    "item.set_weight": frozenset({"command.applied", "weight.overridden"}),
    "item.split": frozenset({"command.applied", "item.split", "stage.moved"}),
    "item.tag": frozenset({"command.applied", "tag.linked"}),
    "item.update": frozenset(
        {"command.applied", "item.priority_changed", "item.updated", "weight.recomputed"}
    ),
    "iteration.close": frozenset({"command.applied", "iteration.state_changed"}),
    "iteration.create": frozenset({"command.applied", "iteration.created"}),
    "iteration.start": frozenset({"command.applied", "iteration.state_changed"}),
    "milestone.complete": frozenset({"command.applied", "milestone.completed"}),
    "milestone.create": frozenset({"command.applied", "milestone.created"}),
    "policy.activate": frozenset({"command.applied", "policy.activated"}),
    "policy.deactivate": frozenset({"command.applied", "policy.deactivated"}),
    "project.add_member": frozenset({"command.applied", "project_member.added"}),
    "project.baseline.capture": frozenset({"baseline.captured", "command.applied"}),
    "project.create": frozenset({"command.applied", "project.created"}),
    "project.plan.create": frozenset({"command.applied", "plan.created", "stage.moved"}),
    "project.update": frozenset({"command.applied", "project.updated"}),
    "reporting_line.set": frozenset({"command.applied", "reporting_line.changed"}),
    "review.bulk": frozenset({"command.applied", "review.resolved"}),
    "review.resolve": frozenset(
        {"command.applied", "glossary.learned", "item.project_changed", "review.resolved", "stage.moved"}
    ),
    "risk.action.add": frozenset({"command.applied", "risk_action.created"}),
    "risk.create": frozenset({"command.applied", "risk.created", "risk.linked"}),
    "risk.link": frozenset({"command.applied", "risk.linked"}),
    "stage.move": frozenset({"command.applied", "stage.moved"}),
    "tag.create": frozenset({"command.applied", "tag.created"}),
    "tag.merge": frozenset({"command.applied", "tag.merged"}),
    "team.add_members": frozenset({"command.applied", "membership.changed", "team.members_changed"}),
    "team.create": frozenset(
        {"command.applied", "membership.changed", "team.created", "team.members_changed"}
    ),
    "team.remove_members": frozenset({"command.applied", "team.members_changed"}),
    "violation.ack": frozenset({"command.applied", "violation.acknowledged"}),
    "violation.resolve": frozenset({"command.applied", "violation.resolved"}),
    "violation.waive": frozenset({"command.applied", "violation.waived"}),
}


async def run_op(w: World, op: str, args: Args) -> tuple[set[str], str]:
    c = cmd(op, args, confirmed=True)
    res = (await send(w.api, c))[0]
    assert res["status"] == "applied", (op, res)
    async with w.sf() as s:
        evs = (
            await s.scalars(
                select(m.Event).where(m.Event.payload["command"]["client_id"].astext == c["client_id"])
            )
        ).all()
    return {e.event_type for e in evs}, c["client_id"]


async def test_every_op_emits_exactly_its_pinned_event_types(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    assert set(PINNED) == set(REGISTRY), set(PINNED) ^ set(REGISTRY)
    w = World(api, session_factory)
    await w.setup()
    got: dict[str, frozenset[str]] = {}
    for op in sorted(REGISTRY):
        types, _ = await run_op(w, op, await ARGS[op](w))
        got[op] = frozenset(types)
    wrong = {op: (sorted(got[op]), sorted(PINNED[op])) for op in REGISTRY if got[op] != PINNED[op]}
    assert not wrong, "\n".join(f"{op}: emitted {a}, pinned {b}" for op, (a, b) in wrong.items())
    assert all(
        "command.applied" in t for t in got.values()
    )  # every applied command is audited by its own event
