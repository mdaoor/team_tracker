"""The management ops (Phase 14). Each is a thin façade: it translates handles/keys into ids and calls the same router
function or domain service the web console uses, so validation, events and ACL checks are shared.

Handlers run inside the executor's per-command transaction. The executor has already enforced `permission` at the declared
scope; the router functions repeat their own checks where they have them (defence in depth, never weaker).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    Decision,
    EstimationUnit,
    IterationState,
    LinkType,
    MembershipRole,
    MilestoneKind,
    Priority,
    ProjectRole,
    ReportingKind,
    ScopeType,
    TargetType,
    TeamKind,
)

from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.errors import ApiError
from tt_server.api.routers import access, core, governance, item_extras, overlay, plan
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid, NotFound
from tt_server.domain.items import merge as merge_svc
from tt_server.domain.items import service as items_svc
from tt_server.domain.overlay import bulk as bulk_svc
from tt_server.domain.plan import service as plan_svc


def _res(out: Any) -> Outcome:
    return Outcome(result=out if isinstance(out, dict) else {"value": out})


async def _person(ctx: Ctx, handle: str) -> str:
    h = handle.lstrip("@")
    pid = await ctx.uow.session.scalar(
        select(m.WorkspaceMember.person_id).where(
            m.WorkspaceMember.workspace_id == ctx.actor.ws.id,
            m.WorkspaceMember.handle.in_([h, "@" + h]) | (m.WorkspaceMember.person_id == h),
            m.WorkspaceMember.deleted_at.is_(None),
        )
    )
    if pid is None:
        raise NotFound("not_found", f"unknown person {handle!r}")
    return str(pid)


async def _team(ctx: Ctx, ref: str) -> m.Team:
    t = await ctx.uow.session.scalar(
        select(m.Team).where(
            m.Team.workspace_id == ctx.actor.ws.id,
            (m.Team.slug == ref) | (m.Team.id == ref),
            m.Team.deleted_at.is_(None),
        )
    )
    if t is None:
        raise NotFound("not_found", f"unknown team {ref!r}")
    return t


async def _items(ctx: Ctx, keys: list[str]) -> list[m.WorkItem]:
    return [await core._item(ctx.uow, ctx.actor.ws.id, k) for k in dict.fromkeys(keys)]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "team"


# ------------------------------------------------------------------ projects


class ProjectCreate(BaseModel):
    name: str
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{1,9}$")
    process: str | None = None
    team: str | None = Field(default=None, description="team slug")
    description: str | None = None


@command(
    "project.create", "projects", "Create a project", ProjectCreate, permission="project.create", preview=True,
    example_args={"name": "Payments", "key": "PAY", "process": "sw-delivery"}, example_text="create project Payments PAY",
)  # fmt: skip
async def project_create(ctx: Ctx, a: ProjectCreate) -> Outcome:
    team = await _team(ctx, a.team) if a.team else None
    if ctx.preview:
        return Outcome(
            preview={
                "affected": 1,
                "will_create": [f"project {a.key.upper()} “{a.name}”"]
                + ([f"inherits process {a.process}"] if a.process else []),
            }
        )
    body = core.ProjectIn(
        name=a.name,
        key=a.key,
        process=a.process,
        team_id=team.id if team else None,
        description=a.description,
    )
    return _res(await core.create_project(body, ctx.actor, ctx.uow))


class ProjectUpdate(BaseModel):
    project: str
    name: str | None = None
    description: str | None = None
    status: str | None = None


@command(
    "project.update", "projects", "Rename or change a project's status", ProjectUpdate, permission="project.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "name": "Payments v2"}, example_text="rename project PAY to Payments v2",
)  # fmt: skip
async def project_update(ctx: Ctx, a: ProjectUpdate) -> Outcome:
    body = core.ProjectPatch(**a.model_dump(exclude={"project"}, exclude_none=True))
    return _res(await core.patch_project(a.project, body, ctx.actor, ctx.uow))


class ProjectAddMember(BaseModel):
    project: str
    person: str
    role: ProjectRole = ProjectRole.CONTRIBUTOR


@command(
    "project.add_member", "projects", "Add a person to a project", ProjectAddMember, permission="project_member.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "person": "@sara", "role": "contributor"}, example_text="add @sara to PAY",
)  # fmt: skip
async def project_add_member(ctx: Ctx, a: ProjectAddMember) -> Outcome:
    return _res(
        await access.add_project_member(
            a.project, access.ProjectMemberIn(handle=a.person.lstrip("@"), role=a.role), ctx.actor, ctx.uow
        )
    )


class ProjectPlanCreate(BaseModel):
    project: str
    plan: dict[str, Any] = Field(
        description="the Phase 1 §1.2 plan payload (phases, deliverables, milestones)"
    )


@command(
    "project.plan.create", "projects", "Create a top-down plan (phases, deliverables, milestones)", ProjectPlanCreate, permission="project.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "plan": {"phases": []}}, example_text="plan PAY: discovery, build, launch",
)  # fmt: skip
async def project_plan_create(ctx: Ctx, a: ProjectPlanCreate) -> Outcome:
    return _res(await plan.create_plan(a.project, plan_svc.PlanIn.model_validate(a.plan), ctx.actor, ctx.uow))


class BaselineCapture(BaseModel):
    project: str
    name: str


@command(
    "project.baseline.capture", "projects", "Freeze the current plan as a named baseline", BaselineCapture, permission="project.write", scope="project", scope_arg="project",
    example_args={"project": "PAY", "name": "kickoff"}, example_text="baseline PAY as kickoff",
)  # fmt: skip
async def baseline_capture(ctx: Ctx, a: BaselineCapture) -> Outcome:
    return _res(await core.capture_baseline(a.project, a.name, ctx.actor, ctx.uow))


# ------------------------------------------------------------------ items


class ItemCreate(BaseModel):
    title: str
    project: str
    type: str | None = Field(
        default=None, description="item type key: epic, story, task, bug, deliverable ..."
    )
    parent: str | None = Field(default=None, description="parent item key")
    description: str | None = None
    priority: Priority = Priority.MEDIUM
    assignee: str | None = Field(default=None, description="@handle; default: you")


@command(
    "item.create", "items", "Create an item (epic, deliverable, task ...)", ItemCreate, permission="item.write_overlay", scope="project", scope_arg="project",
    example_args={"title": "Checkout redesign", "project": "WEB", "type": "epic"}, example_text="new epic Checkout redesign under WEB",
)  # fmt: skip
async def item_create(ctx: Ctx, a: ItemCreate) -> Outcome:
    type_id = None
    if a.type:
        t = await ctx.uow.session.scalar(
            select(m.WorkItemType).where(
                m.WorkItemType.workspace_id == ctx.actor.ws.id, m.WorkItemType.key == a.type, m.WorkItemType.deleted_at.is_(None)
            ).order_by(m.WorkItemType.project_id.is_(None))
        )  # fmt: skip
        if t is None:
            raise Invalid("unknown_type", f"unknown item type {a.type!r}", pointer="/args/type")
        type_id = t.id
    body = core.ItemIn(
        title=a.title, project=a.project, type_id=type_id, parent=a.parent, description=a.description, priority=a.priority,
        assignee_id=await _person(ctx, a.assignee) if a.assignee else None,
    )  # fmt: skip
    return _res(await core.create_item(body, ctx.actor, ctx.uow))


class ItemUpdate(BaseModel):
    key: str
    description: str | None = None
    priority: Priority | None = None
    impact: int | None = Field(default=None, ge=1, le=5)
    severity: str | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    due_date: date | None = None
    assignee: str | None = Field(default=None, description="@handle")
    team: str | None = Field(default=None, description="team slug")


@command(
    "item.update", "items", "Change overlay fields of an item (priority, impact, dates, assignee, team)", ItemUpdate, permission="item.write_overlay", scope="item", scope_arg="key",
    example_args={"key": "WEB-101", "priority": "high"}, example_text="make WEB-101 high priority",
)  # fmt: skip
async def item_update(ctx: Ctx, a: ItemUpdate) -> Outcome:
    data = a.model_dump(exclude={"key", "assignee", "team"}, exclude_none=True)
    if a.assignee:
        data["assignee_id"] = await _person(ctx, a.assignee)
    if a.team:
        data["team_id"] = (await _team(ctx, a.team)).id
    if not data:
        raise Invalid("nothing_to_change", "give at least one field to change", pointer="/args")
    item = await core._item(ctx.uow, ctx.actor.ws.id, a.key)
    # a command is the user's explicit intent: it carries the etag it just read, so no stale-write conflict is possible here
    out = await overlay.patch_item(
        a.key, overlay.ItemPatch(**data), Response(), ctx.actor, ctx.uow, if_match=overlay.etag_of(item)
    )
    return _res(out)


class ItemSetPriority(BaseModel):
    keys: list[str] = Field(min_length=1)
    priority: Priority


@command(
    "item.set_priority", "items", "Set the priority of items (propagates to children that did not override it)", ItemSetPriority,
    permission="item.write_overlay", scope="items", scope_arg="keys", preview=True,
    example_args={"keys": ["WEB-1"], "priority": "high"}, example_text="make WEB-1 high priority",
)  # fmt: skip
async def item_set_priority(ctx: Ctx, a: ItemSetPriority) -> Outcome:
    return await item_bulk_set(ctx, ItemBulkSet(keys=a.keys, set={"priority": a.priority.value}))


class ItemSetImpact(BaseModel):
    keys: list[str] = Field(min_length=1)
    impact: int = Field(ge=1, le=5)


@command(
    "item.set_impact", "items", "Set the impact (1-5) of items", ItemSetImpact, permission="item.write_overlay", scope="items", scope_arg="keys", preview=True,
    example_args={"keys": ["WEB-1"], "impact": 4}, example_text="WEB-1 has impact 4",
)  # fmt: skip
async def item_set_impact(ctx: Ctx, a: ItemSetImpact) -> Outcome:
    return await item_bulk_set(ctx, ItemBulkSet(keys=a.keys, set={"impact": a.impact}))


class ItemSetWeight(BaseModel):
    key: str
    weight: Decimal
    scheme: str = Field(default="default", description="weight scheme name or id")


@command(
    "item.set_weight", "items", "Override an item's weight in a scheme", ItemSetWeight, permission="weight.set", scope="item", scope_arg="key",
    example_args={"key": "WEB-1", "weight": "5"}, example_text="set WEB-1 weight to 5",
)  # fmt: skip
async def item_set_weight(ctx: Ctx, a: ItemSetWeight) -> Outcome:
    return _res(
        await overlay.override_weight(a.key, a.scheme, overlay.WeightIn(value=a.weight), ctx.actor, ctx.uow)
    )


class ItemSetDates(BaseModel):
    key: str
    planned_start: date | None = None
    planned_end: date | None = None
    due_date: date | None = None


@command(
    "item.set_dates", "items", "Set planned start/end and due date", ItemSetDates, permission="item.write_overlay", scope="item", scope_arg="key",
    example_args={"key": "WEB-1", "planned_end": "2026-11-20"}, example_text="WEB-1 ends Nov 20",
)  # fmt: skip
async def item_set_dates(ctx: Ctx, a: ItemSetDates) -> Outcome:
    return await item_update(ctx, ItemUpdate(**a.model_dump(exclude_none=True)))


class ItemSetEstimate(BaseModel):
    key: str
    value: Decimal = Field(gt=0)
    unit: EstimationUnit = EstimationUnit.HOURS
    note: str | None = None


@command(
    "item.set_estimate", "items", "Record an estimate (the first is the original, later ones revisions)", ItemSetEstimate, permission="item.write_overlay", scope="item", scope_arg="key",
    example_args={"key": "WEB-1", "value": "6", "unit": "hours"}, example_text="estimate WEB-1 at 6 hours",
)  # fmt: skip
async def item_set_estimate(ctx: Ctx, a: ItemSetEstimate) -> Outcome:
    item = await core._item(ctx.uow, ctx.actor.ws.id, a.key)
    est = await items_svc.record_estimate(
        ctx.uow, item, person_id=ctx.actor.person.id, value=a.value, unit=a.unit, note=a.note
    )
    emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=item.id, event_type="item.updated", payload={"fields_changed": ["estimate"]})  # fmt: skip
    return Outcome(
        result={"key": item.key, "estimate": str(est.value), "unit": est.unit.value, "kind": est.kind.value}
    )


class ItemSetStage(BaseModel):
    key: str
    stage: str
    reason: str | None = None


@command(
    "item.set_stage", "items", "Move one item to a process stage", ItemSetStage, permission="item.write_overlay", scope="item", scope_arg="key",
    example_args={"key": "WEB-1", "stage": "review"}, example_text="move WEB-1 to review",
)  # fmt: skip
async def item_set_stage(ctx: Ctx, a: ItemSetStage) -> Outcome:
    return await stage_move(ctx, StageMove(keys=[a.key], stage=a.stage, reason=a.reason))


class ItemAssign(BaseModel):
    key: str
    person: str


@command(
    "item.assign", "items", "Assign an item to a person", ItemAssign, permission="item.write_overlay", scope="item", scope_arg="key",
    example_args={"key": "WEB-101", "person": "@sara"}, example_text="assign WEB-101 to @sara",
)  # fmt: skip
async def item_assign(ctx: Ctx, a: ItemAssign) -> Outcome:
    return await item_update(ctx, ItemUpdate(key=a.key, assignee=a.person))


class ItemSetState(BaseModel):
    key: str
    state: str = Field(description="workflow state key, e.g. in_progress, done")


@command(
    "item.set_state", "items", "Change an item's workflow state", ItemSetState, permission="item.write_overlay", scope="item", scope_arg="key", modes=("manager", "scrum_master", "pm", "admin"),
    example_args={"key": "WEB-101", "state": "done"}, example_text="close WEB-101",
)  # fmt: skip
async def item_set_state(ctx: Ctx, a: ItemSetState) -> Outcome:
    return _res(await core.change_state(a.key, core.StateIn(state=a.state), ctx.actor, ctx.uow))


class ItemReparent(BaseModel):
    keys: list[str] = Field(min_length=1)
    parent: str | None = Field(default=None, description="new parent key; null makes them top-level")


@command(
    "item.reparent", "items", "Move items under another parent", ItemReparent, permission="item.write_overlay", scope="items", scope_arg="keys", preview=True, destructive=True,
    example_args={"keys": ["WEB-101", "WEB-102"], "parent": "WEB-90"}, example_text="move WEB-101 and WEB-102 under WEB-90",
)  # fmt: skip
async def item_reparent(ctx: Ctx, a: ItemReparent) -> Outcome:
    its = await _items(ctx, a.keys)
    if ctx.preview:
        kids = int(
            await ctx.uow.session.scalar(
                select(func.count())
                .select_from(m.WorkItem)
                .where(m.WorkItem.parent_id.in_([i.id for i in its]), m.WorkItem.deleted_at.is_(None))
            )
            or 0
        )
        return Outcome(preview={"affected": len(its), "children": kids, "overrides_kept": 0, "will_create": [], "will_delete": [], "sample": [{"key": i.key, "parent": i.parent_id} for i in its[:5]]})  # fmt: skip
    done = [await core.move(i.key, core.MoveIn(parent=a.parent), ctx.actor, ctx.uow) for i in its]
    return Outcome(result={"moved": [d["key"] for d in done], "parent": a.parent})


class ItemMerge(BaseModel):
    sources: list[str] = Field(min_length=1, description="items folded into the target")
    target: str | None = Field(default=None, description="existing item that survives")
    new_title: str | None = Field(
        default=None, description="create a new item with this title instead of using a target"
    )
    allow_cross_project: bool = False


@command(
    "item.merge", "items", "Merge items: their time, evidence, links, tags and assignments move to the target", ItemMerge, permission="item.write_overlay",
    scope="items", scope_arg="sources", preview=True, destructive=True,
    example_args={"sources": ["WEB-101", "WEB-105"], "target": "WEB-101"}, example_text="merge WEB-101 and WEB-105 into one",
)  # fmt: skip
async def item_merge(ctx: Ctx, a: ItemMerge) -> Outcome:
    uow, ws = ctx.uow, ctx.actor.ws
    sources = await _items(ctx, a.sources)
    target = await core._item(uow, ws.id, a.target) if a.target else None
    if target is not None:
        await _need_item(ctx, "item.write_overlay", target)
    if a.allow_cross_project:
        for it in sources:
            await _need_item(ctx, "project.write", it)
    p = await merge_svc.plan(
        uow,
        ws,
        sources=sources,
        target=target,
        new_title=a.new_title,
        allow_cross_project=a.allow_cross_project,
    )
    if ctx.preview:
        return Outcome(preview=p.preview())

    async def create_target(title: str | None, template: m.WorkItem) -> m.WorkItem:
        proj = await uow.session.get(m.Project, template.project_id)
        assert proj is not None  # noqa: S101
        return await items_svc.create_item(
            uow, ws, proj, title=title or template.title, reporter_id=ctx.actor.person.id, assignee_id=template.assignee_id,
            type_id=template.type_id, parent=await uow.session.get(m.WorkItem, template.parent_id) if template.parent_id else None,
        )  # fmt: skip

    tgt = await merge_svc.apply(uow, ws, p, actor_id=ctx.actor.person.id, create_target=create_target)
    return Outcome(result={"target": tgt.key, "merged": [s.key for s in p.sources], **p.counts})


class ItemSplit(BaseModel):
    key: str
    titles: list[str] = Field(min_length=2, max_length=20)


@command(
    "item.split", "items", "Split an item into several siblings (the original stays, linked)", ItemSplit, permission="item.write_overlay", scope="item", scope_arg="key", preview=True,
    example_args={"key": "WEB-101", "titles": ["API part", "UI part"]}, example_text="split WEB-101 into API part and UI part",
)  # fmt: skip
async def item_split(ctx: Ctx, a: ItemSplit) -> Outcome:
    uow, ws = ctx.uow, ctx.actor.ws
    src = await core._item(uow, ws.id, a.key)
    if ctx.preview:
        return Outcome(
            preview={
                "affected": 1,
                "children": 0,
                "overrides_kept": 0,
                "will_create": [f"item “{t}”" for t in a.titles],
                "will_delete": [],
                "sample": [],
            }
        )
    proj = await uow.session.get(m.Project, src.project_id)
    assert proj is not None  # noqa: S101
    parent = await uow.session.get(m.WorkItem, src.parent_id) if src.parent_id else None
    made = [
        await items_svc.create_item(uow, ws, proj, title=t, reporter_id=ctx.actor.person.id, assignee_id=src.assignee_id, type_id=src.type_id, parent=parent)
        for t in a.titles
    ]  # fmt: skip
    for n in made:
        uow.session.add(
            m.WorkItemLink(id=new_id(), from_item_id=n.id, to_item_id=src.id, link_type=LinkType.RELATES_TO)
        )
    emit(
        uow, workspace_id=ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=src.id, event_type="item.split",
        payload={"after": {"into": [n.key for n in made]}},
    )  # fmt: skip
    return Outcome(result={"original": src.key, "created": [n.key for n in made]})


class ItemLink(BaseModel):
    from_: str = Field(alias="from")
    to: str
    type: LinkType = LinkType.RELATES_TO

    model_config = {"populate_by_name": True}


@command(
    "item.link", "items", "Link two items (blocks, depends on, relates to ...)", ItemLink, permission="item.write_overlay", scope="item", scope_arg="from",
    example_args={"from": "WEB-101", "to": "WEB-105", "type": "blocks"}, example_text="WEB-101 blocks WEB-105",
)  # fmt: skip
async def item_link(ctx: Ctx, a: ItemLink) -> Outcome:
    return _res(
        await item_extras.add_link(a.from_, item_extras.LinkIn(to=a.to, link_type=a.type), ctx.actor, ctx.uow)
    )


class ItemTag(BaseModel):
    keys: list[str] = Field(min_length=1)
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


@command(
    "item.tag", "items", "Add or remove tags on items", ItemTag, permission="item.write_overlay", scope="items", scope_arg="keys", preview=True,
    example_args={"keys": ["DATA-1", "DATA-2"], "add": ["q4"]}, example_text="tag DATA-1 and DATA-2 #q4",
)  # fmt: skip
async def item_tag(ctx: Ctx, a: ItemTag) -> Outcome:
    its = await _items(ctx, a.keys)
    if ctx.preview:
        return Outcome(preview={"affected": len(its), "children": 0, "overrides_kept": 0, "will_create": [f"tag #{t}" for t in a.add], "will_delete": [], "sample": [{"key": i.key} for i in its[:5]]})  # fmt: skip
    s = ctx.uow.session
    added = removed = 0
    for name in a.add:
        tag, _ = await items_svc.get_or_create_tag(ctx.uow, ctx.actor.ws, name.lstrip("#"), provisional=False)
        for it in its:
            have = await s.scalar(select(m.TagLink.id).where(m.TagLink.tag_id == tag.id, m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id == it.id, m.TagLink.deleted_at.is_(None)))  # fmt: skip
            if not have:
                s.add(
                    m.TagLink(id=new_id(), tag_id=tag.id, target_type=TargetType.WORK_ITEM, target_id=it.id)
                )
                added += 1
    for name in a.remove:
        found = await s.scalar(select(m.Tag).where(m.Tag.workspace_id == ctx.actor.ws.id, func.lower(m.Tag.name) == name.lstrip("#").lower(), m.Tag.deleted_at.is_(None)))  # fmt: skip
        if found is None:
            continue
        for link in (await s.scalars(select(m.TagLink).where(m.TagLink.tag_id == found.id, m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id.in_([i.id for i in its]), m.TagLink.deleted_at.is_(None)))).all():  # fmt: skip
            link.deleted_at = utcnow()
            removed += 1
    for it in its:
        if a.add:
            emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=it.id, event_type="tag.linked", payload={"after": {"tags": a.add}})  # fmt: skip
        if a.remove:
            emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=it.id, event_type="tag.unlinked", payload={"after": {"tags": a.remove}})  # fmt: skip
    return Outcome(result={"items": len(its), "links_added": added, "links_removed": removed})


class ItemBulkSet(BaseModel):
    keys: list[str] = Field(min_length=1)
    set: dict[str, Any] = Field(description="priority | impact | stage | weight")


@command(
    "item.bulk_set", "items", "Set priority, impact, stage or weight on many items with a propagation preview", ItemBulkSet, permission="item.write_overlay",
    scope="items", scope_arg="keys", preview=True, destructive=True,
    example_args={"keys": ["WEB-1", "WEB-2"], "set": {"priority": "high"}}, example_text="make WEB-1 and WEB-2 high priority",
)  # fmt: skip
async def item_bulk_set(ctx: Ctx, a: ItemBulkSet) -> Outcome:
    targets = await bulk_svc.resolve_targets(ctx.uow, ctx.actor.ws, ids=a.keys, filt=None)
    if ctx.preview:
        return Outcome(preview=await bulk_svc.preview(ctx.uow, ctx.actor.ws, targets, a.set))
    return _res(await overlay.bulk_apply(overlay.BulkEdit(ids=a.keys, set=a.set), ctx.actor, ctx.uow))


class MoveToIteration(BaseModel):
    keys: list[str] = Field(min_length=1)
    iteration: str = Field(description="iteration name or id within the items' project")


@command(
    "item.move_to_iteration", "iterations", "Put items into an iteration", MoveToIteration, permission="iteration.write", scope="items", scope_arg="keys", modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"keys": ["WEB-1"], "iteration": "Sprint 4"}, example_text="move WEB-1 to Sprint 4",
)  # fmt: skip
async def move_to_iteration(ctx: Ctx, a: MoveToIteration) -> Outcome:
    its = await _items(ctx, a.keys)
    s = ctx.uow.session
    for it in its:
        iteration = await s.scalar(select(m.Iteration).where(m.Iteration.project_id == it.project_id, (m.Iteration.name == a.iteration) | (m.Iteration.id == a.iteration)))  # fmt: skip
        if iteration is None:
            raise NotFound(
                "not_found",
                f"no iteration {a.iteration!r} in the project of {it.key}",
                pointer="/args/iteration",
            )
        it.iteration_id = iteration.id
        emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.WORK_ITEM, entity_id=it.id, event_type="item.updated", payload={"fields_changed": ["iteration_id"]})  # fmt: skip
    return Outcome(result={"moved": [i.key for i in its], "iteration": a.iteration})


# ------------------------------------------------------------------ teams & people


class TeamCreate(BaseModel):
    name: str
    kind: TeamKind = TeamKind.VIRTUAL
    lead: str | None = Field(default=None, description="@handle")
    members: list[str] = Field(default_factory=list, description="@handles")


@command(
    "team.create", "teams", "Create a team (org, virtual or ad hoc) with members", TeamCreate, permission="team.write", preview=True,
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
    team = await core.create_team(
        core.TeamIn(name=a.name, slug=_slug(a.name), kind=a.kind, lead_id=lead), ctx.actor, ctx.uow
    )
    for pid in dict.fromkeys(people):
        await core.add_membership(team["id"], core.MembershipIn(person_id=pid), ctx.actor, ctx.uow)
    emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.TEAM, entity_id=team["id"], event_type="team.members_changed", payload={"after": {"added": len(people)}})  # fmt: skip
    return Outcome(result={**team, "members": len(people)})


class TeamMembers(BaseModel):
    team: str = Field(description="team slug")
    members: list[str] = Field(min_length=1, description="@handles")


@command(
    "team.add_members", "teams", "Add people to a team", TeamMembers, permission="team.write", scope="workspace",
    example_args={"team": "payments", "members": ["@sara"]}, example_text="add @sara to team payments",
)  # fmt: skip
async def team_add_members(ctx: Ctx, a: TeamMembers) -> Outcome:
    team = await _team(ctx, a.team)
    n = 0
    for h in a.members:
        pid = await _person(ctx, h)
        live = await ctx.uow.session.scalar(select(m.Membership.id).where(m.Membership.team_id == team.id, m.Membership.person_id == pid, m.Membership.left_at.is_(None)))  # fmt: skip
        if live is None:
            await core.add_membership(
                team.id, core.MembershipIn(person_id=pid, role=MembershipRole.MEMBER), ctx.actor, ctx.uow
            )
            n += 1
    emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.TEAM, entity_id=team.id, event_type="team.members_changed", payload={"after": {"added": n}})  # fmt: skip
    return Outcome(result={"team": team.slug, "added": n})


@command(
    "team.remove_members", "teams", "Remove people from a team", TeamMembers, permission="team.write", destructive=True, preview=True,
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
    "reporting_line.set", "teams", "Set who a person reports to", ReportingLineSet, permission="reporting_line.write", modes=("admin", "manager"),
    example_args={"person": "@sara", "manager": "@mia", "is_primary": True}, example_text="@sara reports to @mia",
)  # fmt: skip
async def reporting_line_set(ctx: Ctx, a: ReportingLineSet) -> Outcome:
    body = core.ReportingLineIn(
        person_id=await _person(ctx, a.person),
        manager_id=await _person(ctx, a.manager),
        kind=a.kind,
        is_primary=a.is_primary,
    )
    return _res(await core.add_reporting_line(body, ctx.actor, ctx.uow))


# ------------------------------------------------------------------ dimensions & tags


class TagMerge(BaseModel):
    tag: str
    into: str


@command(
    "tag.merge", "tags", "Merge one tag into another", TagMerge, permission="classification.write", destructive=True,
    example_args={"tag": "q-4", "into": "q4"}, example_text="merge tag q-4 into q4",
)  # fmt: skip
async def tag_merge(ctx: Ctx, a: TagMerge) -> Outcome:
    ids = []
    for name in (a.tag, a.into):
        t = await ctx.uow.session.scalar(select(m.Tag).where(m.Tag.workspace_id == ctx.actor.ws.id, (func.lower(m.Tag.name) == name.lstrip("#").lower()) | (m.Tag.id == name), m.Tag.deleted_at.is_(None)))  # fmt: skip
        if t is None:
            raise NotFound("not_found", f"unknown tag {name!r}")
        ids.append(t.id)
    return _res(await core.merge_tags(ids[0], ids[1], ctx.actor, ctx.uow))


class TagCreate(BaseModel):
    name: str


@command(
    "tag.create", "tags", "Create a tag", TagCreate, permission="classification.write", modes=("manager", "pm", "admin", "scrum_master"),
    example_args={"name": "q4"}, example_text="new tag q4",
)  # fmt: skip
async def tag_create(ctx: Ctx, a: TagCreate) -> Outcome:
    tag, created = await items_svc.get_or_create_tag(
        ctx.uow, ctx.actor.ws, a.name.lstrip("#"), provisional=False
    )
    if created:
        emit(ctx.uow, workspace_id=ctx.actor.ws.id, actor_id=ctx.actor.person.id, entity_type=TargetType.PROJECT, entity_id=ctx.actor.ws.id, event_type="tag.created", payload={"after": {"name": tag.name}})  # fmt: skip
    return Outcome(result={"id": tag.id, "name": tag.name, "created": created})


class DimensionValueAdd(BaseModel):
    dimension: str
    name: str
    parent: str | None = None


@command(
    "dimension.value.add", "tags", "Add a value to a dimension (customer, theme, ...)", DimensionValueAdd, permission="dimension.admin",
    example_args={"dimension": "theme", "name": "Checkout"}, example_text="add Checkout to theme",
)  # fmt: skip
async def dimension_value_add(ctx: Ctx, a: DimensionValueAdd) -> Outcome:
    return _res(
        await overlay.create_value(
            a.dimension,
            overlay.ValueIn(name=a.name, key=_slug(a.name), parent_id=a.parent),
            ctx.actor,
            ctx.uow,
        )
    )


class Classify(BaseModel):
    target: str = Field(description="item or project key")
    dimension: str
    value: str
    target_type: Literal["work_item", "project", "product"] = "work_item"


@command(
    "classify", "tags", "Classify an item or project on a dimension", Classify, permission="classification.write", scope="item", scope_arg="target",
    example_args={"target": "WEB-101", "dimension": "theme", "value": "checkout"}, example_text="classify WEB-101 as theme checkout",
)  # fmt: skip
async def classify(ctx: Ctx, a: Classify) -> Outcome:
    body = overlay.ClassifyIn(
        target_type=TargetType(a.target_type), target_id=a.target, dimension=a.dimension, value=a.value
    )
    return _res(await overlay.classify(body, ctx.actor, ctx.uow))


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
    return _res(await governance.acknowledge(a.id, ctx.actor, ctx.uow))


@command(
    "violation.resolve", "governance", "Mark a violation resolved", ViolationNote, permission="violation.resolve",
    example_args={"id": "<violation id>", "note": "fixed"}, example_text="resolve violation, fixed",
)  # fmt: skip
async def violation_resolve(ctx: Ctx, a: ViolationNote) -> Outcome:
    return _res(await governance.resolve(a.id, governance.Note(note=a.note), ctx.actor, ctx.uow))


@command(
    "violation.waive", "governance", "Waive a violation with a reason", ViolationReason, permission="violation.waive",
    example_args={"id": "<violation id>", "reason": "customer approved exception"}, example_text="waive violation, customer approved exception",
)  # fmt: skip
async def violation_waive(ctx: Ctx, a: ViolationReason) -> Outcome:
    return _res(await governance.waive(a.id, governance.Reason(reason=a.reason), ctx.actor, ctx.uow))


class HabitCoachNote(BaseModel):
    habit: str
    note: str = Field(min_length=1)


@command(
    "habit.coach_note", "governance", "Write a coaching note on a habit (visible to managers and coaches only)", HabitCoachNote, permission="habit.coach", modes=("coach", "manager", "admin"),
    example_args={"habit": "<habit id>", "note": "pair on estimates"}, example_text="coaching note on habit: pair on estimates",
)  # fmt: skip
async def habit_coach_note(ctx: Ctx, a: HabitCoachNote) -> Outcome:
    return _res(
        await governance.patch_habit(a.habit, governance.HabitPatch(coaching_note=a.note), ctx.actor, ctx.uow)
    )


class HabitSetStatus(BaseModel):
    habit: str
    status: Literal["open", "acknowledged", "improving", "resolved", "dismissed"]


@command(
    "habit.set_status", "governance", "Set a habit's coaching status", HabitSetStatus, permission="habit.coach", modes=("coach", "manager", "admin"),
    example_args={"habit": "<habit id>", "status": "improving"}, example_text="mark habit as improving",
)  # fmt: skip
async def habit_set_status(ctx: Ctx, a: HabitSetStatus) -> Outcome:
    return _res(
        await governance.patch_habit(
            a.habit, governance.HabitPatch.model_validate({"status": a.status}), ctx.actor, ctx.uow
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
    "policy.activate", "governance", "Activate a tested policy", PolicyRef, permission="policy.write", modes=("admin", "manager"),
    example_args={"policy": "stale-in-progress"}, example_text="activate policy stale-in-progress",
)  # fmt: skip
async def policy_activate(ctx: Ctx, a: PolicyRef) -> Outcome:
    return _res(
        await governance.activate_policy(await _policy_id(ctx, a.policy), ctx.actor, ctx.uow, force=a.force)
    )


@command(
    "policy.deactivate", "governance", "Deactivate a policy", PolicyRef, permission="policy.write", modes=("admin", "manager"),
    example_args={"policy": "stale-in-progress"}, example_text="deactivate policy stale-in-progress",
)  # fmt: skip
async def policy_deactivate(ctx: Ctx, a: PolicyRef) -> Outcome:
    return _res(await governance.deactivate_policy(await _policy_id(ctx, a.policy), ctx.actor, ctx.uow))


# ------------------------------------------------------------------ iterations & milestones


class IterationCreate(BaseModel):
    project: str
    name: str
    start_date: date
    end_date: date
    goal: str | None = None


@command(
    "iteration.create", "iterations", "Create an iteration", IterationCreate, permission="iteration.write", scope="project", scope_arg="project", modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"project": "WEB", "name": "Sprint 4", "start_date": "2026-11-02", "end_date": "2026-11-13"}, example_text="new sprint 4 on WEB from Nov 2 to Nov 13",
)  # fmt: skip
async def iteration_create(ctx: Ctx, a: IterationCreate) -> Outcome:
    return _res(
        await core.create_iteration(
            a.project, core.IterationIn(**a.model_dump(exclude={"project"})), ctx.actor, ctx.uow
        )
    )


class IterationRef(BaseModel):
    project: str
    iteration: str = Field(description="iteration name or id")


async def _iteration(ctx: Ctx, project: str, ref: str) -> m.Iteration:
    p = await core._project(ctx.uow, ctx.actor.ws.id, project)
    it = await ctx.uow.session.scalar(select(m.Iteration).where(m.Iteration.project_id == p.id, (m.Iteration.name == ref) | (m.Iteration.id == ref)))  # fmt: skip
    if it is None:
        raise NotFound("not_found", f"no iteration {ref!r} in {p.key}", pointer="/args/iteration")
    return it


@command(
    "iteration.start", "iterations", "Start a planned iteration", IterationRef, permission="iteration.write", scope="project", scope_arg="project", modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"project": "WEB", "iteration": "Sprint 4"}, example_text="start sprint 4 on WEB",
)  # fmt: skip
async def iteration_start(ctx: Ctx, a: IterationRef) -> Outcome:
    it = await _iteration(ctx, a.project, a.iteration)
    return _res(
        await core.iteration_state(
            it.id, core.IterationState_(state=IterationState.ACTIVE), ctx.actor, ctx.uow
        )
    )


@command(
    "iteration.close", "iterations", "Close the active iteration", IterationRef, permission="iteration.write", scope="project", scope_arg="project", destructive=True, modes=("scrum_master", "pm", "manager", "admin"),
    example_args={"project": "WEB", "iteration": "Sprint 4"}, example_text="close sprint 4 on WEB",
)  # fmt: skip
async def iteration_close(ctx: Ctx, a: IterationRef) -> Outcome:
    it = await _iteration(ctx, a.project, a.iteration)
    return _res(
        await core.iteration_state(
            it.id, core.IterationState_(state=IterationState.CLOSED), ctx.actor, ctx.uow
        )
    )


class MilestoneCreate(BaseModel):
    project: str
    name: str
    due_date: date
    kind: MilestoneKind = MilestoneKind.DEADLINE


@command(
    "milestone.create", "iterations", "Create a milestone", MilestoneCreate, permission="milestone.write", scope="project", scope_arg="project", modes=("pm", "manager", "admin"),
    example_args={"project": "WEB", "name": "Beta", "due_date": "2026-12-01"}, example_text="milestone Beta on WEB due Dec 1",
)  # fmt: skip
async def milestone_create(ctx: Ctx, a: MilestoneCreate) -> Outcome:
    return _res(
        await core.create_milestone(
            a.project, core.MilestoneIn(**a.model_dump(exclude={"project"})), ctx.actor, ctx.uow
        )
    )


class MilestoneRef(BaseModel):
    project: str
    milestone: str = Field(description="milestone name or id")


@command(
    "milestone.complete", "iterations", "Complete a milestone", MilestoneRef, permission="milestone.write", scope="project", scope_arg="project", modes=("pm", "manager", "admin"),
    example_args={"project": "WEB", "milestone": "Beta"}, example_text="milestone Beta on WEB is done",
)  # fmt: skip
async def milestone_complete(ctx: Ctx, a: MilestoneRef) -> Outcome:
    p = await core._project(ctx.uow, ctx.actor.ws.id, a.project)
    ms = await ctx.uow.session.scalar(select(m.Milestone).where(m.Milestone.project_id == p.id, (m.Milestone.name == a.milestone) | (m.Milestone.id == a.milestone)))  # fmt: skip
    if ms is None:
        raise NotFound("not_found", f"no milestone {a.milestone!r} in {p.key}", pointer="/args/milestone")
    return _res(await core.complete_milestone(ms.id, ctx.actor, ctx.uow))


# ------------------------------------------------------------------ personal (any role)


class ContextNoteAdd(BaseModel):
    title: str
    body: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope: str | None = Field(default=None, description="project key / team slug the note applies to")
    pinned: bool = False


@command(
    "context_note.add", "personal", "Add a context note for a project, team or the workspace", ContextNoteAdd, permission="context_note.write", modes=("manager", "coach", "pm", "admin", "scrum_master"),
    example_args={"title": "Freeze", "body": "Code freeze from Dec 1", "scope_type": "workspace"}, example_text="note: code freeze from Dec 1",
)  # fmt: skip
async def context_note_add(ctx: Ctx, a: ContextNoteAdd) -> Outcome:
    sid = ctx.actor.ws.id
    if a.scope_type == ScopeType.PROJECT and a.scope:
        sid = (await core._project(ctx.uow, ctx.actor.ws.id, a.scope)).id
    elif a.scope_type == ScopeType.TEAM and a.scope:
        sid = (await _team(ctx, a.scope)).id
    body = core.ContextNoteIn(
        scope_type=a.scope_type, scope_id=sid, title=a.title, body=a.body, pinned=a.pinned
    )
    return _res(await core.create_note(body, ctx.actor, ctx.uow))


class DailyLogConfirm(BaseModel):
    day: date | None = None


@command(
    "dailylog.confirm", "personal", "Confirm your daily log", DailyLogConfirm, permission=None, modes=("reporter", "manager", "scrum_master", "coach", "pm", "admin"),
    example_args={}, example_text="confirm my day",
)  # fmt: skip
async def dailylog_confirm(ctx: Ctx, a: DailyLogConfirm) -> Outcome:
    from tt_server.api.routers import capture
    from tt_server.domain.capture import dailylog

    day = a.day or utcnow().date()
    row = await ctx.uow.session.scalar(select(m.DailyLog).where(m.DailyLog.workspace_id == ctx.actor.ws.id, m.DailyLog.person_id == ctx.actor.person.id, m.DailyLog.date == day))  # fmt: skip
    if row is None:
        raise NotFound("not_found", f"no daily log for {day}", pointer="/args/day")
    _ = capture
    await dailylog.confirm(ctx.uow, ctx.actor.ws, row, ctx.actor.person.id)
    return Outcome(result={"id": row.id, "date": day.isoformat(), "status": "confirmed"})


async def _need_item(ctx: Ctx, permission: str, item: m.WorkItem) -> None:
    from tt_server.api.authz import check_item

    await check_item(ctx.actor, ctx.uow, permission, item)
