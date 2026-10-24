from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select, text
from tt_schema.capture import Event
from tt_schema.enums import TargetType
from tt_schema.tenancy import Person, Workspace
from tt_schema.testing.factories import build
from tt_server.db import models
from tt_server.db.uow import UnitOfWork
from tt_server.jobs import registry


@pytest.fixture
def routed() -> Iterator[None]:
    registry.register("workspace.", "tt_server.jobs.test_task")
    yield
    registry.JOBS.clear()


def test_registry_routes_by_prefix(routed: None) -> None:
    assert registry.tasks_for("workspace.created") == ["tt_server.jobs.test_task"]
    assert registry.tasks_for("item.created") == []
    registry.register("workspace.", "tt_server.jobs.test_task")  # idempotent
    assert len(registry.JOBS["workspace."]) == 1


async def test_jobs_are_deferred_in_the_same_transaction(uow: UnitOfWork, routed: None) -> None:
    async with uow:
        ws = models.Workspace(**build(Workspace, slug="outbox").model_dump())
        person = models.Person(**build(Person).model_dump())
        uow.session.add_all([ws, person])
        await uow.session.flush()
        event = build(
            Event,
            workspace_id=ws.id,
            actor_id=person.id,
            entity_type=TargetType.PROJECT,
            entity_id=ws.id,
            event_type="workspace.created",
        )
        uow.events.append(event)
        await uow.commit()
        row = (
            await uow.session.execute(
                text(
                    "SELECT task_name, args FROM procrastinate_jobs WHERE task_name = 'tt_server.jobs.test_task'"  # noqa: E501
                )
            )
        ).one()
        assert row.args == {"event_id": event.id}
        assert (
            await uow.session.scalar(
                select(func.count()).select_from(models.Event).where(models.Event.workspace_id == ws.id)
            )
            == 1
        )
