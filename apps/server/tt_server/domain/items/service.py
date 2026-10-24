"""Work items: keys, paths, state transitions, tags, estimates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id, utcnow
from tt_schema.enums import (
    AssignmentRole,
    EstimateKind,
    EstimationUnit,
    EventSource,
    StateCategory,
    TargetType,
    ValueSource,
)

from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, ltree_label
from tt_server.domain.projects.service import pick_state, workflow_states

# Extension point (Phase 3 stage <-> state sync): called after a state *category* change.
StateCategoryHook = Callable[[UnitOfWork, m.WorkItem, StateCategory, StateCategory], Awaitable[None]]
on_state_category_changed: list[StateCategoryHook] = []


async def next_item_key(session: AsyncSession, project: m.Project) -> str:
    """`{project.key}-{n}` from a per-project counter updated under a row lock (architecture §4.7)."""
    await session.execute(
        text(
            """
            INSERT INTO project_key_sequences (project_id, next)
            SELECT :pid, COALESCE(MAX(NULLIF(substring(key from '-([0-9]+)$'), '')::int), 0) + 1
            FROM work_items WHERE project_id = :pid
            ON CONFLICT (project_id) DO NOTHING
            """
        ),
        {"pid": project.id},
    )
    n = await session.scalar(
        text("UPDATE project_key_sequences SET next = next + 1 WHERE project_id = :pid RETURNING next - 1"),
        {"pid": project.id},
    )
    return f"{project.key}-{n}"


async def type_level(session: AsyncSession, type_id: str) -> int:
    return int(await session.scalar(select(m.WorkItemType.level).where(m.WorkItemType.id == type_id)) or 0)


async def validate_nesting(
    session: AsyncSession, ws: m.Workspace, child_type_id: str, parent: m.WorkItem
) -> None:
    """Child level must exceed the parent's unless `settings.allow_same_level_nesting`."""
    child, par = await type_level(session, child_type_id), await type_level(session, parent.type_id)
    allow_same = bool((ws.settings or {}).get("allow_same_level_nesting"))
    if child < par or (child == par and not allow_same):
        raise ApiError(
            400, "invalid_nesting", "child type level must be deeper than the parent's", pointer="/parent_id"
        )


async def create_item(
    uow: UnitOfWork,
    ws: m.Workspace,
    project: m.Project,
    *,
    title: str,
    reporter_id: str,
    assignee_id: str | None = None,
    team_id: str | None = None,
    type_id: str | None = None,
    parent: m.WorkItem | None = None,
    provisional: bool = False,
    source: dict[str, object] | None = None,
    key: str | None = None,
    inherit: bool = True,
    default_stage: bool = True,
    **fields: object,
) -> m.WorkItem:
    session = uow.session
    if "priority" not in fields:
        fields.setdefault("priority_source", ValueSource.INHERITED)
    states = await workflow_states(session, project.workflow_id)
    state = pick_state(states, StateCategory.OPEN, initial=True)
    type_id = type_id or project.default_item_type_id
    if parent is not None:
        await validate_nesting(session, ws, type_id, parent)
    item_id = new_id()
    path = ltree_label(item_id) if parent is None else f"{parent.path}.{ltree_label(item_id)}"
    item = m.WorkItem(
        id=item_id,
        project_id=project.id,
        key=key or await next_item_key(session, project),
        type_id=type_id,
        title=title,
        state_id=state.id,
        reporter_id=reporter_id,
        assignee_id=assignee_id,
        team_id=team_id,
        parent_id=parent.id if parent else None,
        path=path,
        is_provisional=provisional,
        source=source,
        **fields,
    )
    session.add(item)
    await session.flush()
    if assignee_id:
        session.add(
            m.Assignment(id=new_id(), work_item_id=item.id, person_id=assignee_id, role=AssignmentRole.OWNER)
        )
    if inherit:
        from tt_server.domain.overlay import propagation

        await propagation.inherit_on_create(uow, ws, item, parent=parent, default_stage=default_stage)
    return item


async def set_state(
    uow: UnitOfWork, item: m.WorkItem, state: m.WorkflowState, *, at: datetime | None = None
) -> tuple[StateCategory, StateCategory]:
    """Move to `state`, maintaining actual_start / resolved_at / closed_at. Returns (old, new) categories."""
    at = at or utcnow()
    old_state = await uow.session.get(m.WorkflowState, item.state_id)
    old_cat = old_state.category if old_state else StateCategory.OPEN
    item.state_id = state.id
    if state.category == StateCategory.ACTIVE and item.actual_start is None:
        item.actual_start = at
    if state.category == StateCategory.DONE:
        item.resolved_at = at
        item.closed_at = at
        item.actual_end = at
        item.progress_pct = 100
    elif old_cat == StateCategory.DONE:
        item.resolved_at = None
        item.closed_at = None
        item.actual_end = None
    if old_cat != state.category:
        for hook in on_state_category_changed:
            await hook(uow, item, old_cat, state.category)
    return old_cat, state.category


