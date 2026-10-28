"""Derived plan statuses: pure functions of dates, state categories and counts (never stored)."""

from __future__ import annotations

from datetime import date

AT_RISK_DAYS, DUE_SOON_DAYS, AT_RISK_PROGRESS = 7, 14, 50


# ---------------------------------------------------------------- statuses (pure, tested directly)
def deliverable_status(
    *,
    done: bool,
    planned_end: date | None,
    progress: int | None,
    has_work: bool,
    predecessor_overdue: bool,
    today: date,
) -> str:
    """Derive a deliverable's status (done, overdue, at risk, on track) from its dates and progress."""
    if done:
        return "done"
    if planned_end is not None and planned_end < today:
        return "overdue"
    soon = planned_end is not None and (planned_end - today).days <= AT_RISK_DAYS
    if (soon and (progress or 0) < AT_RISK_PROGRESS) or predecessor_overdue:
        return "at_risk"
    return "in_progress" if has_work else "not_started"


def milestone_status(*, completed: bool, due: date, today: date) -> str:
    """Derive a milestone's status: completed, overdue, due_soon or planned."""
    if completed:
        return "completed"
    if due < today:
        return "overdue"
    return "due_soon" if (due - today).days <= DUE_SOON_DAYS else "planned"


def project_health(milestones: list[str], deliverables: list[str]) -> str:
    """Roll milestone and deliverable statuses up into green, amber, red or unknown."""
    if not milestones and not deliverables:
        return "unknown"
    if "overdue" in milestones:
        return "red"
    if "due_soon" in milestones or "at_risk" in deliverables or "overdue" in deliverables:
        return "amber"
    return "green"
