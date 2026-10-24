"""Server-owned tables of Phase 10: the Jira connection, issue links, pushed worklogs and the sync log (Jira is a projection)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tt_server.db.base import RowBase, WorkspaceScopedMixin


class JiraConnection(WorkspaceScopedMixin, RowBase):
    """One per workspace. The API token and the webhook secret are stored encrypted (Fernet, `integrations/jira/crypto.py`)."""

    __tablename__ = "jira_connections"
    base_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    email: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    token_enc: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    webhook_secret_enc: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    bot_account_id: Mapped[str | None] = mapped_column(
        sa.Text(), nullable=True
    )  # the integration user: its edits are not echoed back
    enabled: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    __table_args__ = (sa.UniqueConstraint("workspace_id"),)


class JiraLink(RowBase):
    """Item ↔ issue. `last_pushed_hash` covers the field payload; `last_state_key` the workflow state last reflected in Jira."""

    __tablename__ = "jira_links"
    item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    issue_key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    issue_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    last_pushed_hash: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_state_key: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    last_pushed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text(), nullable=False, default="synced"
    )  # synced | failed | pending
    attempts: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    __table_args__ = (sa.UniqueConstraint("item_id"), sa.UniqueConstraint("issue_key"))


class JiraWorklog(RowBase):
    """One Jira worklog per time entry; the unique key makes pushing idempotent under job retry."""

    __tablename__ = "jira_worklogs"
    time_entry_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("time_entries.id"), nullable=False)
    item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    worklog_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    __table_args__ = (sa.UniqueConstraint("time_entry_id"),)


class JiraSyncLog(WorkspaceScopedMixin, RowBase):
    __tablename__ = "jira_sync_log"
    item_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    issue_key: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    direction: Mapped[str] = mapped_column(sa.Text(), nullable=False)  # push | inbound
    action: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    ok: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    diff: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    __table_args__ = (sa.Index("ix_jira_sync_log_ws_created", "workspace_id", "created_at"),)
