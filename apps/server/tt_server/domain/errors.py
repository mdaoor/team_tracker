"""Failures the domain can report, free of HTTP (Phase 13 R4.1, review T7; ADR-0047).

Domain, ACL, governance, integration and export code raises these; `install_error_handlers` maps them to the response
envelope in one place. Each class carries the status it maps to so the mapping stays a table, not scattered `if`s.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base of all expected, client-visible failures; carries the HTTP status, code, message and JSON pointer."""

    status: int = 500
    default_code: str = "error"

    def __init__(
        self,
        code: str | None = None,
        message: str = "",
        *,
        pointer: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.message = message
        self.pointer, self.details = pointer, details or {}


class Invalid(DomainError):
    """The input is wrong (400)."""

    status, default_code = 400, "invalid"


class Unauthenticated(DomainError):
    """The caller has not proven who they are (401)."""

    status, default_code = 401, "unauthorized"


class Forbidden(DomainError):
    """The caller is known but lacks the permission for a visible resource (403)."""

    status, default_code = 403, "forbidden"


class NotFound(DomainError):
    """Also what a caller gets for something they may not see (ADR-0008)."""

    status, default_code = 404, "not_found"


class Conflict(DomainError):
    """The request clashes with current state, e.g. a duplicate key or lost update (409)."""

    status, default_code = 409, "conflict"


class PreconditionRequired(DomainError):
    """The request must carry an If-Match precondition and did not (428)."""

    status, default_code = 428, "precondition_required"


class Throttled(DomainError):
    """Too many requests in the window; the client should retry later (429)."""

    status, default_code = 429, "rate_limited"


class Unavailable(DomainError):
    """A dependency or build artefact the request needs is missing (503)."""

    status, default_code = 503, "unavailable"


class Fault(DomainError):
    """A server-side misconfiguration the caller cannot fix (500)."""

    status, default_code = 500, "internal_error"


BY_STATUS: dict[int, type[DomainError]] = {
    c.status: c for c in (Invalid, Unauthenticated, Forbidden, NotFound, Conflict, PreconditionRequired, Throttled, Unavailable, Fault)
}  # fmt: skip
