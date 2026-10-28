"""Review queue, violations, habits and policies. (Phase 14)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.enums import HabitStatus

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.errors import ApiError
from tt_server.api.routers import (
    governance,
    governance_habits,
    governance_violations,
    overlay,
)
from tt_server.db import models as m
from tt_server.domain.errors import NotFound

# ------------------------------------------------------------------ governance


def _review_gate(ctx: Ctx) -> None:
    if not (ctx.actor.acl.can("review_queue.resolve") or ctx.actor.acl.managed_ids):
        raise ApiError(
            403,
            "forbidden",
            "missing permission 'review_queue.resolve'",
            details={"permission": "review_queue.resolve"},
        )


class ReviewResolve(BaseModel):
    row: str = Field(description="review-queue ref")
    choice: dict[str, Any] = Field(default_factory=dict)


@command(
    "review.resolve", "governance", "Resolve a review-queue row", ReviewResolve, permission=None, modes=("manager", "scrum_master", "pm", "admin"),
    example_args={"row": "<ref>", "choice": {}}, example_text="resolve review row with the suggested choice",
)  # fmt: skip
async def review_resolve(ctx: Ctx, a: ReviewResolve) -> Outcome:
    _review_gate(ctx)
    return _res(
        await overlay.resolve_review(overlay.ResolveIn(ref=a.row, choice=a.choice), ctx.actor, ctx.uow)
    )


class ReviewBulk(BaseModel):
    rows: list[str] = Field(min_length=1)
    action: str = "ignore"


@command(
    "review.bulk", "governance", "Apply one action to many review rows", ReviewBulk, permission=None, destructive=True, preview=True, modes=("manager", "scrum_master", "pm", "admin"),
    example_args={"rows": ["<ref>"], "action": "ignore"}, example_text="ignore these review rows",
)  # fmt: skip
async def review_bulk(ctx: Ctx, a: ReviewBulk) -> Outcome:
    _review_gate(ctx)
    if ctx.preview:
        return Outcome(
            preview={
                "affected": len(a.rows),
                "will_delete": [],
                "will_create": [],
                "sample": [{"action": a.action}],
            }
        )
    return _res(
        await overlay.bulk_review(overlay.BulkReviewIn(refs=a.rows, action=a.action), ctx.actor, ctx.uow)
    )


class ViolationRef(BaseModel):
    id: str


class ViolationNote(ViolationRef):
    note: str | None = None


class ViolationReason(ViolationRef):
    reason: str = Field(min_length=3)


@command(
    "violation.ack", "governance", "Acknowledge a policy violation", ViolationRef, permission=None, modes=("manager", "reporter", "scrum_master", "pm", "admin"),
    example_args={"id": "<violation id>"}, example_text="acknowledge violation",
)  # fmt: skip
async def violation_ack(ctx: Ctx, a: ViolationRef) -> Outcome:
    return _res(await governance_violations.acknowledge(a.id, ctx.actor, ctx.uow))


@command(
    "violation.resolve", "governance", "Mark a violation resolved", ViolationNote, permission="violation.resolve",
    example_args={"id": "<violation id>", "note": "fixed"}, example_text="resolve violation, fixed",
)  # fmt: skip
async def violation_resolve(ctx: Ctx, a: ViolationNote) -> Outcome:
    return _res(
        await governance_violations.resolve(a.id, governance_violations.Note(note=a.note), ctx.actor, ctx.uow)
    )


@command(
    "violation.waive", "governance", "Waive a violation with a reason", ViolationReason, permission="violation.waive",
    example_args={"id": "<violation id>", "reason": "customer approved exception"}, example_text="waive violation, customer approved exception",
)  # fmt: skip
async def violation_waive(ctx: Ctx, a: ViolationReason) -> Outcome:
    return _res(
        await governance_violations.waive(
            a.id, governance_violations.Reason(reason=a.reason), ctx.actor, ctx.uow
        )
    )


class HabitCoachNote(BaseModel):
    habit: str
    note: str = Field(min_length=1)


@command(
    "habit.coach_note", "governance", "Write a coaching note on a habit (visible to managers and coaches only)", HabitCoachNote, permission="habit.coach", modes=("coach", "manager", "admin"),
    example_args={"habit": "<habit id>", "note": "pair on estimates"}, example_text="coaching note on habit: pair on estimates",
)  # fmt: skip
async def habit_coach_note(ctx: Ctx, a: HabitCoachNote) -> Outcome:
    return _res(
        await governance_habits.patch_habit(
            a.habit, governance_habits.HabitPatch(coaching_note=a.note), ctx.actor, ctx.uow
        )
    )


class HabitSetStatus(BaseModel):
    habit: str
    status: HabitStatus = Field(description="observed | flagged | in_coaching | resolved")


@command(
    "habit.set_status", "governance", "Set a habit's coaching status", HabitSetStatus, permission="habit.coach", modes=("coach", "manager", "admin"),
    example_args={"habit": "<habit id>", "status": "in_coaching"}, example_text="mark habit as in coaching",
)  # fmt: skip
async def habit_set_status(ctx: Ctx, a: HabitSetStatus) -> Outcome:
    return _res(
        await governance_habits.patch_habit(
            a.habit, governance_habits.HabitPatch(status=a.status), ctx.actor, ctx.uow
        )
    )


class PolicyRef(BaseModel):
    policy: str = Field(description="policy key")
    force: bool = False


async def _policy_id(ctx: Ctx, key: str) -> str:
    pid = await ctx.uow.session.scalar(select(m.Policy.id).where(m.Policy.workspace_id == ctx.actor.ws.id, m.Policy.key == key, m.Policy.deleted_at.is_(None)))  # fmt: skip
    if pid is None:
        raise NotFound("not_found", f"unknown policy {key!r}")
    return str(pid)


@command(
    "policy.activate", "governance", "Activate a tested policy", PolicyRef, token_scope="admin", permission="policy.write", modes=("admin", "manager"),
    example_args={"policy": "stale-in-progress"}, example_text="activate policy stale-in-progress",
)  # fmt: skip
async def policy_activate(ctx: Ctx, a: PolicyRef) -> Outcome:
    return _res(
        await governance.activate_policy(await _policy_id(ctx, a.policy), ctx.actor, ctx.uow, force=a.force)
    )


@command(
    "policy.deactivate", "governance", "Deactivate a policy", PolicyRef, token_scope="admin", permission="policy.write", modes=("admin", "manager"),
    example_args={"policy": "stale-in-progress"}, example_text="deactivate policy stale-in-progress",
)  # fmt: skip
async def policy_deactivate(ctx: Ctx, a: PolicyRef) -> Outcome:
    return _res(await governance.deactivate_policy(await _policy_id(ctx, a.policy), ctx.actor, ctx.uow))
