"""Server-owned tables that have no counterpart in tt_schema (sequences, tokens, review queue)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tt_server.db.base import Base, RowBase, WorkspaceScopedMixin


class ProjectKeySequence(Base):
    """Per-project counter behind `WorkItem.key = f"{project.key}-{n}"` (updated under row lock)."""

    __tablename__ = "project_key_sequences"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), primary_key=True)
    next: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=1)


class ApiToken(WorkspaceScopedMixin, RowBase):
    """Personal API token. Only the SHA-256 of the secret is stored; the secret is shown once."""

    __tablename__ = "api_tokens"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    hash: Mapped[str] = mapped_column(sa.Text(), nullable=False, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class ReviewItem(WorkspaceScopedMixin, RowBase):
    """Backing store of the manager review queue (Phase 5): suggestions the capture path could not apply."""

    __tablename__ = "review_items"
    kind: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    target_type: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    target_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    suggestion: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 4), nullable=True)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="open")
    __table_args__ = (sa.Index("ix_review_items_workspace_status", "workspace_id", "status"),)
