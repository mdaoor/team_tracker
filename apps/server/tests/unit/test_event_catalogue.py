from __future__ import annotations

import pytest
from tt_schema.capture import Event
from tt_schema.enums import TargetType
from tt_schema.testing.factories import build
from tt_server.events.catalogue import EVENT_TYPES, UnknownEventTypeError, is_known
from tt_server.events.writer import EventWriter


def test_known_and_wildcard_types() -> None:
    assert is_known("item.created") and is_known("dimension.created") and is_known("context_note.updated")
    assert not is_known("item.exploded") and not is_known("dimension.") and not is_known("bogus.created")


def test_every_appendix_a_family_present() -> None:
    for t in ("workspace.created", "stage.moved", "violation.opened", "jira.pushed", "insight.generated"):
        assert is_known(t)
    assert len(EVENT_TYPES) >= 100


def test_writer_rejects_unknown_type() -> None:
    writer = EventWriter()
    with pytest.raises(UnknownEventTypeError):
        writer.append(build(Event, event_type="item.exploded", entity_type=TargetType.WORK_ITEM))
    writer.append(build(Event, event_type="item.created", entity_type=TargetType.WORK_ITEM))
    assert len(writer) == 1
