"""Items: create, overlay edits, merge, split, link, tag, bulk. (Phase 14)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    EstimationUnit,
    LinkType,
    Priority,
    TargetType,
)

from tt_server.api.commands.ops._common import _items, _need_item, _person, _res, _slug, _team  # noqa: F401
from tt_server.api.commands.ops.process import StageMove, stage_move
from tt_server.api.commands.registry import Ctx, Outcome, command
from tt_server.api.routers import (
    core,
    item_extras,
    overlay,
)
from tt_server.db import models as m
from tt_server.domain.common import emit
from tt_server.domain.errors import Invalid
from tt_server.domain.items import merge as merge_svc
from tt_server.domain.items import service as items_svc
from tt_server.domain.overlay import bulk as bulk_svc

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
