"""Projects, workflows and item types."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import EventSource, ItemTypeKey, ProjectKind, StateCategory, TargetType, Visibility

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Conflict, Fault, Invalid

KEY_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")


async def default_workflow(session: AsyncSession, workspace_id: str) -> m.Workflow:
    wf = await session.scalar(
        select(m.Workflow)
        .where(
            m.Workflow.workspace_id == workspace_id, m.Workflow.is_default, m.Workflow.deleted_at.is_(None)
        )
        .order_by(m.Workflow.created_at)
    )
    if wf is None:
        raise Fault("no_default_workflow", "workspace has no default workflow")
    return wf


async def item_type(
    session: AsyncSession, workspace_id: str, key: ItemTypeKey, project_id: str | None = None
) -> m.WorkItemType:
    stmt = select(m.WorkItemType).where(
        m.WorkItemType.workspace_id == workspace_id,
        m.WorkItemType.key == key,
        m.WorkItemType.deleted_at.is_(None),
    )
    rows = (await session.scalars(stmt)).all()
    for row in rows:  # project-specific type wins over the workspace builtin
        if project_id and row.project_id == project_id:
            return row
    for row in rows:
        if row.project_id is None:
            return row
    raise Fault("no_item_type", f"item type {key.value!r} missing")


async def workflow_states(session: AsyncSession, workflow_id: str) -> list[m.WorkflowState]:
    return list(
        (
            await session.scalars(
                select(m.WorkflowState)
                .where(m.WorkflowState.workflow_id == workflow_id)
                .order_by(m.WorkflowState.position)
            )
        ).all()
    )


def pick_state(
    states: list[m.WorkflowState], category: StateCategory, *, initial: bool = False, terminal: bool = False
) -> m.WorkflowState:
    if initial:
        for s in states:
            if s.is_initial:
                return s
    if terminal:
        for s in states:
            if s.is_terminal:
                return s
    for s in states:
        if s.category == category:
            return s
    return states[0]


async def create_project(
    uow: UnitOfWork,
    ws: m.Workspace,
    *,
    key: str,
    name: str,
    owner_id: str,
    actor_id: str,
    kind: ProjectKind = ProjectKind.STANDARD,
    visibility: Visibility = Visibility.MEMBERS,
    **extra: Any,
) -> m.Project:
    if kind != ProjectKind.INBOX:
        key = key.upper()
    if kind != ProjectKind.INBOX and not KEY_RE.match(key):
        raise Invalid(
            "invalid_key", "project key must be upper-case letters, digits, '_' or '-'", pointer="/key"
        )
    session = uow.session
    if await session.scalar(
        select(m.Project.id).where(m.Project.workspace_id == ws.id, m.Project.key == key)
    ):
        raise Conflict("project_exists", f"project {key} already exists", pointer="/key")
    wf = await default_workflow(session, ws.id)
    task = await item_type(session, ws.id, ItemTypeKey.TASK)
    if kind != ProjectKind.INBOX and "default_process_id" not in extra:
        from tt_server.domain.overlay.process import ensure_default_process

        extra["default_process_id"] = (await ensure_default_process(session, ws.id, owner_id)).id
    project = m.Project(
        id=new_id(),
        workspace_id=ws.id,
        key=key,
        name=name,
        owner_id=owner_id,
        workflow_id=wf.id,
        default_item_type_id=task.id,
        kind=kind,
        visibility=visibility,
        **extra,
    )
    session.add(project)
    await session.flush()
    session.add(m.ProjectKeySequence(project_id=project.id, next=1))
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PROJECT,
        entity_id=project.id,
        event_type="project.created",
        payload={"after": {"key": key, "name": name, "kind": kind.value}},
        source=EventSource.OVERLAY,
    )
    return project
