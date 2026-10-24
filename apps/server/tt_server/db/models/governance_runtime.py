"""Server-owned tables of Phase 8: the grace-period tracker and in-app notifications."""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from tt_server.db.base import RowBase, WorkspaceScopedMixin


class PolicyMatchState(RowBase):
    """When a (policy, target) pair first matched. A violation is only opened once the match has held for the policy's grace period."""

    __tablename__ = "policy_match_state"
    policy_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("policies.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    target_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    first_matched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    __table_args__ = (sa.UniqueConstraint("policy_id", "target_type", "target_id"),)


class Notification(WorkspaceScopedMixin, RowBase):
    """In-app notification (Slack and e-mail arrive with the chat clients). One per (violation, recipient, day)."""

    __tablename__ = "notifications"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    violation_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("violations.id"), nullable=True)
    kind: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="violation")
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="")
    channel: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="in_app")
    day: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    __table_args__ = (
        sa.UniqueConstraint("violation_id", "person_id", "day", "channel", name="uq_notifications_dedupe"),
    )
