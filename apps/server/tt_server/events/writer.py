"""EventWriter: buffers `Event`s during a unit of work and is the only code that INSERTs into `events`."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.capture import Event

from tt_server.db.models.capture import Event as EventRow
from tt_server.events.catalogue import require_known


class EventWriter:
    def __init__(self) -> None:
        self._buffer: list[Event] = []

    def append(self, event: Event) -> None:
        require_known(event.event_type)
        self._buffer.append(event)

    def __len__(self) -> int:
        return len(self._buffer)

    @property
    def pending(self) -> tuple[Event, ...]:
        return tuple(self._buffer)

    def flush(self, session: AsyncSession) -> list[Event]:
        """Add buffered events to the session (same transaction as the projection rows)."""
        flushed = list(self._buffer)
        for event in flushed:
            session.add(EventRow(**event.model_dump()))
        self._buffer.clear()
        return flushed
