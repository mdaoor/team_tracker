"""The derived plan statuses (plan P1 §1.2) as pure functions."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from tt_server.domain.plan.service import deliverable_status, milestone_status, project_health

T = date(2026, 10, 24)


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        (
            dict(
                done=True,
                planned_end=T - timedelta(days=30),
                progress=0,
                has_work=False,
                predecessor_overdue=True,
            ),
            "done",
        ),
        (
            dict(
                done=False,
                planned_end=T - timedelta(days=1),
                progress=90,
                has_work=True,
                predecessor_overdue=False,
            ),
            "overdue",
        ),
        (
            dict(
                done=False,
                planned_end=T + timedelta(days=3),
                progress=20,
                has_work=True,
                predecessor_overdue=False,
            ),
            "at_risk",
        ),
        (
            dict(
                done=False,
                planned_end=T + timedelta(days=3),
                progress=None,
                has_work=False,
                predecessor_overdue=False,
            ),
            "at_risk",
        ),
        (
            dict(
                done=False,
                planned_end=T + timedelta(days=3),
                progress=80,
                has_work=True,
                predecessor_overdue=False,
            ),
            "in_progress",
        ),
        (
            dict(
                done=False,
                planned_end=T + timedelta(days=60),
                progress=0,
                has_work=False,
                predecessor_overdue=True,
            ),
            "at_risk",
        ),
        (
            dict(
                done=False,
                planned_end=T + timedelta(days=60),
                progress=0,
                has_work=True,
                predecessor_overdue=False,
            ),
            "in_progress",
        ),
        (
            dict(
                done=False,
                planned_end=T + timedelta(days=60),
                progress=None,
                has_work=False,
                predecessor_overdue=False,
            ),
            "not_started",
        ),
        (
            dict(done=False, planned_end=None, progress=None, has_work=False, predecessor_overdue=False),
            "not_started",
        ),
    ],
)
def test_deliverable_status(kw: dict[str, object], want: str) -> None:
    assert deliverable_status(today=T, **kw) == want  # type: ignore[arg-type]


def test_milestone_status_and_project_health() -> None:
    assert milestone_status(completed=True, due=T - timedelta(days=9), today=T) == "completed"
    assert milestone_status(completed=False, due=T - timedelta(days=1), today=T) == "overdue"
    assert milestone_status(completed=False, due=T + timedelta(days=14), today=T) == "due_soon"
    assert milestone_status(completed=False, due=T + timedelta(days=15), today=T) == "planned"
    assert project_health([], []) == "unknown"
    assert project_health(["planned"], ["not_started"]) == "green"
    assert project_health(["due_soon"], []) == "amber" and project_health([], ["at_risk"]) == "amber"
    assert project_health(["overdue", "planned"], ["done"]) == "red"
