"""A world to run every management op against: builds real objects for each op's arguments (items, teams, risks, approvals,
review rows, violations, habits, policies, iterations ...) so authorisation is tested on objects that exist, not on a 404.

Used by the role x op matrix (`test_commands_matrix.py`) and the per-op event snapshot (`test_commands_events.py`). Every object is
created as the workspace owner (@mahmoud) through the same commands and endpoints the product uses.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.enums import HabitStatus, Polarity, PolicyCategory, TargetType, Trend
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.overlay import approvals

from tests.api.conftest import Api
from tests.api.test_acl_governance import active_item, make_policy, run, violations
from tests.api.test_commands import one

Args = dict[str, Any]
Builder = Callable[["World"], Awaitable[Args]]


class World:
    def __init__(self, api: Api, sf: async_sessionmaker[AsyncSession]) -> None:
        self.api, self.sf, self.n = api, sf, 0

    def uniq(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"

    async def admin(self, op: str, args: Args) -> dict[str, Any]:
        res = await one(self.api, op, args, confirmed=True)
        assert res["status"] == "applied", (op, args, res)
        return dict(res["result"])

    async def setup(self) -> None:
        """What every op may need to exist: the `theme` dimension, a team, an active no-estimate policy."""
        r = await self.api.call("POST", "/dimensions", json={"key": "theme", "name": "Theme", "applies_to": ["work_item", "project"]})  # fmt: skip
        assert r.status_code == 201, r.text
        await self.admin("dimension.value.add", {"dimension": "theme", "name": "Checkout"})
        await self.admin("team.create", {"name": "payments", "kind": "virtual"})
        await make_policy(self.api, "no_est")

    # ---- objects
    async def item(self, project: str = "WEB", **kw: Any) -> str:
        return str(
            (await self.admin("item.create", {"title": self.uniq("it"), "project": project, **kw}))["key"]
        )

    async def tag(self) -> str:
        name = self.uniq("tag")
        await self.admin("tag.create", {"name": name})
        return name

    async def risk(self, project: str = "WEB") -> str:
        return str((await self.admin("risk.create", {"project": project, "title": self.uniq("risk")}))["key"])

    async def iteration(self, *, started: bool = False) -> str:
        name = self.uniq("Sprint ")
        await self.admin("iteration.create", {"project": "WEB", "name": name, "start_date": "2026-11-02", "end_date": "2026-11-13"})  # fmt: skip
        if started:
            await self.admin("iteration.start", {"project": "WEB", "iteration": name})
        return name

    async def milestone(self) -> str:
        name = self.uniq("Milestone ")
        await self.admin("milestone.create", {"project": "WEB", "name": name, "due_date": "2026-12-01"})
        return name

    async def approval(self) -> str:
        key = await self.item()
        async with UnitOfWork(self.sf) as uow:
            ws = await uow.session.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
            item = await uow.session.scalar(select(m.WorkItem).where(m.WorkItem.key == key))
            owner = await uow.session.scalar(select(m.WorkspaceMember.person_id).where(m.WorkspaceMember.handle == "@mahmoud"))  # fmt: skip
            assert ws and item and owner
            a = await approvals.request(uow, ws, project_id=item.project_id, target_type=TargetType.WORK_ITEM, target_id=item.id, title="scope change", requested_by=owner)  # fmt: skip
            await uow.commit()
            return str(a.id)

    async def review_row(self) -> str:
        await self.api.send(as_="@ali", action="create", item={"title": self.uniq("no project ")})
        rows = (await self.api.call("GET", "/review-queue")).json()["data"]
        assert rows, "the capture without a project should have queued a review row"
        return str(rows[0]["ref"])

    async def violation(self) -> str:
        key = await active_item(self.api, self.uniq("noest "))
        await run(self.api, "no_est")
        found = [v for v in await violations(self.api, status="open") if v["evidence"].get("item") == key]
        assert found, key
        return str(found[0]["id"])

    async def habit(self, category: str = "process") -> str:
        async with self.sf() as s:
            ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
            who = await s.scalar(
                select(m.WorkspaceMember.person_id).where(m.WorkspaceMember.handle == "@ali")
            )
            assert ws and who
            h = m.Habit(
                id=new_id(), workspace_id=ws.id, subject_type=TargetType.PERSON, subject_id=who, key=self.uniq("habit"),
                polarity=Polarity.NEGATIVE, category=PolicyCategory(category), trend=Trend.STABLE, status=HabitStatus.OBSERVED,
                occurrences=3, rate=Decimal("0.5"), score=Decimal("1"), window="30d",
            )  # fmt: skip
            s.add(h)
            await s.commit()
            return str(h.id)

    async def policy(self, *, active: bool) -> str:
        key = self.uniq("pol")
        await make_policy(self.api, key, activate=active)
        if not active:
            assert (
                await self.api.call("POST", f"/policies/{key}:test")
            ).status_code == 200  # activation needs a fresh dry-run
        return key


def _dates() -> Args:
    return {"planned_end": date(2026, 11, 20).isoformat()}


# op -> builder of valid args against freshly created objects (called as the owner, before the op runs as the role under test)
ARGS: dict[str, Builder] = {}


def arg(op: str) -> Callable[[Builder], Builder]:
    def deco(fn: Builder) -> Builder:
        ARGS[op] = fn
        return fn

    return deco


async def _p_create(w: World) -> Args:
    return {"name": "Fresh", "key": w.uniq("Q")}


async def _p_update(w: World) -> Args:
    return {"project": "WEB", "name": w.uniq("Website ")}


async def _p_member(w: World) -> Args:
    return {"project": "DATA", "person": "@sara", "role": "contributor"}


async def _p_plan(w: World) -> Args:
    return {
        "project": "DATA",
        "plan": {"phases": [{"title": w.uniq("Phase "), "deliverables": []}], "milestones": []},
    }


async def _p_baseline(w: World) -> Args:
    return {"project": "WEB", "name": w.uniq("base")}


async def _i_create(w: World) -> Args:
    return {"title": w.uniq("new "), "project": "WEB"}


async def _i_key_priority(w: World) -> Args:
    return {"key": await w.item(), "priority": "high"}


async def _i_prio(w: World) -> Args:
    return {"keys": [await w.item()], "priority": "high"}


async def _i_impact(w: World) -> Args:
    return {"keys": [await w.item()], "impact": 4}


async def _i_weight(w: World) -> Args:
    return {"key": await w.item(), "weight": "5"}


async def _i_dates(w: World) -> Args:
    return {"key": await w.item(), **_dates()}


async def _i_estimate(w: World) -> Args:
    return {"key": await w.item(), "value": "6", "unit": "hours"}


async def _i_stage(w: World) -> Args:
    return {"key": await w.item(), "stage": "build"}


async def _i_assign(w: World) -> Args:
    return {"key": await w.item(), "person": "@sara"}


async def _i_state(w: World) -> Args:
    return {"key": await w.item(), "state": "doing"}


async def _i_reparent(w: World) -> Args:
    return {"keys": [await w.item()], "parent": await w.item(type="epic")}


async def _i_merge(w: World) -> Args:
    a, b = await w.item(), await w.item()
    return {"sources": [a, b], "target": a}


async def _i_split(w: World) -> Args:
    return {"key": await w.item(), "titles": ["API part", "UI part"]}


async def _i_link(w: World) -> Args:
    return {"from": await w.item(), "to": await w.item(), "type": "blocks"}


async def _i_tag(w: World) -> Args:
    return {"keys": [await w.item()], "add": [w.uniq("q")]}


async def _i_bulk(w: World) -> Args:
    return {"keys": [await w.item()], "set": {"priority": "high"}}


async def _i_iter(w: World) -> Args:
    return {"keys": [await w.item()], "iteration": await w.iteration()}


async def _t_create(w: World) -> Args:
    return {"name": w.uniq("team"), "kind": "virtual", "members": ["@sara"]}


async def _t_add(w: World) -> Args:
    return {"team": "payments", "members": ["@sara"]}


async def _t_remove(w: World) -> Args:
    await w.admin("team.add_members", {"team": "payments", "members": ["@ali"]})
    return {"team": "payments", "members": ["@ali"]}


async def _rl(w: World) -> Args:
    return {"person": "@ali", "manager": "@sara"}


async def _tag_merge(w: World) -> Args:
    return {"tag": await w.tag(), "into": await w.tag()}


async def _tag_create(w: World) -> Args:
    return {"name": w.uniq("fresh")}


async def _dim_value(w: World) -> Args:
    return {"dimension": "theme", "name": w.uniq("Value ")}


async def _classify(w: World) -> Args:
    return {"target": await w.item(), "dimension": "theme", "value": "checkout"}


async def _stage_move(w: World) -> Args:
    return {"keys": [await w.item()], "stage": "build", "reason": "ready"}


async def _risk_create(w: World) -> Args:
    return {"project": "WEB", "title": w.uniq("risk "), "probability": 4, "impact": 3}


async def _risk_link(w: World) -> Args:
    return {"risk": await w.risk(), "item": await w.item()}


async def _risk_action(w: World) -> Args:
    return {"risk": await w.risk(), "description": "ask the vendor"}


async def _approval(w: World) -> Args:
    return {"approval": await w.approval(), "decision": "approved", "comment": "ok"}


async def _review_resolve(w: World) -> Args:
    return {"row": await w.review_row(), "choice": {"project": "WEB"}}


async def _review_bulk(w: World) -> Args:
    return {"rows": [await w.review_row()], "action": "ignore"}


async def _v_ack(w: World) -> Args:
    return {"id": await w.violation()}


async def _v_resolve(w: World) -> Args:
    return {"id": await w.violation(), "note": "fixed"}


async def _v_waive(w: World) -> Args:
    return {"id": await w.violation(), "reason": "customer approved an exception"}


async def _h_note(w: World) -> Args:
    return {"habit": await w.habit(), "note": "pair on estimates"}


async def _h_status(w: World) -> Args:
    return {"habit": await w.habit(), "status": "in_coaching"}


async def _pol_activate(w: World) -> Args:
    return {"policy": await w.policy(active=False)}


async def _pol_deactivate(w: World) -> Args:
    return {"policy": await w.policy(active=True)}


async def _it_create(w: World) -> Args:
    return {"project": "WEB", "name": w.uniq("Iter "), "start_date": "2026-11-02", "end_date": "2026-11-13"}


async def _it_start(w: World) -> Args:
    return {"project": "WEB", "iteration": await w.iteration()}


async def _it_close(w: World) -> Args:
    return {"project": "WEB", "iteration": await w.iteration(started=True)}


async def _ms_create(w: World) -> Args:
    return {"project": "WEB", "name": w.uniq("Ms "), "due_date": "2026-12-01"}


async def _ms_complete(w: World) -> Args:
    return {"project": "WEB", "milestone": await w.milestone()}


async def _note(w: World) -> Args:
    return {"title": w.uniq("Note "), "body": "Code freeze from Dec 1", "scope_type": "workspace"}


async def _dailylog(w: World) -> Args:
    return {"day": "2026-10-23"}


ARGS.update(
    {
        "project.create": _p_create, "project.update": _p_update, "project.add_member": _p_member,
        "project.plan.create": _p_plan, "project.baseline.capture": _p_baseline,
        "item.create": _i_create, "item.update": _i_key_priority, "item.set_priority": _i_prio, "item.set_impact": _i_impact,
        "item.set_weight": _i_weight, "item.set_dates": _i_dates, "item.set_estimate": _i_estimate, "item.set_stage": _i_stage,
        "item.assign": _i_assign, "item.set_state": _i_state, "item.reparent": _i_reparent, "item.merge": _i_merge,
        "item.split": _i_split, "item.link": _i_link, "item.tag": _i_tag, "item.bulk_set": _i_bulk,
        "item.move_to_iteration": _i_iter,
        "team.create": _t_create, "team.add_members": _t_add, "team.remove_members": _t_remove, "reporting_line.set": _rl,
        "tag.merge": _tag_merge, "tag.create": _tag_create, "dimension.value.add": _dim_value, "classify": _classify,
        "stage.move": _stage_move, "risk.create": _risk_create, "risk.link": _risk_link, "risk.action.add": _risk_action,
        "approval.decide": _approval,
        "review.resolve": _review_resolve, "review.bulk": _review_bulk,
        "violation.ack": _v_ack, "violation.resolve": _v_resolve, "violation.waive": _v_waive,
        "habit.coach_note": _h_note, "habit.set_status": _h_status,
        "policy.activate": _pol_activate, "policy.deactivate": _pol_deactivate,
        "iteration.create": _it_create, "iteration.start": _it_start, "iteration.close": _it_close,
        "milestone.create": _ms_create, "milestone.complete": _ms_complete,
        "context_note.add": _note, "dailylog.confirm": _dailylog,
    }
)  # fmt: skip
