"""Phase 13 R3.9: API request -> SQL -> deferred job form one trace."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tt_server.jobs.otel import TRACE_KEY, inject_context, traced_task


async def test_job_span_continues_the_trace_of_the_request_that_deferred_it() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider) if not isinstance(trace.get_tracer_provider(), TracerProvider) else None
    import tt_server.jobs.otel as otel

    otel._tracer = provider.get_tracer("tt.jobs")
    seen: list[dict[str, object]] = []

    @traced_task(name="test.trace_probe")
    async def probe(event_id: str) -> str:
        seen.append({"event_id": event_id})
        return event_id

    api_tracer = provider.get_tracer("api")
    with api_tracer.start_as_current_span("POST /capture/batches") as request_span:
        carrier = inject_context()  # what the outbox stores in the job arguments
    assert TRACE_KEY in carrier
    assert await probe.func(event_id="e1", **carrier) == "e1"  # type: ignore[attr-defined]
    spans = {s.name: s for s in exporter.get_finished_spans()}
    job, req = spans["job test.trace_probe"], spans["POST /capture/batches"]
    assert job.context.trace_id == req.context.trace_id
    assert job.parent is not None and job.parent.span_id == request_span.get_span_context().span_id
    assert seen == [{"event_id": "e1"}]  # the carrier never reaches the task body


async def test_a_failing_job_marks_its_span_as_an_error() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    import tt_server.jobs.otel as otel

    otel._tracer = provider.get_tracer("tt.jobs")

    @traced_task(name="test.fail_probe")
    async def boom() -> None:
        raise ValueError("nope")

    try:
        await boom.func()  # type: ignore[attr-defined]
    except ValueError:
        pass
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "job test.fail_probe"]
    assert span.status.status_code.name == "ERROR" and span.events[0].name == "exception"
