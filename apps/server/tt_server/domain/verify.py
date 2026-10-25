"""Projection verification (phase 12 §4, ADR-0040): do the projection tables still agree with the event log?

Replaying events into fresh tables (the plan's `rebuild-projection`) is not possible here: event payloads record the *change* that a
mutation made, not the full row, so the log alone cannot reconstruct a row. What the log *can* prove is that every projected fact has
an event that explains it. This module checks exactly that and reports drift; recovery from drift is a point-in-time restore."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHECKS: dict[str, str] = {
    "items_without_created_event": """
        SELECT i.key FROM work_items i JOIN projects p ON p.id = i.project_id
        WHERE p.workspace_id = :ws AND i.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM events e WHERE e.workspace_id = :ws AND e.entity_id = i.id AND e.event_type = 'item.created')
        ORDER BY i.key LIMIT 200""",
    "time_entries_without_event": """
        SELECT t.id FROM time_entries t JOIN work_items i ON i.id = t.work_item_id JOIN projects p ON p.id = i.project_id
        WHERE p.workspace_id = :ws AND t.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM events e WHERE e.workspace_id = :ws AND e.event_type = 'time.logged'
                          AND e.payload #>> '{after,time_entry_id}' = t.id)
        ORDER BY t.id LIMIT 200""",
    "time_entry_minutes_differ_from_event": """
        SELECT t.id FROM time_entries t JOIN work_items i ON i.id = t.work_item_id JOIN projects p ON p.id = i.project_id
        JOIN events e ON e.workspace_id = :ws AND e.event_type = 'time.logged' AND e.payload #>> '{after,time_entry_id}' = t.id
        WHERE p.workspace_id = :ws AND t.deleted_at IS NULL AND (e.payload #>> '{after,minutes}')::int <> t.minutes
        ORDER BY t.id LIMIT 200""",
    "events_pointing_at_missing_actor": """
        SELECT e.id FROM events e LEFT JOIN people p ON p.id = e.actor_id
        WHERE e.workspace_id = :ws AND p.id IS NULL ORDER BY e.id LIMIT 200""",
}


@dataclass
class Report:
    """Findings of the integrity checks, grouped by check name."""

    findings: dict[str, list[str]] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {"clean": self.clean, "findings": self.findings}


async def verify(session: AsyncSession, workspace_id: str) -> Report:
    """Run every integrity check against a workspace and collect offending row ids."""
    rep = Report()
    for name, sql in CHECKS.items():
        rows = [str(r[0]) for r in (await session.execute(text(sql), {"ws": workspace_id})).all()]
        if rows:
            rep.findings[name] = rows
    return rep
