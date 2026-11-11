# ADR-0024: The Inbox is invisible to admins
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
Items in a person's Inbox project are visible only to that person (and, through the review queue, their line manager). Workspace admins do not see them in lists, snapshots or search.

## Why
Unfiled capture contains half-formed and personal notes; the plan treats it as the reporter's scratchpad until filed.

## Consequences
An admin who needs them uses the audit feed (`/events`) which redacts `raw_input`, or the review queue owner path. Whether admins should have an explicit audited override is an open product question.
