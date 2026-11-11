# ADR-0023: A workspace `manager` sees `members`-tier items without being a member
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
`visible()` lets holders of the workspace-level manager role read items whose visibility is `members`, `team` or `project` even when they are not on the project or team (`acl/visibility.py`).

## Why
A manager's job is cross-team oversight (review queue, violations, digests); requiring membership of every project would make them request access to what they are accountable for.

## Consequences
`private` and `assignees` remain closed to managers, wellbeing data keeps its own rule (subject and line managers only), and the Inbox stays private (ADR-0024). Revisit if tenants ask for manager scoping by team; `scope_members` is the hook.
