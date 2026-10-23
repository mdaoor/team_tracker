"""Outbox routing: which Procrastinate tasks run after which events (keyed by `event_type` prefix).

Empty in Phase 0; later phases register e.g. `register("item.", "tt_server.jobs.propagation.run")`.
"""

from __future__ import annotations

JOBS: dict[str, list[str]] = {}


def register(event_prefix: str, task_name: str) -> None:
    tasks = JOBS.setdefault(event_prefix, [])
    if task_name not in tasks:
        tasks.append(task_name)


def tasks_for(event_type: str) -> list[str]:
    out: list[str] = []
    for prefix, names in JOBS.items():
        if event_type.startswith(prefix):
            out.extend(n for n in names if n not in out)
    return out
