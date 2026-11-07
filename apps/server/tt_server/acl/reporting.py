"""Reporting-line resolver (object model §3): who manages whom, honouring validity windows and scopes."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.enums import ReportingKind, ScopeType

from tt_server.clock import utc_today
from tt_server.db import models as m

LINE_KINDS = (ReportingKind.LINE, ReportingKind.SENIOR)


def _valid(at: date) -> list[object]:
    return [
        m.ReportingLine.deleted_at.is_(None),
        or_(m.ReportingLine.valid_from.is_(None), m.ReportingLine.valid_from <= at),
        or_(m.ReportingLine.valid_to.is_(None), m.ReportingLine.valid_to >= at),
    ]


async def managers_of(
    session: AsyncSession,
    ws_id: str,
    person_id: str,
    kinds: Iterable[ReportingKind] | None = None,
    scope: tuple[ScopeType, str] | None = None,
    at: date | None = None,
) -> list[str]:
    """Managers of `person_id` of the given kinds (default: all); with `scope`, unscoped lines also count."""
    stmt = select(m.ReportingLine.manager_id).where(
        m.ReportingLine.workspace_id == ws_id,
        m.ReportingLine.person_id == person_id,
        *_valid(at or utc_today()),  # type: ignore[arg-type]
    )
    if kinds is not None:
        stmt = stmt.where(m.ReportingLine.kind.in_(list(kinds)))
    if scope is not None:
        stmt = stmt.where(
            or_(
                m.ReportingLine.scope_type == ScopeType.NONE,
                (m.ReportingLine.scope_type == scope[0]) & (m.ReportingLine.scope_id == scope[1]),
            )
        )
    return list(dict.fromkeys((await session.scalars(stmt)).all()))


async def reports_of(
    session: AsyncSession,
    ws_id: str,
    manager_id: str,
    kinds: Iterable[ReportingKind] | None = None,
    scope: tuple[ScopeType, str] | None = None,
    at: date | None = None,
) -> list[str]:
    stmt = select(m.ReportingLine.person_id).where(
        m.ReportingLine.workspace_id == ws_id,
        m.ReportingLine.manager_id == manager_id,
        *_valid(at or utc_today()),  # type: ignore[arg-type]
    )
    if kinds is not None:
        stmt = stmt.where(m.ReportingLine.kind.in_(list(kinds)))
    if scope is not None:
        stmt = stmt.where(
            or_(
                m.ReportingLine.scope_type == ScopeType.NONE,
                (m.ReportingLine.scope_type == scope[0]) & (m.ReportingLine.scope_id == scope[1]),
            )
        )
    return list(dict.fromkeys((await session.scalars(stmt)).all()))
