"""Server-owned tables of Phase 7: presentations (ADR-0021) and the snapshot cache."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tt_server.db.base import RowBase, WorkspaceScopedMixin


class Presentation(WorkspaceScopedMixin, RowBase):
    """A deck: scope + range + weight scheme + an ordered list of slides (embedded JSON, validated by the API)."""

    __tablename__ = "presentations"
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    scope: Mapped[str] = mapped_column(
        sa.Text(), nullable=False, default="workspace"
    )  # kind:ref, `workspace`
    range_days: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=14)
    weight_scheme_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("weight_schemes.id"), nullable=True
    )
    theme: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="auto")
    slides: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    is_shared: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    is_template: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    share_token: Mapped[str | None] = mapped_column(sa.Text(), nullable=True, unique=True)
    share_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class SnapshotCache(WorkspaceScopedMixin, RowBase):
    """Assembled snapshot for (scope, range, scheme, viewer visibility key); refreshed by a job, bypassed by `fresh=1`."""

    __tablename__ = "snapshot_cache"
    cache_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    scope: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    days: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    scheme_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    body: Mapped[Any] = mapped_column(JSONB(), nullable=False)
    __table_args__ = (sa.UniqueConstraint("workspace_id", "cache_key"),)
