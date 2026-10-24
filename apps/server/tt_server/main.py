"""FastAPI application factory."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tt_schema import client_message_json_schema

from tt_server.api.errors import install_error_handlers
from tt_server.api.routers import (
    access,
    auth,
    capture,
    client,
    console,
    core,
    identity,
    llm,
    overlay,
    query,
    webauth,
)
from tt_server.api.session import CSRF_COOKIE, set_session
from tt_server.db.session import make_engine, make_session_factory
from tt_server.integrations.ldap import ldap_provider_from_settings
from tt_server.integrations.llm import gateway_from_settings
from tt_server.integrations.oidc import oidc_provider_from_settings
from tt_server.logging import configure_logging
from tt_server.settings import Settings, get_settings
from tt_server.telemetry import configure_telemetry

log = structlog.get_logger()


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    configure_telemetry(otlp_endpoint=settings.otlp_endpoint)
    db_engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db_engine.dispose()

    app = FastAPI(title="Team Tracker", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = db_engine
    app.state.session_factory = session_factory or make_session_factory(db_engine)
    app.state.ldap = ldap_provider_from_settings(settings)
    app.state.oidc = oidc_provider_from_settings(settings)
    app.state.llm = gateway_from_settings(settings)
    install_error_handlers(app)
    for r in (
        auth.router,
        webauth.router,
        webauth.api_router,
        identity.router,
        console.router,
        query.router,
        llm.router,  # before core: `/items/search` must win over `/items/{id_or_key}`
        capture.router,
        core.router,
        client.router,
        overlay.router,
        access.router,
    ):
        app.include_router(r)

    @app.middleware("http")
    async def transport_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """HTTP -> HTTPS redirect (health probes excepted) and HSTS on every HTTPS response (§4.6)."""
        proto = request.url.scheme
        if settings.trust_forwarded_proto and (fwd := request.headers.get("x-forwarded-proto")):
            proto = fwd.split(",")[0].strip()
        if settings.force_https and proto != "https" and request.url.path not in ("/healthz", "/readyz"):
            target = request.url.replace(scheme="https")
            if settings.https_port:
                target = target.replace(netloc=f"{request.url.hostname}:{settings.https_port}")
            return RedirectResponse(str(target), status_code=308)
        response = await call_next(request)
        if proto == "https":
            response.headers["Strict-Transport-Security"] = (
                f"max-age={settings.hsts_max_age}; includeSubDomains"
            )
        return response

    @app.middleware("http")
    async def session_refresh(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Sliding 12 h session: re-issue the cookie when a request authenticated by it is getting old."""
        response = await call_next(request)
        pid = getattr(request.state, "refresh_session", None)
        if pid:
            csrf = request.cookies.get(CSRF_COOKIE)
            set_session(response, settings, pid, secure=request.url.scheme == "https", csrf=csrf)
        return response

    @app.middleware("http")
    async def request_id(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-Id"] = rid
        return response

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz(response: Response) -> dict[str, Any]:
        checks: dict[str, str] = {}
        try:
            async with db_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                checks["database"] = "ok"
                has_schema = await conn.scalar(text("SELECT to_regclass('procrastinate_jobs') IS NOT NULL"))
                checks["procrastinate"] = "ok" if has_schema else "schema_missing"
        except Exception as exc:
            log.warning("readyz_failed", error=str(exc))
            checks.setdefault("database", "unavailable")
            checks.setdefault("procrastinate", "unknown")
        ready = all(v == "ok" for v in checks.values())
        if not ready:
            response.status_code = 503
        return {"status": "ready" if ready else "not_ready", "checks": checks}

    @app.get("/api/v1/meta/schema/client-message", tags=["meta"])
    async def client_message_schema(batch: bool = False) -> dict[str, Any]:
        """The exact JSON Schema translators must emit (served so clients can fetch the matching version)."""
        return client_message_json_schema(batch=batch)

    return app
