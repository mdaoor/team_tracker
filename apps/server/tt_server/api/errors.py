"""Error envelope per appendix A: {"error": {"code", "message", "pointer", "details"}}."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        pointer: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.pointer, self.details = pointer, details or {}


def error_body(
    code: str, message: str, pointer: str | None = None, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "pointer": pointer, "details": details or {}}}


def _pointer(loc: tuple[Any, ...]) -> str:
    parts = loc[1:] if loc and loc[0] in {"body", "query", "path", "header", "cookie"} else loc
    return "/" + "/".join(str(p) for p in parts)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
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
