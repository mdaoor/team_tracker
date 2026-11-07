"""Error envelope per appendix A: {"error": {"code", "message", "pointer", "details"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tt_server.domain.errors import DomainError

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(DomainError):
    """An error raised by the HTTP layer with an explicit status. Domain code raises the typed errors of `domain.errors`."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        pointer: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, pointer=pointer, details=details)
        self.status = status


def error_body(
    code: str, message: str, pointer: str | None = None, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "pointer": pointer, "details": details or {}}}


def _pointer(loc: tuple[Any, ...]) -> str:
    parts = loc[1:] if loc and loc[0] in {"body", "query", "path", "header", "cookie"} else loc
    return "/" + "/".join(str(p) for p in parts)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.pointer, exc.details), exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODES.get(exc.status_code, "http_error")
        return JSONResponse(error_body(code, str(exc.detail)), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        pointer = _pointer(tuple(first.get("loc", ()))) if first else None
        details = {"errors": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in errors]}
        return JSONResponse(
            error_body("validation_error", first.get("msg", "invalid request"), pointer, details), 400
        )

    from sqlalchemy.exc import DBAPIError, StatementError

    @app.exception_handler(StatementError)
    async def _statement_error(_: Request, exc: StatementError) -> JSONResponse:
        """A value the database type system refuses (a string that is not an enum member, a malformed id, a NUL byte) is the caller's
        input, not a server fault. Anything else that reaches here is a genuine bug and stays a 500."""
        from psycopg import errors as pg

        orig = getattr(exc, "orig", None)
        user_input = isinstance(orig, (LookupError, ValueError)) or isinstance(
            orig, (pg.DataError, pg.InvalidTextRepresentation, pg.NumericValueOutOfRange)
        )
        if not user_input:
            raise exc
        return JSONResponse(
            error_body("invalid_parameter", "a parameter has a value this field cannot hold", None, {}), 400
        )

    app.add_exception_handler(DBAPIError, _statement_error)  # type: ignore[arg-type]

    from sqlalchemy.exc import IntegrityError

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        """Constraint violations are about the request's content: a reference to something that does not exist, a duplicate key, a value
        the schema forbids. The domain layer checks the common cases for better messages; this is the safety net for the rest."""
        from psycopg import errors as pg

        orig = getattr(exc, "orig", None)
        if isinstance(orig, pg.UniqueViolation):
            return JSONResponse(error_body("conflict", "that value already exists", None, {}), 409)
        if isinstance(orig, pg.ForeignKeyViolation):
            return JSONResponse(
                error_body("invalid_reference", "a referenced record does not exist", None, {}), 400
            )
        if isinstance(orig, (pg.NotNullViolation, pg.CheckViolation)):
            return JSONResponse(
                error_body("invalid_parameter", "a required value is missing or invalid", None, {}), 400
            )
        raise exc
