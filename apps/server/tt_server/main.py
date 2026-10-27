"""FastAPI application factory."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from tt_schema import client_message_json_schema

from tt_server.api.commands.router import router as commands_router
from tt_server.api.errors import error_body, install_error_handlers
from tt_server.api.routers import (
    access,
    auth,
    capture,
    client,
    console,
    core,
    core_projects,
    core_tenancy,
    governance,
    governance_habits,
    governance_ui,
    governance_violations,
    identity,
    item_attachments,
    item_extras,
    item_fields,
    jira,
    llm,
    overlay,
    plan,
    presentation_deck_io,
    presentation_insights,
    presentation_templates,
    presentations,
    privacy,
    query,
    query_layouts,
    recurrence,
    saved_views,
    webauth,
)
from tt_server.api.session import CSRF_COOKIE, set_session
from tt_server.bootstrap import seed_reference_data
from tt_server.csp import html_csp
from tt_server.db.session import make_engine, make_session_factory
from tt_server.integrations.ldap import ldap_provider_from_settings
from tt_server.integrations.llm import gateway_from_settings
from tt_server.integrations.oidc import oidc_provider_from_settings
from tt_server.logging import configure_logging
from tt_server.metrics import Metrics
from tt_server.ratelimit import SlidingWindow, client_ip, credential_key
from tt_server.settings import Settings, get_settings
from tt_server.telemetry import configure_telemetry, instrument

log = structlog.get_logger()

HTML_CSP = html_csp()  # fallback only: shells set their own nonce-bearing policy


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_runtime()
    configure_logging(settings.log_level, json=settings.log_json)
    provider = configure_telemetry(otlp_endpoint=settings.otlp_endpoint)
    db_engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        if settings.seed_reference_on_start:
            try:  # idempotent; also done by `tt-admin migrate`
                await seed_reference_data(app_.state.session_factory)
            except Exception:  # noqa: BLE001 - a database that is down must not stop the process; /readyz reports it
                log.warning("reference_data_not_seeded", exc_info=True)
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
        query_layouts.router,
        saved_views.router,
        governance.router,
        governance_violations.router,
        governance_habits.router,
        governance_ui.router,
        jira.router,
        plan.router,
        jira.hook,
        presentation_templates.router,
        presentations.router,
        presentation_deck_io.router,
        presentation_insights.router,
        presentations.public,
        presentations.shell,
        commands_router,  # `/items:merge` and `/commands/*` before core's `/items/{id_or_key}`
        llm.router,  # before core: `/items/search` must win over `/items/{id_or_key}`
        capture.router,
        privacy.router,
        item_extras.router,
        item_fields.router,
        item_attachments.router,
        recurrence.router,
        core_tenancy.router,
        core_projects.router,
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

    def request_ip(request: Request) -> str | None:
        return client_ip(
            settings, request.client.host if request.client else None, request.headers.get("x-forwarded-for")
        )

    capture_window = (
        SlidingWindow(settings.rate_capture_per_min) if settings.rate_capture_per_min > 0 else None
    )
    auth_window = SlidingWindow(settings.rate_auth_per_min) if settings.rate_auth_per_min > 0 else None
    command_window = (
        SlidingWindow(settings.rate_commands_per_min) if settings.rate_commands_per_min > 0 else None
    )

    def throttle_commands(request: Request, n: int) -> JSONResponse | None:
        """Count `n` management commands against the caller's credential bucket; a 429 response when over the limit.
        Called by the three entry points that run commands (their bodies are only known after parsing)."""
        if command_window is None or n <= 0:
            return None
        wait = command_window.hit(
            credential_key(
                request.headers.get("authorization"), request.cookies.get("tt_session"),
                request.headers.get("x-debug-actor"), request_ip(request),
            ),
            n,
        )  # fmt: skip
        if wait is None:
            return None
        retry = str(int(wait) + 1)
        return JSONResponse(
            error_body("rate_limited", "too many commands; slow down", None, {"retry_after": int(retry)}),
            429, headers={"Retry-After": retry},
        )  # fmt: skip

    app.state.throttle_commands = throttle_commands

    @app.middleware("http")
    async def rate_limit(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """429 + Retry-After for a runaway capture client or a burst against the sign-in endpoints."""
        path, wait = request.url.path, None
        if request.method == "POST" and capture_window and path.endswith("/capture/batches"):
            key = credential_key(
                request.headers.get("authorization"), request.cookies.get("tt_session"),
                request.headers.get("x-debug-actor"), request_ip(request),
            )  # fmt: skip
            wait = capture_window.hit(key)
        elif request.method == "POST" and auth_window and path.startswith("/auth/"):
            wait = auth_window.hit(credential_key(None, None, None, request_ip(request)))
        if wait is not None:
            retry = str(int(wait) + 1)
            return JSONResponse(
                error_body("rate_limited", "too many requests; slow down", None, {"retry_after": int(retry)}),
                429, headers={"Retry-After": retry},
            )  # fmt: skip
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """nosniff + no framing everywhere; a strict CSP on API responses; the HTML shells that carry the deck bundle get the narrowest
        policy that still lets an inline single-file bundle run (no remote origins, no frames, no forms)."""
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("X-Frame-Options", "DENY")
        if "content-security-policy" not in h:
            if h.get("content-type", "").startswith("text/html"):
                h["Content-Security-Policy"] = HTML_CSP
            elif not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
                h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    @app.middleware("http")
    async def reject_nul(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """NUL bytes (raw, percent-encoded, or JSON-escaped) cannot be stored by Postgres: say so with a 400 rather than a 500 later."""
        raw = request.url.query.lower()
        if "%00" in raw or "\x00" in raw:
            return JSONResponse(
                error_body("invalid_parameter", "NUL characters are not allowed", None, {}), 400
            )
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.body()
            if b"\\u0000" in body.lower() or b"\x00" in body:
                return JSONResponse(
                    error_body("invalid_parameter", "NUL characters are not allowed", None, {}), 400
                )
        return await call_next(request)

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

    metrics = Metrics()
    app.state.metrics = metrics
    app.middleware("http")(metrics.middleware)

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def scrape(request: Request) -> Response:
        """Prometheus scrape. Off (404) unless `TT_METRICS_TOKEN` is set or the app runs in dev; with a token it needs `Bearer <token>`."""
        token = settings.metrics_token
        if token is None and settings.env != "dev":
            return JSONResponse(error_body("not_found", "metrics are disabled", None, {}), 404)
        if token is not None and not secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {token}"
        ):
            return JSONResponse(error_body("unauthorized", "metrics need the scrape token", None, {}), 401)
        await metrics.refresh(db_engine)
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

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

    instrument(app, db_engine, provider)
    return app
