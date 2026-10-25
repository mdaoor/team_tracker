# ADR-0047: The domain raises typed errors; the HTTP layer maps them once
Date: 2026-10-25   Status: accepted (Phase 13 R4.1; review T7)

## Context
27 modules under `domain/`, `acl/`, `governance/`, `integrations/` and `exports/` imported `tt_server.api.errors.ApiError`, so the domain
depended on the HTTP layer and two import cycles formed through it.

## Decision
`tt_server/domain/errors.py` defines `DomainError` with `Invalid`(400), `Unauthenticated`(401), `Forbidden`(403), `NotFound`(404), `Conflict`(409),
`PreconditionRequired`(428), `Throttled`(429), `Fault`(500) and `Unavailable`(503). `ApiError` is now a `DomainError` subclass for HTTP-layer code
that needs an explicit status. One handler in `install_error_handlers` renders any `DomainError` into the unchanged envelope.
`tests/unit/test_architecture.py` fails if anything outside `api/` imports the HTTP layer, if the module-level import graph has a cycle, or if
function-level imports of our own modules exceed 30 (`admin.py` exempt: CLI start-up time).

## Consequences
Behaviour on the wire is identical. Domain tests assert `DomainError` subclasses.
