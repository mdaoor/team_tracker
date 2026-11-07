"""Alembic environment: reads the same `Settings` as the app (sync psycopg driver)."""

from __future__ import annotations

import tt_server.db.models  # noqa: F401  (register tables)
from alembic import context
from sqlalchemy import create_engine
from tt_server.db.base import Base
from tt_server.settings import get_settings

target_metadata = Base.metadata


def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    """Procrastinate owns its tables (`procrastinate schema --apply`); Alembic must not touch them."""
    if type_ == "table" and name and name.startswith("procrastinate_"):
        return False
    return not (type_ == "index" and name and "procrastinate_" in name)


def _url() -> str:
    return context.config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True, include_object=include_object
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=include_object
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
