"""Checklists on an item: creating one with its entries and appending an entry."""

from __future__ import annotations

from sqlalchemy import select
from tt_schema.base import new_id

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork


async def create_checklist(uow: UnitOfWork, item: m.WorkItem, title: str, entries: list[str]) -> m.Checklist:
    """Add a checklist (placed after the existing ones) with its entries in order."""
    n = len(
        (await uow.session.scalars(select(m.Checklist.id).where(m.Checklist.work_item_id == item.id))).all()
    )
    cl = m.Checklist(id=new_id(), work_item_id=item.id, title=title, position=n)
    uow.session.add(cl)
    await uow.session.flush()
    for pos, text in enumerate(entries):
        uow.session.add(m.ChecklistItem(id=new_id(), checklist_id=cl.id, text=text, position=pos))
    await uow.session.flush()
    return cl


async def append_entry(uow: UnitOfWork, cl: m.Checklist, text: str) -> m.ChecklistItem:
    """Append an entry at the end of the checklist."""
    n = len(
        (
            await uow.session.scalars(select(m.ChecklistItem.id).where(m.ChecklistItem.checklist_id == cl.id))
        ).all()
    )
    ci = m.ChecklistItem(id=new_id(), checklist_id=cl.id, text=text, position=n)
    uow.session.add(ci)
    await uow.session.flush()
    return ci
