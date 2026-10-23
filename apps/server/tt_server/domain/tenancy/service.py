"""Workspace, person and member provisioning."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id
from tt_schema.enums import (
    EstimationUnit,
    EventSource,
    ItemTypeKey,
    ProjectKind,
    StateCategory,
    TargetType,
    Visibility,
)

from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.overlay.mirrors import ensure_builtin_dimensions
from tt_server.domain.overlay.process import ensure_default_process
from tt_server.domain.overlay.weights import ensure_default_scheme
from tt_server.domain.projects.service import create_project

DEFAULT_SETTINGS: dict[str, Any] = {
    "time_zone": "UTC",
    "daily_log_time": "17:30",
    "glossary": {},
    "allow_same_level_nesting": False,
}
# key, level — builtin work item types (Phase 1 §1.2)
BUILTIN_TYPES = [
    (ItemTypeKey.EPIC, 0),
    (ItemTypeKey.STORY, 1),
    (ItemTypeKey.TASK, 2),
    (ItemTypeKey.BUG, 2),
    (ItemTypeKey.SUBTASK, 3),
    (ItemTypeKey.SPIKE, 2),
    (ItemTypeKey.PHASE, 0),
    (ItemTypeKey.DELIVERABLE, 1),
    (ItemTypeKey.DECISION, 2),
]
SIMPLE_STATES = [
    ("todo", "To do", StateCategory.OPEN, True, False),
    ("doing", "Doing", StateCategory.ACTIVE, False, False),
    ("done", "Done", StateCategory.DONE, False, True),
]


async def get_or_create_person(
    uow: UnitOfWork, *, display_name: str, email: str, time_zone: str = "UTC"
) -> m.Person:
    person = await uow.session.scalar(select(m.Person).where(m.Person.email == email))
    if person is None:
        person = m.Person(id=new_id(), display_name=display_name, email=email, time_zone=time_zone)
        uow.session.add(person)
        await uow.session.flush()
    return person


async def create_workspace(
    uow: UnitOfWork, *, name: str, slug: str, creator_id: str, settings: dict[str, Any] | None = None
) -> m.Workspace:
    if await uow.session.scalar(select(m.Workspace.id).where(m.Workspace.slug == slug)):
        raise ApiError(409, "workspace_exists", f"workspace {slug!r} already exists", pointer="/slug")
    ws = m.Workspace(id=new_id(), name=name, slug=slug, settings={**DEFAULT_SETTINGS, **(settings or {})})
    uow.session.add(ws)
    await uow.session.flush()
    wf = m.Workflow(id=new_id(), workspace_id=ws.id, name="Simple", is_default=True)
    uow.session.add(wf)
    await uow.session.flush()
    for pos, (key, state_name, category, initial, terminal) in enumerate(SIMPLE_STATES):
        uow.session.add(
            m.WorkflowState(
                id=new_id(),
                workflow_id=wf.id,
                key=key,
                name=state_name,
                category=category,
                position=pos,
                is_initial=initial,
                is_terminal=terminal,
            )
        )
    for key, level in BUILTIN_TYPES:
        uow.session.add(
            m.WorkItemType(
                id=new_id(),
                workspace_id=ws.id,
                key=key,
                name=key.value.title(),
                level=level,
                estimation_unit=EstimationUnit.HOURS,
                default_workflow_id=wf.id,
            )
        )
    await uow.session.flush()
    await ensure_builtin_dimensions(uow.session, ws.id)
    await ensure_default_process(uow.session, ws.id, creator_id)
    await ensure_default_scheme(uow.session, ws.id)
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=creator_id,
        entity_type=TargetType.PERSON,
        entity_id=creator_id,
        event_type="workspace.created",
        payload={"after": {"workspace_id": ws.id, "slug": slug, "name": name}},
        source=EventSource.OVERLAY,
    )
    return ws


async def add_member(
    uow: UnitOfWork, ws: m.Workspace, person: m.Person, *, handle: str, actor_id: str
) -> m.WorkspaceMember:
    """Create the membership and its private Inbox project (Phase 1 §1.1)."""
    if not handle.startswith("@"):
        handle = "@" + handle
    session: AsyncSession = uow.session
    clash = await session.scalar(
        select(m.WorkspaceMember.id).where(
            m.WorkspaceMember.workspace_id == ws.id,
            (m.WorkspaceMember.handle == handle) | (m.WorkspaceMember.person_id == person.id),
        )
    )
    if clash:
        raise ApiError(409, "member_exists", "handle or person is already a member", pointer="/handle")
    member = m.WorkspaceMember(id=new_id(), workspace_id=ws.id, person_id=person.id, handle=handle)
    session.add(member)
    await session.flush()
    inbox = await create_project(
        uow,
        ws,
        key=f"INBOX-{handle[1:]}",
        name=f"Inbox {handle}",
        owner_id=person.id,
        actor_id=actor_id,
        kind=ProjectKind.INBOX,
        visibility=Visibility.PRIVATE,
    )
    member.inbox_project_id = inbox.id
    emit(
        uow,
        workspace_id=ws.id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=person.id,
        event_type="member.added",
        payload={"after": {"handle": handle, "inbox_project_id": inbox.id}},
    )
    return member
