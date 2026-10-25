"""Display names for ids, resolved server-side in one batch (Phase 13 R2.1).

A response that a person reads must never show a bare UUID: people become `@handle`, teams their name, items/projects
their key, policies their key. An id that no longer resolves becomes a short, honest placeholder, never the raw id.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tt_server.db import models as m

_TYPES = ("person", "team", "work_item", "project", "product", "policy", "rule")


class Names:
    def __init__(self) -> None:
        self._m: dict[tuple[str, str], str] = {}

    @staticmethod
    def _kind(t: Any) -> str:
        return str(getattr(t, "value", t))

    def person(self, pid: str | None) -> str | None:
        return None if pid is None else self._m.get(("person", pid), "(former member)")

    def label(self, entity_type: Any, eid: str | None) -> str | None:
        if eid is None:
            return None
        kind = self._kind(entity_type)
        hit = self._m.get((kind, eid))
        if hit:
            return hit
        return f"({kind.replace('_', ' ')} removed)" if kind in _TYPES else f"({kind.replace('_', ' ')})"

    @classmethod
    async def load(
        cls,
        s: AsyncSession,
        ws_id: str,
        refs: Iterable[tuple[Any, str | None]],
    ) -> Names:
        """`refs` are `(entity_type, id)` pairs; unknown types are skipped and later labelled by type."""
        by: dict[str, set[str]] = {}
        for t, i in refs:
            if i:
                by.setdefault(cls._kind(t), set()).add(i)
        n = cls()
        if ids := by.get("person"):
            for pid, handle in (
                await s.execute(
                    select(m.WorkspaceMember.person_id, m.WorkspaceMember.handle).where(
                        m.WorkspaceMember.workspace_id == ws_id, m.WorkspaceMember.person_id.in_(ids)
                    )
                )
            ).all():
                n._m[("person", pid)] = handle
            missing = ids - {i for (k, i) in n._m if k == "person"}
            if missing:  # the system/bot person is not a workspace member
                for pid, name in (
                    await s.execute(
                        select(m.Person.id, m.Person.display_name).where(m.Person.id.in_(missing))
                    )
                ).all():
                    n._m[("person", pid)] = name
        if ids := by.get("team"):
            for i, name in (
                await s.execute(
                    select(m.Team.id, m.Team.name).where(m.Team.workspace_id == ws_id, m.Team.id.in_(ids))
                )
            ).all():
                n._m[("team", i)] = name
        if ids := by.get("work_item"):
            for i, key in (
                await s.execute(
                    select(m.WorkItem.id, m.WorkItem.key)
                    .join(m.Project, m.Project.id == m.WorkItem.project_id)
                    .where(m.Project.workspace_id == ws_id, m.WorkItem.id.in_(ids))
                )
            ).all():
                n._m[("work_item", i)] = key
        if ids := by.get("project"):
            for i, key in (
                await s.execute(
                    select(m.Project.id, m.Project.key).where(
                        m.Project.workspace_id == ws_id, m.Project.id.in_(ids)
                    )
                )
            ).all():
                n._m[("project", i)] = key
        if ids := by.get("product"):
            for i, key in (
                await s.execute(
                    select(m.Product.id, m.Product.key).where(
                        m.Product.workspace_id == ws_id, m.Product.id.in_(ids)
                    )
                )
            ).all():
                n._m[("product", i)] = key
        if ids := by.get("policy"):
            for i, key in (
                await s.execute(
                    select(m.Policy.id, m.Policy.key).where(
                        m.Policy.workspace_id == ws_id, m.Policy.id.in_(ids)
                    )
                )
            ).all():
                n._m[("policy", i)] = key
        return n
