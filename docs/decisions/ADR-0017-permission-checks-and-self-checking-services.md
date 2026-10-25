# ADR-0017: Permission checks are router dependencies; services reachable from several routes check themselves
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
Permissions are enforced by route dependencies (`needs()`, `check_project`, `check_item`) with `visible()` applied to every read statement. New rule: **any service entry point reachable from more than one route (or from a job/bulk path) performs its own authorisation** and does not rely on the router.

## Why
Review finding 3 (review-queue resolve/bulk mutating any item) was the consequence of a service that trusted its one caller. `review.resolve` now calls `authorise()` using `uow.context['acl']`; `bulk` and `confirm_all_suggested` go through `resolve`.

## Consequences
Services that read `uow.context['acl']` are only correct inside a request; jobs run without an ACL and are therefore treated as system actors. Every new multi-route service entry needs a negative test (see `tests/api/test_review_a.py`).
