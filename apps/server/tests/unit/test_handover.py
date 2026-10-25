"""tools/handover.py fills the appendix-D prompts from the real plans without leaving placeholders behind."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools import handover as h  # noqa: E402


def test_the_templates_are_found_in_the_appendix() -> None:
    assert set(h.templates()) >= {"D.1", "D.2", "D.3", "D.4"}  # D.5 (remediation and new features) joined in Phase 13


def test_phases_are_ordered_with_2b_after_2() -> None:
    labels = [p.label for p in h.phases()]
    assert labels[:5] == ["0", "1", "2", "2b", "3"] and labels[-4:] == ["12", "13", "14", "15"]
    assert h.find("phase-10").slug == "jira-sync"
    assert h.neighbour(h.find("3"), -1).label == "2b"  # type: ignore[union-attr]


def test_kickoff_names_the_branch_phase_file_and_neighbours() -> None:
    text = h.kickoff(h.find("10"), url="https://example.test/repo")
    assert (
        "phase/10-jira-sync" in text
        and "plans/11-phase-10-jira-sync.md" in text
        and "https://example.test/repo" in text
    )
    assert "Phase 11" in text and "10-phase-9-governance-ui-habits.md" in text
    assert "<N>" not in text and "<slug>" not in text and "<N+1>" not in text


def test_the_last_phase_has_no_next_phase() -> None:
    assert "none" in h.kickoff(h.find("15"), url="x")


def test_resume_review_and_spec_change() -> None:
    assert "phase/4-access-control" in h.resume(h.find("4"))
    r = h.review(h.find("8"), "https://github.com/o/r/pull/9")
    assert "pull/9" in r and "Phase 8" in r and "<link>" not in r
    s = h.spec_change("Habits get a project subject")
    assert "Habits get a project subject" in s and "<describe>" not in s


def test_testplan_lists_every_acceptance_criterion() -> None:
    p = h.find("10")
    plan = h.testplan(p)
    rows = [ln for ln in plan.splitlines() if re.match(r"\| \d+ \|", ln)]
    assert len(rows) == len(h.acceptance(p)) == 6
    assert "Jira outage" in plan


def test_unknown_phase_is_a_clear_error() -> None:
    with pytest.raises(SystemExit) as e:
        h.find("99")
    assert "unknown phase" in str(e.value)
