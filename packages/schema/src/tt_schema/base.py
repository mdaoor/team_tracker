"""Shared base model, id and clock helpers (see docs/spec/team_tracker_models.py `Base`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

try:  # UUID v7 when available (architecture §4.7), else the shim from the models file
    from uuid_utils import uuid7 as _uuid7
except ImportError:  # pragma: no cover
    _uuid7 = None  # type: ignore[assignment]


def new_id() -> str:
    """Time-sortable UUID v7 string."""
    if _uuid7 is not None:
        return str(_uuid7())
    import time

    ts = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 80) - 1)
    return str(uuid.UUID(int=(ts << 80) | rand))


def utcnow() -> datetime:
    return datetime.now(UTC)


class TTModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=False, extra="forbid")


class Base(TTModel):
    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    created_by: Optional[str] = None
    deleted_at: Optional[datetime] = None
