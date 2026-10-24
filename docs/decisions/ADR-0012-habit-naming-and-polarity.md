# ADR-0012: Habit naming and polarity
Date: 2026-10-24   Status: accepted

## Decision
A habit is a *positive* behaviour score per subject (person or team) over a window; a high score is good. Habit detectors that are the
mirror of a violation policy use the positive name: `evidence_consistency` is the positive form of the `evidence_gap` policy.

Violation-driven habits are matched to their policy by **template key**, not by policy key, so a workspace may rename or clone the
policy without breaking the habit. Habits are computed for `person` and `team` subjects only; there is no project subject.

## Wellbeing
Habits and violations about a person are visible to that person and their line managers only. An admin without a line cannot read them;
the snapshot and presentations mark such a slide `restricted`.

## Consequences
Dashboards never show a negative-polarity habit, so "higher is better" holds on every chart.
