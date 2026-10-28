# ADR-0042: Review rows go to the reporter's line manager first; every Inbox item gets a row; rows carry a default
Date: 2026-10-25   Status: accepted, amends ADR-0006 (Phase 13 R6.1; UX review 3, 8)

## Context
`review_owner()` preferred the project owner, so a reporter who owned a project reviewed her own captures, and an item
created in an Inbox with no recognisable phrase produced no row at all. Without an LLM no row had a one-click action.

## Decision
* Owner order: the reporter's primary line manager; else the project owner unless they are the reporter; else another
  project manager; Inbox with no line manager: the first workspace member. When the reporter is the only manager there is,
  they still get the row (nobody else could act on it).
* Every item created in an Inbox gets an `unclassified_project` row (phrase may be empty).
* `GET /review-queue` rows carry `reporter`, `created_at` and `suggested{choice,reason,confidence}` from a deterministic
  ladder (`domain/overlay/defaults.py`): parent's project → glossary phrase → reporter's most-used project in 30 days →
  the reporter's team project. For unknown people: nearest handle. The LLM may rank; it never replaces the ladder.
* Resolving is reversible: `POST /review-queue/undo` reopens the row and reverts the write for project moves,
  assignments, ignores and classification confirms (others answer `409 cannot_undo`); `POST /review-queue/unlearn` drops the
  glossary phrase a resolve created. `choice.learn=false` resolves without learning.

## Consequences
The demo seed makes `@lina`, not `@sara`, owner of WEB. Tests cover routing, the ladder order, undo and unlearn.
