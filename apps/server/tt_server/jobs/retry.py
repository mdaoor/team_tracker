"""Retry policy of event-driven jobs (Phase 13 R3.3, review T5): a transient failure must not silently drop an event.

Five attempts. Procrastinate waits `exponential_wait ** (attempt + 1)` seconds, so a base of 4 retries after 16 s, 64 s,
4 min and 17 min (the plan's literal `exponential_wait=4` would wait 100 s, 17 min, 2.8 h, 28 h: ADR-0044 records the change). After the last one Procrastinate marks the job `failed`;
that is the dead-letter state, exported as `tt_jobs_failed` and alerted on in `ops/prometheus/alerts.yml`.
"""

from procrastinate import RetryStrategy

EVENT_RETRY = RetryStrategy(max_attempts=5, exponential_wait=4)
