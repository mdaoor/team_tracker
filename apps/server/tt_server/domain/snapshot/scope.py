"""Snapshot scoping helpers: parsing the scope string, the scope's item filter, the date-range filter and chunking."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from tt_schema.enums import (
    TargetType,
    ViolationStatus,
)

from tt_server.db import models as m
from tt_server.domain.errors import Invalid, NotFound
from tt_server.query.compile import QueryCtx

CHUNK = 5000
OPEN_VIOLATION = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


def parse_scope(scope: str) -> tuple[str, str]:
    """Split a 'kind:ref' scope string, rejecting unknown kinds."""
    kind, _, ref = scope.partition(":")
    if kind not in {"workspace", "product", "project", "team", "person"}:
        raise Invalid("invalid_scope", f"unknown scope {scope!r}", pointer="/scope")
    return kind, ref


def _chunks(xs: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(xs), CHUNK):
        yield xs[i : i + CHUNK]


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    return d.date().isoformat() if isinstance(d, datetime) else d.isoformat()


async def _scope_filter(qc: QueryCtx, kind: str, ref: str) -> tuple[dict[str, Any], str]:
    s = qc.session
    if kind == "workspace":
        return {}, f"Workspace: {ref or 'all'}"
    if kind == "project":
        name = await s.scalar(
            select(m.Project.name).where(m.Project.workspace_id == qc.ws_id, m.Project.key == ref.upper())
        )
        if name is None:
            raise NotFound("not_found", f"unknown project {ref!r}", pointer="/scope")
        return {"project": {"in": [ref.upper()]}}, f"Project: {name}"
    if kind == "product":
        prod = await s.scalar(
            select(m.Product).where(m.Product.workspace_id == qc.ws_id, m.Product.key == ref.upper())
        )
        if prod is None:
            raise NotFound("not_found", f"unknown product {ref!r}", pointer="/scope")
        keys = (
            await s.scalars(
                select(m.Project.key)
                .join(m.ProductLink, m.ProductLink.target_id == m.Project.id)
                .where(
                    m.ProductLink.product_id == prod.id,
                    m.ProductLink.target_type == TargetType.PROJECT,
                    m.ProductLink.deleted_at.is_(None),
                )
            )
        ).all()
        return {"project": {"in": sorted(keys) or ["-"]}}, f"Product: {prod.name}"
    if kind == "team":
        return {"people": {"assignee_in_team": ref}}, f"Team: {ref}"
    handle = ref if ref.startswith("@") else "@" + ref
    return {"people": {"assignee": handle}}, f"Person: {handle}"


def _range_filter(frm: date, to: date) -> dict[str, Any]:
    a, b = frm.isoformat(), to.isoformat()
    return {
        "or": [
            {"state": {"category": ["open", "active"]}},
            {"actual_end": {"between": [a, b]}},
            {"planned_end": {"between": [a, b]}},
            {"planned_start": {"between": [a, b]}},
            {"touched": {"since": a}},
        ]
    }
