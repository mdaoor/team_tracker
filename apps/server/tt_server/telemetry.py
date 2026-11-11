"""OpenTelemetry wiring. A tracer provider is always installed; an OTLP exporter only when configured."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider


def configure_telemetry(service_name: str = "tt-server", otlp_endpoint: str | None = None) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:  # pragma: no cover - needs an OTLP collector
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    if not isinstance(trace.get_tracer_provider(), TracerProvider):  # set-once global
        trace.set_tracer_provider(provider)
    return provider


def instrument(app: object, engine: object, provider: TracerProvider | None = None) -> None:
    """Request spans (FastAPI) with the SQL they ran as children (SQLAlchemy); the job spans continue the same trace."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)  # type: ignore[arg-type]
    sync: Any = getattr(engine, "sync_engine", engine)
    if not getattr(
        sync, "_tt_instrumented", False
    ):  # one tracer per engine, however many apps share it (tests)
        SQLAlchemyInstrumentor().instrument(engine=sync, tracer_provider=provider, skip_dep_check=True)
        sync._tt_instrumented = True
