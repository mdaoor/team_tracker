"""Stages, risks and approvals. (Phase 14)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from tt_schema.enums import (
    Decision,
    TargetType,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    overlay,
)

# ------------------------------------------------------------------ process & risk


class StageMove(BaseModel):
    keys: list[str] = Field(min_length=1)
    stage: str
    reason: str | None = None
    process: str | None = None


@command(
    "stage.move", "process", "Move items to a process stage", StageMove, permission="item.write_overlay", scope="items", scope_arg="keys", preview=True, modes=("scrum_master", "manager", "pm", "admin"),
    example_args={"keys": ["WEB-1", "WEB-2"], "stage": "build", "reason": "failed QA"}, example_text="move WEB-1 and WEB-2 to build, reason failed QA",
)  # fmt: skip
async def stage_move(ctx: Ctx, a: StageMove) -> Outcome:
    its = await _items(ctx, a.keys)
    if ctx.preview:
        return Outcome(preview={"affected": len(its), "children": 0, "overrides_kept": 0, "will_create": [], "will_delete": [], "sample": [{"key": i.key, "to": a.stage} for i in its[:5]]})  # fmt: skip
    for it in its:
        await overlay.move_stage(
            it.key, overlay.StageMove(stage=a.stage, process=a.process, reason=a.reason), ctx.actor, ctx.uow
        )
    return Outcome(result={"moved": [i.key for i in its], "stage": a.stage})


class RiskCreate(BaseModel):
    project: str
    title: str
    probability: int = Field(default=3, ge=1, le=5)
    impact: int = Field(default=3, ge=1, le=5)
    item: str | None = None
    description: str | None = None


@command(
    "risk.create", "process", "Register a risk", RiskCreate, permission="risk.write", scope="project", scope_arg="project", modes=("manager", "scrum_master", "pm", "admin"),
    example_args={"project": "WEB", "title": "Vendor delay", "probability": 4, "impact": 3}, example_text="new risk on WEB: vendor delay",
)  # fmt: skip
async def risk_create(ctx: Ctx, a: RiskCreate) -> Outcome:
    body = overlay.RiskIn(project=a.project, title=a.title, probability=a.probability, impact=a.impact, description=a.description,
                          **({"target_type": TargetType.WORK_ITEM, "target_id": a.item} if a.item else {}))  # fmt: skip
    return _res(await overlay.create_risk(body, ctx.actor, ctx.uow))


class RiskLink(BaseModel):
    risk: str
    item: str


@command(
    "risk.link", "process", "Link a risk to an item", RiskLink, permission="risk.write", scope="item", scope_arg="item",
    example_args={"risk": "RSK-1", "item": "WEB-101"}, example_text="link risk RSK-1 to WEB-101",
)  # fmt: skip
async def risk_link(ctx: Ctx, a: RiskLink) -> Outcome:
    return _res(await overlay.link_risk(a.risk, overlay.RiskLinkIn(item=a.item), ctx.actor, ctx.uow))


class RiskActionAdd(BaseModel):
    risk: str
    description: str
    due_date: date | None = None


@command(
    "risk.action.add", "process", "Add a mitigation action to a risk", RiskActionAdd, permission="risk.write",
    example_args={"risk": "RSK-1", "description": "Ask vendor for a firm date"}, example_text="add action to RSK-1: ask vendor for a firm date",
)  # fmt: skip
async def risk_action_add(ctx: Ctx, a: RiskActionAdd) -> Outcome:
    return _res(
        await overlay.add_risk_action(
            a.risk, overlay.ActionIn(description=a.description, due_date=a.due_date), ctx.actor, ctx.uow
        )
    )


class ApprovalDecide(BaseModel):
    approval: str
    decision: Decision
    comment: str | None = None


@command(
    "approval.decide", "process", "Approve or reject a pending approval", ApprovalDecide, permission=None, destructive=False, modes=("manager", "pm", "admin", "scrum_master"),
    example_args={"approval": "<approval id>", "decision": "approved", "comment": "ok"}, example_text="approve the pending approval",
)  # fmt: skip
async def approval_decide(ctx: Ctx, a: ApprovalDecide) -> Outcome:
    # the router checks `approval.decide` on the approval's project and the decision row's approver
    return _res(
        await overlay.decide(
            a.approval, overlay.DecisionIn(decision=a.decision, comment=a.comment), ctx.actor, ctx.uow
        )
    )
