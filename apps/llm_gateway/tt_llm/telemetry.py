"""Token / cost / latency metrics per task (OpenTelemetry; no-ops until an SDK provider is configured)."""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("tt_llm")
REQUESTS = _meter.create_counter("tt_llm_requests", description="Gateway requests by task and outcome")
COST = _meter.create_counter("tt_llm_cost_usd", unit="USD", description="Model spend by task and workspace")
LATENCY = _meter.create_histogram("tt_llm_latency_ms", unit="ms", description="Task latency")
