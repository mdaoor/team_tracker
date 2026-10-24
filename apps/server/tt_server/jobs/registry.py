"""Outbox routing: which Procrastinate tasks run after which events (keyed by `event_type` prefix)."""

from __future__ import annotations

from collections.abc import Callable

from tt_server.settings import Settings, get_settings

JOBS: dict[str, list[str]] = {
    "review.created": ["llm.suggest_for_event"],
    **{
        prefix: ["governance.on_event"]
        for prefix in ("item.", "activity.", "stage.", "classification.", "dailylog.", "evidence.", "time.")
    },
}
for _prefix in ("item.", "time.", "stage.", "classification."):
    JOBS.setdefault(_prefix, []).append("jira.on_event")

# Tasks that only make sense when a feature is configured; otherwise they are never enqueued.
ENABLED: dict[str, Callable[[Settings], bool]] = {
    "llm.suggest_for_event": lambda s: bool(s.llm_gateway_url and s.features.llm_suggestions),
    "governance.on_event": lambda s: s.features.governance,
    "jira.on_event": lambda s: s.features.jira_sync,
}


def _always(_settings: Settings) -> bool:
    return True


def register(event_prefix: str, task_name: str) -> None:
    tasks = JOBS.setdefault(event_prefix, [])
    if task_name not in tasks:
        tasks.append(task_name)


def tasks_for(event_type: str) -> list[str]:
    settings = get_settings()
    out: list[str] = []
    for prefix, names in JOBS.items():
        if event_type.startswith(prefix):
            out.extend(n for n in names if n not in out and ENABLED.get(n, _always)(settings))
    return out
