# ADR-0044: Event-driven jobs retry five times with a base-4 exponential wait
Date: 2026-10-25   Status: accepted (Phase 13 R3.3; review T5). Deviates from the plan's literal `exponential_wait=10`.

## Context
`governance.on_event` and `llm.suggest_for_event` had no retry, so a database blip silently dropped an event.

## Decision
`tt_server/jobs/retry.py: EVENT_RETRY = RetryStrategy(max_attempts=5, exponential_wait=4)`. Procrastinate waits
`exponential_wait ** (attempt + 1)` seconds, so 4 gives 16 s, 64 s, 4 min, 17 min; the plan's 10 would wait 100 s, 17 min,
2.8 h and 28 h, longer than the data stays relevant. After the fifth failure the job is `failed` (the dead-letter state),
counted by `tt_jobs_failed` and alerted by `TTJobsFailed`. A test asserts every task routed by `jobs/registry.py` has the strategy.

## Consequences
Jobs must stay idempotent (they were: keyed by event id).