async def move_item(session: AsyncSession, item: m.WorkItem, new_parent: m.WorkItem | None) -> None:
    """Re-parent and rewrite `path` of the whole subtree in one statement."""
    old_prefix = item.path
    own = ltree_label(item.id)
    new_prefix = own if new_parent is None else f"{new_parent.path}.{own}"
    if new_parent is not None and (
        new_parent.path == old_prefix or new_parent.path.startswith(old_prefix + ".")
    ):
        raise ApiError(400, "cycle", "an item cannot be moved under its own descendant", pointer="/parent_id")
    await session.execute(
        text(
            "UPDATE work_items SET path = CAST(:np AS ltree) || subpath(path, nlevel(CAST(:op AS ltree))) "
            "WHERE path <@ CAST(:op AS ltree) AND path <> CAST(:op AS ltree)"
        ),
        {"np": new_prefix, "op": old_prefix},
    )
    item.parent_id = new_parent.id if new_parent else None
    item.path = new_prefix
    await session.flush()


async def get_or_create_tag(
    uow: UnitOfWork, ws: m.Workspace, name: str, *, provisional: bool
) -> tuple[m.Tag, bool]:
    session = uow.session
    tag = await session.scalar(
        select(m.Tag).where(
            m.Tag.workspace_id == ws.id, func.lower(m.Tag.name) == name.lower(), m.Tag.deleted_at.is_(None)
        )
    )
    if tag:
        return tag, False
    tag = m.Tag(id=new_id(), workspace_id=ws.id, name=name, is_provisional=provisional)
    session.add(tag)
    await session.flush()
    return tag, True


async def record_estimate(
    uow: UnitOfWork,
    item: m.WorkItem,
    *,
    person_id: str,
    value: Decimal,
    unit: EstimationUnit,
    note: str | None = None,
) -> m.Estimate:
    """History row; the first estimate is `original`, later ones `revised`. Latest original syncs the item."""
    session = uow.session
    exists = await session.scalar(
        select(m.Estimate.id)
        .where(m.Estimate.work_item_id == item.id, m.Estimate.kind == EstimateKind.ORIGINAL)
        .limit(1)
    )
    kind = EstimateKind.REVISED if exists else EstimateKind.ORIGINAL
    row = m.Estimate(
        id=new_id(), work_item_id=item.id, person_id=person_id, value=value, unit=unit, kind=kind, note=note
    )
    session.add(row)
    if kind == EstimateKind.ORIGINAL:
        item.estimate_value, item.estimate_unit = value, unit
    return row


def event_source_for(capture: bool) -> EventSource:
    return EventSource.CLIENT if capture else EventSource.OVERLAY


__all__ = [
    "create_item",
    "emit",
    "get_or_create_tag",
    "move_item",
    "next_item_key",
    "on_state_category_changed",
    "record_estimate",
    "set_state",
    "validate_nesting",
]


async def change_project(
    uow: UnitOfWork, ws: m.Workspace, item: m.WorkItem, project: m.Project, *, actor_id: str
) -> None:
    """Move an item (typically out of an Inbox): new key in the destination, old key kept in external_refs."""
    from tt_server.domain.overlay import propagation

    if item.project_id == project.id:
        return
    session = uow.session
    old_key, old_project_id = item.key, item.project_id
    old_state = await session.get(m.WorkflowState, item.state_id)
    states = await workflow_states(session, project.workflow_id)
    new_state = pick_state(states, old_state.category if old_state else StateCategory.OPEN)
    item.external_refs = {**(item.external_refs or {}), "previous_key": old_key}
    item.key = await next_item_key(session, project)
    item.project_id, item.state_id, item.is_provisional = project.id, new_state.id, False
    await session.flush()
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.WORK_ITEM,
        entity_id=item.id,
        event_type="item.project_changed",
        payload={
            "before": {"project_id": old_project_id, "key": old_key},
            "after": {"project_id": project.id, "key": item.key},
        },
        source=EventSource.OVERLAY,
        correlation_id=uow.context.get("correlation_id"),
    )
    parent = await session.get(m.WorkItem, item.parent_id) if item.parent_id else None
    await propagation.inherit_on_create(uow, ws, item, parent=parent, actor=actor_id)
