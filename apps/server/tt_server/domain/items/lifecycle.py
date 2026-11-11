"""Work-item commands behind `/items`: create, change state, re-parent; and tag merging."""

from __future__ import annotations

from sqlalchemy import select
from tt_schema.base import utcnow
from tt_schema.enums import EventSource, Priority, StateCategory, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, emit_admin
from tt_server.domain.errors import Invalid, NotFound
from tt_server.domain.items import service as items
from tt_server.domain.projects.service import workflow_states


async def create_item(
    uow: UnitOfWork,
    ws: m.Workspace,
    actor_id: str,
    project: m.Project,
    *,
    title: str,
    assignee_id: str | None,
    type_id: str | None,
    parent: m.WorkItem | None,
    description: str | None,
    priority: Priority,
) -> m.WorkItem:
    """Create an item reported by the actor (assignee defaults to the actor) and record `item.created`."""
    item = await items.create_item(
        uow,
        ws,
        project,
        title=title,
        reporter_id=actor_id,
        assignee_id=assignee_id or actor_id,
        type_id=type_id,
        parent=parent,
        description=description,
        priority=priority,
    )
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.created",
        payload={"after": {"key": item.key, "title": item.title}},
        source=EventSource.OVERLAY,
    )
    return item


async def change_state(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, item: m.WorkItem, state_key: str
) -> m.WorkItem:
    """Move an item to a state of its project's workflow; leaving DONE records a reopen."""
    proj = await uow.session.get(m.Project, item.project_id)
    if proj is None:
        raise NotFound("not_found", "project not found")
    states = await workflow_states(uow.session, proj.workflow_id)
    target = next((s for s in states if s.key == state_key), None)
    if target is None:
        raise Invalid(
            "unknown_state", f"state {state_key!r} is not in the project workflow", pointer="/state"
        )
    old, new = await items.set_state(uow, item, target)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.reopened"
        if old == StateCategory.DONE and new != StateCategory.DONE
        else "item.state_changed",
        payload={"before": {"category": old.value}, "after": {"category": new.value, "state": target.key}},
    )
    return item


async def reparent(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, item: m.WorkItem, parent: m.WorkItem | None
) -> m.WorkItem:
    """Move an item (and its subtree) under `parent`, or to the top level when None."""
    if parent is not None:
        if parent.project_id != item.project_id:
            raise Invalid(
                "cross_project_move",
                "an item can only move inside its own project; its key, workflow and plan belong to it",
                pointer="/parent",
            )
        await items.validate_nesting(uow.session, ws, item.type_id, parent)
    await items.move_item(uow.session, item, parent)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.parent_changed",
        payload={"after": {"parent_id": item.parent_id}},
    )
    return item


async def merge_tags(
    uow: UnitOfWork, ws: m.Workspace, actor_id: str, tag_id: str, other_id: str
) -> tuple[m.Tag, int]:
    """Fold tag `tag_id` into `other_id`: links move (duplicates drop) and the source tag is deleted."""
    src, dst = (await uow.session.get(m.Tag, tag_id), await uow.session.get(m.Tag, other_id))
    if not src or not dst or src.workspace_id != ws.id or dst.workspace_id != ws.id or src.id == dst.id:
        raise NotFound("not_found", "tag not found")
    have = set(
        (await uow.session.scalars(select(m.TagLink.target_id).where(m.TagLink.tag_id == dst.id))).all()
    )
    moved = 0
    for link in (await uow.session.scalars(select(m.TagLink).where(m.TagLink.tag_id == src.id))).all():
        if link.target_id in have:
            link.deleted_at = utcnow()
        else:
            link.tag_id = dst.id
            moved += 1
    src.deleted_at = utcnow()
    emit_admin(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        event_type="tag.merged",
        payload={"before": {"tag_id": src.id}, "after": {"tag_id": dst.id, "links_moved": moved}},
    )
    return dst, moved
