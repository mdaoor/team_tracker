from __future__ import annotations

import pytest
from sqlalchemy import func, select
from tt_schema.capture import Event
from tt_schema.enums import TargetType
from tt_schema.tenancy import Workspace
from tt_schema.testing.factories import build
from tt_server.db import models
from tt_server.db.uow import MissingEventError, UnitOfWork


def _workspace_row(slug: str) -> models.Workspace:
    ws = build(Workspace, name=slug, slug=slug)
    return models.Workspace(**ws.model_dump())


async def test_projection_change_without_event_is_rejected(uow: UnitOfWork) -> None:
    async with uow:
        uow.session.add(_workspace_row("uow-b"))
        with pytest.raises(MissingEventError):
            await uow.commit()


async def test_allow_unlogged_is_the_seed_escape_hatch(uow: UnitOfWork) -> None:
    async with uow:
        uow.session.add(_workspace_row("uow-c"))
        await uow.commit(allow_unlogged=True)
        assert (
            await uow.session.scalar(
                select(func.count()).select_from(models.Workspace).where(models.Workspace.slug == "uow-c")
            )
            == 1
        )


async def test_event_and_projection_commit_atomically(uow: UnitOfWork) -> None:
    from tt_schema.tenancy import Person

    async with uow:
        ws = _workspace_row("uow-d")
        person = models.Person(**build(Person).model_dump())
        uow.session.add_all([ws, person])
        await uow.session.flush()
        uow.events.append(
            build(
                Event,
                workspace_id=ws.id,
                actor_id=person.id,
                entity_type=TargetType.PROJECT,
                entity_id=ws.id,
                event_type="workspace.created",
                payload={"after": {"slug": "uow-d"}},
            )
        )
        await uow.commit()
        count = await uow.session.scalar(
            select(func.count()).select_from(models.Event).where(models.Event.workspace_id == ws.id)
        )
        assert count == 1
        assert not uow.events.pending


async def test_idempotent_client_id(uow: UnitOfWork) -> None:
    from sqlalchemy.exc import IntegrityError
    from tt_schema.tenancy import Person

    async with uow:
        ws = _workspace_row("uow-e")
        person = models.Person(**build(Person).model_dump())
        uow.session.add_all([ws, person])
        await uow.session.flush()
        for _ in range(2):
            uow.events.append(
                build(
                    Event,
                    workspace_id=ws.id,
                    actor_id=person.id,
                    entity_type=TargetType.PROJECT,
                    entity_id=ws.id,
                    event_type="workspace.created",
                    client_id="dup-1",
                )
            )
        with pytest.raises(IntegrityError):
            await uow.commit()
