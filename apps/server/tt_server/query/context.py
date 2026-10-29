"""The query context (who asks, which workspace, weights, `now`) and the small expressions every clause shares."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_filter import And, Clause, FilterError, Node, resolve
from tt_schema.enums import (
    Priority,
    ViolationStatus,
)

from tt_server.acl.context import AclContext
from tt_server.db import models as m

PRIORITY_ORDER = [p.value for p in Priority]
SEVERITY_ORDER = ["info", "warning", "violation", "critical"]
OPEN_VIOLATION = (ViolationStatus.OPEN, ViolationStatus.ACKNOWLEDGED)


@dataclass
class QueryCtx:
    session: AsyncSession
    ws_id: str
    acl: AclContext
    person_id: str
    handle: str
    tz: str = "UTC"
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    scheme_id: str | None = None
    _dims: dict[str, m.Dimension] = field(default_factory=dict)
    _teams: list[m.Team] | None = None

    @property
    def today(self) -> date:
        try:
            return self.now.astimezone(ZoneInfo(self.tz)).date()
        except Exception:  # noqa: BLE001
            return self.now.date()

    def day(self, token: str) -> date:
        d: date = resolve(token, self.today)
        return d

    def start_of(self, d: date) -> datetime:
        try:
            return datetime.combine(d, time.min, tzinfo=ZoneInfo(self.tz))
        except Exception:  # noqa: BLE001
            return datetime.combine(d, time.min, tzinfo=UTC)

    async def dimension(self, key: str, ptr: str) -> m.Dimension:
        if key not in self._dims:
            d = await self.session.scalar(
                select(m.Dimension).where(
                    m.Dimension.workspace_id == self.ws_id,
                    func.lower(m.Dimension.key) == key.lower(),
                    m.Dimension.deleted_at.is_(None),
                )
            )
            if d is None:
                raise FilterError(ptr, f"unknown dimension {key!r}")
            self._dims[key] = d
        return self._dims[key]

    async def person_ids(self, handles: list[str], ptr: str) -> list[str]:
        out: list[str] = []
        for h in handles:
            if h == "me":
                out.append(self.person_id)
                continue
            handle = h if h.startswith("@") else "@" + h
            pid = await self.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == self.ws_id, m.WorkspaceMember.handle == handle
                )
            )
            if pid is None:
                raise FilterError(ptr, f"unknown person {h!r}")
            out.append(pid)
        return out

    async def team_ids(self, slug: str, ptr: str) -> set[str]:
        if self._teams is None:
            self._teams = list(
                (await self.session.scalars(select(m.Team).where(m.Team.workspace_id == self.ws_id))).all()
            )
        root = next((t for t in self._teams if slug in (t.slug, t.id)), None)
        if root is None:
            raise FilterError(ptr, f"unknown team {slug!r}")
        ids, frontier = {root.id}, {root.id}
        while frontier:
            kids = {t.id for t in self._teams if t.parent_team_id in frontier} - ids
            ids |= kids
            frontier = kids
        return ids

    async def scheme(self, ref: str | None, ptr: str) -> str | None:
        if ref is None:
            return self.scheme_id
        sid = await self.session.scalar(
            select(m.WeightScheme.id).where(
                m.WeightScheme.workspace_id == self.ws_id,
                m.WeightScheme.deleted_at.is_(None),
                (m.WeightScheme.id == ref) | (m.WeightScheme.name == ref),
            )
        )
        if sid is None:
            raise FilterError(ptr, f"unknown weight scheme {ref!r}")
        return str(sid)


def weight_expr(scheme_id: str | None) -> Any:
    """The item's weight under a scheme, without touching `WorkItem.weight` (§13.4)."""
    if scheme_id is None:
        return m.WorkItem.weight
    sub = (
        select(m.Weight.value)
        .where(m.Weight.work_item_id == m.WorkItem.id, m.Weight.scheme_id == scheme_id)
        .correlate(m.WorkItem)
        .scalar_subquery()
    )
    return func.coalesce(sub, 0)


def priority_rank() -> Any:
    return case(*[(m.WorkItem.priority == Priority(p), i) for i, p in enumerate(PRIORITY_ORDER)], else_=-1)


def wants_inbox(node: Node) -> bool:
    """`include_inbox: true` at the top level (ACL still decides whose Inbox items are visible)."""
    items = node.items if isinstance(node, And) else (node,)
    return any(isinstance(n, Clause) and n.kind == "include_inbox" and n.args["value"] for n in items)
