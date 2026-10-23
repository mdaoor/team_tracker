# ADR-0008: 404 for invisible objects, 403 for forbidden ones
Date: 2026-10-23   Status: accepted

## Context
Deep links (UX §4) must not reveal that an object exists to someone who may not see it. At the same time a person who
*can* see an object and tries something they may not do deserves an explanation, not a pretend-missing page.

## Decision
Every object lookup goes through `visible()` (one SQL predicate per entity, `acl/visibility.py`). An object that is not
visible is `404 not_found`; the response is byte-identical to a genuinely missing key. A visible object whose action needs a
permission the actor lacks is `403 forbidden` with `details.permission`. Non-members of a workspace get `404` for the whole
workspace. Token-scope failures are `403 insufficient_scope` because the token's owner may well see the object.

Inbox rule: items and the Inbox project itself are visible only to their owner and the owner's `line|senior` managers --
not to workspace admins and not through team, product or workspace views.

## Consequences
Routers never filter by hand and never check existence before visibility. Lists simply omit what the actor cannot see; counts
and `next_cursor` are computed after the predicate so they leak nothing. Admins debugging "why can't X see Y" use the
`/events` feed and `/me/scopes` rather than a bypass.
