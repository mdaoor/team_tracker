"""Tracing across the queue (Phase 13 R3.9): a job's span is a child of the request that deferred it.

`inject()` stores the W3C `traceparent` in the job arguments when the outbox defers it; `traced_task` pops it, continues
the trace and wraps the task body in a span named `job <task name>`.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.propagate import inject as _inject
from opentelemetry.trace import Status, StatusCode

from tt_server.jobs.app import app

TRACE_KEY = "traceparent"
_tracer = trace.get_tracer("tt.jobs")


def inject_context() -> dict[str, str]:
    """The current span's context as a carrier ({} when there is no active span)."""
    carrier: dict[str, str] = {}
    _inject(carrier)
    return {TRACE_KEY: carrier[TRACE_KEY]} if TRACE_KEY in carrier else {}


def traced_task(**task_kwargs: Any) -> Callable[[Callable[..., Awaitable[Any]]], Any]:
    """`@app.task(**task_kwargs)` plus a span per execution, linked to the deferring request."""
    name = task_kwargs["name"]

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Any:
        @functools.wraps(fn)
        async def run(*args: Any, **kwargs: Any) -> Any:
            parent = extract({TRACE_KEY: kwargs.pop(TRACE_KEY)}) if TRACE_KEY in kwargs else None
            with _tracer.start_as_current_span(
                f"job {name}", context=parent, attributes={"tt.job": name}
            ) as span:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR))
                    raise

        return app.task(**task_kwargs)(run)

    return decorate
