"""Translator configuration served to reporter clients: glossary, rules and correction feedback."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit, row_dict
from tt_server.domain.errors import Conflict, Forbidden, Invalid, NotFound

MAX_FEEDBACK = 500


def _announce(uow: UnitOfWork, ws_id: str, actor_id: str, event_type: str, payload: dict[str, Any]) -> None:
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=actor_id,
        entity_type=TargetType.PERSON,
        entity_id=actor_id,
        event_type=event_type,
        payload=payload,
    )


async def pack_member(
    uow: UnitOfWork, ws: m.Workspace, member: m.WorkspaceMember, is_admin: bool, for_handle: str | None
) -> m.WorkspaceMember:
    """The member whose context pack is built: the caller, or (admin only, air-gapped export) another member."""
    if not for_handle or for_handle == member.handle:
        return member
    if not is_admin:
        raise Forbidden("forbidden", "only a workspace admin may export another person's pack")
    other = await uow.session.scalar(
        select(m.WorkspaceMember).where(
            m.WorkspaceMember.workspace_id == ws.id,
            m.WorkspaceMember.handle == (for_handle if for_handle.startswith("@") else "@" + for_handle),
        )
    )
    if other is None:
        raise NotFound("not_found", f"unknown member {for_handle}")
    return other


# ------------------------------------------------------------------ glossary
async def _entry(uow: UnitOfWork, ws_id: str, entry_id: str) -> m.GlossaryEntry:
    e = await uow.session.scalar(
        select(m.GlossaryEntry).where(
            m.GlossaryEntry.id == entry_id,
            m.GlossaryEntry.workspace_id == ws_id,
            m.GlossaryEntry.deleted_at.is_(None),
        )
    )
    if e is None:
        raise NotFound("not_found", "glossary entry not found")
    return e


async def list_glossary(uow: UnitOfWork, ws_id: str) -> list[dict[str, Any]]:
    """Live glossary entries of the workspace, alphabetical."""
    rows = (
        await uow.session.scalars(
            select(m.GlossaryEntry)
            .where(m.GlossaryEntry.workspace_id == ws_id, m.GlossaryEntry.deleted_at.is_(None))
            .order_by(m.GlossaryEntry.phrase)
        )
    ).all()
    return [row_dict(r) for r in rows]


async def create_glossary(
    uow: UnitOfWork, ws_id: str, actor_id: str, fields: dict[str, Any]
) -> m.GlossaryEntry:
    """Add a phrase -> target entry; a phrase (case-insensitive) may be defined only once."""
    clash = await uow.session.scalar(
        select(m.GlossaryEntry.id).where(
            m.GlossaryEntry.workspace_id == ws_id,
            func.lower(m.GlossaryEntry.phrase) == fields["phrase"].lower(),
            m.GlossaryEntry.deleted_at.is_(None),
        )
    )
    if clash:
        raise Conflict("glossary_exists", "phrase already defined", pointer="/phrase")
    row = m.GlossaryEntry(id=new_id(), workspace_id=ws_id, **fields)
    uow.session.add(row)
    await uow.session.flush()
    _announce(
        uow,
        ws_id,
        actor_id,
        "glossary.learned",
        {
            "after": {
                "phrase": fields["phrase"],
                "to": f"{fields['resolves_to_type']}:{fields['resolves_to_id']}",
            }
        },
    )
    return row


async def patch_glossary(
    uow: UnitOfWork, ws_id: str, actor_id: str, entry_id: str, changes: dict[str, Any]
) -> m.GlossaryEntry:
    """Apply the given fields to a glossary entry and record which changed."""
    row = await _entry(uow, ws_id, entry_id)
    for k, v in changes.items():
        setattr(row, k, v)
    _announce(uow, ws_id, actor_id, "glossary.learned", {"fields_changed": list(changes)})
    return row


async def delete_glossary(uow: UnitOfWork, ws_id: str, actor_id: str, entry_id: str) -> None:
    """Soft-delete a glossary entry."""
    row = await _entry(uow, ws_id, entry_id)
    row.deleted_at = utcnow()
    _announce(uow, ws_id, actor_id, "glossary.learned", {"before": {"phrase": row.phrase}})


# ------------------------------------------------------------------ translator rules
async def _rule(uow: UnitOfWork, ws_id: str, rule_id: str) -> m.TranslatorRule:
    r = await uow.session.scalar(
        select(m.TranslatorRule).where(
            m.TranslatorRule.id == rule_id,
            m.TranslatorRule.workspace_id == ws_id,
            m.TranslatorRule.deleted_at.is_(None),
        )
    )
    if r is None:
        raise NotFound("not_found", "rule not found")
    return r


async def list_rules(uow: UnitOfWork, ws_id: str) -> list[dict[str, Any]]:
    """Live translator rules in their evaluation order."""
    rows = (
        await uow.session.scalars(
            select(m.TranslatorRule)
            .where(m.TranslatorRule.workspace_id == ws_id, m.TranslatorRule.deleted_at.is_(None))
            .order_by(m.TranslatorRule.position, m.TranslatorRule.created_at)
        )
    ).all()
    return [row_dict(r) for r in rows]


async def create_rule(uow: UnitOfWork, ws_id: str, actor_id: str, fields: dict[str, Any]) -> m.TranslatorRule:
    """Add a translator rule; a scope id is required exactly when the scope is not the workspace."""
    if (fields["scope_type"] == "workspace") != (fields["scope_id"] is None):
        raise Invalid(
            "invalid_scope",
            "scope_id is required exactly when scope_type is not 'workspace'",
            pointer="/scope_id",
        )
    row = m.TranslatorRule(id=new_id(), workspace_id=ws_id, **fields)
    uow.session.add(row)
    await uow.session.flush()
    _announce(
        uow, ws_id, actor_id, "translator_rule.created", {"after": {"rule_id": row.id, "text": row.text}}
    )
    return row


async def patch_rule(
    uow: UnitOfWork, ws_id: str, actor_id: str, rule_id: str, changes: dict[str, Any]
) -> m.TranslatorRule:
    """Apply the given fields to a translator rule and record which changed."""
    row = await _rule(uow, ws_id, rule_id)
    for k, v in changes.items():
        setattr(row, k, v)
    _announce(uow, ws_id, actor_id, "translator_rule.updated", {"fields_changed": list(changes)})
    return row


async def delete_rule(uow: UnitOfWork, ws_id: str, actor_id: str, rule_id: str) -> None:
    """Soft-delete a translator rule."""
    row = await _rule(uow, ws_id, rule_id)
    row.deleted_at = utcnow()
    _announce(uow, ws_id, actor_id, "translator_rule.deleted", {"before": {"rule_id": row.id}})


# ------------------------------------------------------------------ feedback
async def store_feedback(
    uow: UnitOfWork, ws_id: str, person_id: str, corrections: list[dict[str, Any]]
) -> int:
    """Store the user's corrections of the translator's output (at most MAX_FEEDBACK per call); returns the count."""
    if len(corrections) > MAX_FEEDBACK:
        raise Invalid("too_many", f"at most {MAX_FEEDBACK} corrections per request")
    for f in corrections:
        uow.session.add(m.TranslatorFeedback(id=new_id(), workspace_id=ws_id, person_id=person_id, **f))
    if corrections:
        _announce(uow, ws_id, person_id, "translator_rule.feedback", {"after": {"count": len(corrections)}})
    return len(corrections)
