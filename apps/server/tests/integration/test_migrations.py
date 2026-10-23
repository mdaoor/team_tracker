from __future__ import annotations

import tt_server.db.models  # noqa: F401
from alembic import command
from sqlalchemy import create_engine, inspect, text
from tt_schema import ALL_MODELS
from tt_server.db.base import Base

from tests.conftest import alembic_config


def test_every_model_has_a_table(migrated_url: str) -> None:
    eng = create_engine(migrated_url)
    tables = set(inspect(eng).get_table_names())
    eng.dispose()
    assert len(ALL_MODELS) == 65
    assert {t.name for t in Base.metadata.sorted_tables} <= tables
    extras = {
        "project_key_sequences",
        "api_tokens",
        "review_items",
        "glossary_entries",
        "translator_rules",
        "translator_feedback",
    }  # server-owned tables
    assert len(Base.metadata.tables) == len(ALL_MODELS) + len(extras)
    assert extras <= set(Base.metadata.tables)


def test_round_trip(migrated_url: str) -> None:
    cfg = alembic_config(migrated_url)
    command.downgrade(cfg, "base")
    eng = create_engine(migrated_url)
    assert [t for t in inspect(eng).get_table_names() if not t.startswith("procrastinate_")] == [
        "alembic_version"
    ]
    command.upgrade(cfg, "head")
    command.check(cfg)  # models and migration agree
    with eng.connect() as conn:
        exts = {r[0] for r in conn.execute(text("SELECT extname FROM pg_extension"))}
    eng.dispose()
    assert {"pg_trgm", "ltree", "btree_gist"} <= exts
