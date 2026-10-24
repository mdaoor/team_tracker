"""Procrastinate application (queue lives in the same Postgres DB, `procrastinate_*` tables)."""

from __future__ import annotations

import importlib

from procrastinate import App, PsycopgConnector

from tt_server.settings import get_settings

app = App(connector=PsycopgConnector(conninfo=get_settings().psycopg_dsn))

# Modules that declare @app.task functions; later phases append here.
TASK_MODULES: list[str] = [
    "tt_server.jobs.tasks",
    "tt_server.jobs.llm_tasks",
    "tt_server.jobs.governance_tasks",
]


def load_tasks() -> None:
    for module in TASK_MODULES:
        importlib.import_module(module)
