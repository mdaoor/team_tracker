"""Ingestion context: the per-message `Ctx` and the confidence / matching thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.client import ClientMessage
from tt_schema.enums import (
    EventSource,
    TargetType,
)

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit

log = structlog.get_logger()

CONFIDENCE_FLOOR = 0.7
MATCH_THRESHOLD = 0.6
MATCH_GAP = 0.1
RECENCY_BONUS = 0.1
RECENCY_DAYS = 7


@dataclass
class Ctx:
    """Per-message processing context: unit of work, workspace, message, actor and subject, with emit helpers."""

    uow: UnitOfWork
    ws: m.Workspace
    msg: ClientMessage
    client_version: str
    correlation_id: str
    actor: m.WorkspaceMember
    subject: m.WorkspaceMember  # whose work this is (differs from actor for on_behalf_of)
    actor_person: m.Person
    subject_person: m.Person
    at: datetime
    warnings: list[str] = field(default_factory=list)
    review_ids: list[str] = field(default_factory=list)
    resolution: dict[str, Any] = field(default_factory=dict)
    events: list[Any] = field(default_factory=list)

    @property
    def session(self) -> AsyncSession:
        return self.uow.session

    def emit(
        self, event_type: str, entity_type: TargetType, entity_id: str, payload: dict[str, Any] | None = None
    ) -> None:
        self.events.append(
            emit(
                self.uow,
                workspace_id=self.ws.id,
                actor_id=self.actor.person_id,
                entity_type=entity_type,
                entity_id=entity_id,
                event_type=event_type,
                payload=payload,
                source=EventSource.CLIENT,
                occurred_at=self.at,
                correlation_id=self.correlation_id,
                raw_input=self.msg.raw_input,
            )
        )

    def conf_ok(self, name: str) -> bool:
        c = (self.msg.llm_confidence or {}).get(name)
        return c is None or c >= CONFIDENCE_FLOOR
