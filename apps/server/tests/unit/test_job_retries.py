"""Phase 13 R3.3: every event-driven job retries with back-off before it dead-letters."""

from procrastinate import RetryStrategy
from tt_server.jobs import registry
from tt_server.jobs.app import app, load_tasks


def test_every_routed_task_has_a_retry_strategy() -> None:
    load_tasks()
    routed = {name for names in registry.JOBS.values() for name in names}
    assert routed >= {"governance.on_event", "llm.suggest_for_event", "jira.on_event"}
    for name in sorted(routed):
        strat = app.tasks[name].retry_strategy
        assert isinstance(strat, RetryStrategy), f"{name} would be lost on the first transient error"
        assert strat.max_attempts >= 5 and strat.exponential_wait >= 3, name


def test_retry_backoff_grows_then_gives_up() -> None:
    from types import SimpleNamespace
    from typing import Any

    from tt_server.jobs.retry import EVENT_RETRY

    def decide(attempts: int) -> Any:
        return EVENT_RETRY.get_retry_decision(exception=ValueError(), job=SimpleNamespace(attempts=attempts))  # type: ignore[arg-type]

    from datetime import UTC, datetime

    secs = [(decide(n).retry_at - datetime.now(UTC)).total_seconds() for n in (1, 2, 3, 4)]
    assert secs == sorted(secs) and secs[0] >= 10 and secs[-1] <= 3600  # grows, but within the hour
    assert decide(5) is None  # the fifth failure dead-letters (status `failed`, tt_jobs_failed, TTJobsFailed alert)
