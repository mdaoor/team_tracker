"""Apply the Procrastinate queue schema (idempotent) into the application database."""

from __future__ import annotations

import psycopg
from procrastinate import App, PsycopgConnector


def ensure_procrastinate_schema(dsn: str) -> bool:
    """Create the `procrastinate_*` tables if missing. Returns True when the schema was applied."""
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT to_regclass('procrastinate_jobs')").fetchone()
        if row and row[0]:
            return False
    app = App(connector=PsycopgConnector(conninfo=dsn))
    with app.open():
        app.schema_manager.apply_schema()
    return True
