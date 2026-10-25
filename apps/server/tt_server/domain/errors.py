"""Failures the domain can report, free of HTTP (Phase 13 R4.1, review T7; ADR-0047).

Domain, ACL, governance, integration and export code raises these; `install_error_handlers` maps them to the response
envelope in one place. Each class carries the status it maps to so the mapping stays a table, not scattered `if`s.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
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
    status, default_code = 401, "unauthorized"


class Forbidden(DomainError):
    status, default_code = 403, "forbidden"


class NotFound(DomainError):
    """Also what a caller gets for something they may not see (ADR-0008)."""

    status, default_code = 404, "not_found"


class Conflict(DomainError):
    status, default_code = 409, "conflict"


class PreconditionRequired(DomainError):
    status, default_code = 428, "precondition_required"


class Throttled(DomainError):
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
