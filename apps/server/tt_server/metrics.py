"""Prometheus metrics (phase 12 §2): RED per route, business gauges read on scrape, job backlog.

One registry per application instance (tests create many apps). Route labels are the route *template* (`/items/{id_or_key}`), never the
concrete path, so cardinality stays bounded."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.15, 0.3, 0.5, 1, 2, 5, 10)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "tt_http_requests_total",
            "HTTP requests by route template and status class",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "tt_http_request_duration_seconds",
            "Request latency by route template",
            ["method", "route"],
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.errors = Counter(
            "tt_http_errors_total", "5xx responses by route template", ["route"], registry=self.registry
        )
        self.capture_messages = Counter(
            "tt_capture_batches_total", "Capture batches accepted", registry=self.registry
        )
        self.gauges = {
            name: Gauge(f"tt_{name}", doc, registry=self.registry)
            for name, doc in (
                ("review_queue_open", "Open review-queue items"),
                ("violations_open", "Open or acknowledged policy violations"),
                ("jobs_todo", "Procrastinate jobs waiting"),
                ("jobs_failed", "Procrastinate jobs failed"),
                ("jobs_oldest_todo_age_seconds", "Age of the oldest waiting job"),
                ("jira_push_failed_1h", "Jira pushes that failed in the last hour"),
                ("events_last_5m", "Events written in the last five minutes"),
            )
        }

    async def middleware(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            self.requests.labels(request.method, route, f"{status // 100}xx").inc()
            self.latency.labels(request.method, route).observe(time.perf_counter() - t0)
            if status >= 500:
                self.errors.labels(route).inc()
            if route.endswith("/capture/batches") and status < 300 and request.method == "POST":
                self.capture_messages.inc()

    async def refresh(self, engine: AsyncEngine) -> None:
        """Business and job gauges are read from the database at scrape time (a scrape is cheap: eight indexed counts)."""
        q = {
            "review_queue_open": "SELECT count(*) FROM review_items WHERE status = 'open' AND deleted_at IS NULL",
            "violations_open": "SELECT count(*) FROM violations WHERE status IN ('open','acknowledged') AND deleted_at IS NULL",
            "jobs_todo": "SELECT count(*) FROM procrastinate_jobs WHERE status = 'todo'",
            "jobs_failed": "SELECT count(*) FROM procrastinate_jobs WHERE status = 'failed'",
            "jobs_oldest_todo_age_seconds": (
                "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(scheduled_at)), 0) FROM procrastinate_jobs WHERE status = 'todo'"
            ),
            "jira_push_failed_1h": "SELECT count(*) FROM events WHERE event_type = 'jira.push_failed' AND occurred_at > now() - interval '1 hour'",
            "events_last_5m": "SELECT count(*) FROM events WHERE occurred_at > now() - interval '5 minutes'",
        }
        async with engine.connect() as conn:
            for name, sql in q.items():
                try:
                    self.gauges[name].set(float((await conn.execute(text(sql))).scalar() or 0))
                except Exception:  # noqa: BLE001 - a missing table (job schema not installed yet) must not break the scrape
                    await conn.rollback()
                    self.gauges[name].set(float("nan"))

    def render(self) -> bytes:
        return generate_latest(self.registry)
