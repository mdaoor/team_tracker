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
    scopes: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=lambda: ["capture", "read"])


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


class GlossaryEntry(WorkspaceScopedMixin, RowBase):
    """Phrase -> project/item/person mapping published in the ContextPack (replaces settings.glossary)."""

    __tablename__ = "glossary_entries"
    project_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=True)
    phrase: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    resolves_to_type: Mapped[str] = mapped_column(sa.Text(), nullable=False)  # project | item | person
    resolves_to_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    source: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="manual")  # manual | learned
    hits: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    __table_args__ = (sa.Index("uq_glossary_phrase", "workspace_id", sa.text("lower(phrase)"), unique=True),)


class TranslatorRule(WorkspaceScopedMixin, RowBase):
    """Plain-English rule for the translator, flows into ContextPack.manager_rules."""

    __tablename__ = "translator_rules"
    scope_type: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="workspace")
    scope_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    position: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)


class TranslatorFeedback(WorkspaceScopedMixin, RowBase):
    """A reporter's correction of a parsed field (opt-in), kept for evaluation / fine-tuning."""

    __tablename__ = "translator_feedback"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    client_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    field: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    raw: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    previous: Mapped[Any] = mapped_column(JSONB(), nullable=True)
    corrected: Mapped[Any] = mapped_column(JSONB(), nullable=True)


class IdpGroupMapping(WorkspaceScopedMixin, RowBase):
    """Maps an identity-provider group (AD DN / OIDC group id) to a role and/or a team (Phase 4 §4.6)."""

    __tablename__ = "idp_group_mappings"
    provider: Mapped[str] = mapped_column(sa.Text(), nullable=False)  # ldap | oidc
    group: Mapped[str] = mapped_column(sa.Text(), nullable=False)  # DN or id, compared case-insensitively
    role_key: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    scope_type: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="workspace")
    scope_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    team_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("teams.id"), nullable=True)
    __table_args__ = (sa.Index("ix_idp_group_mappings_group", "workspace_id", sa.text('lower("group")')),)
