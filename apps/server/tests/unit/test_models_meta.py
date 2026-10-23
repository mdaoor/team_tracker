from __future__ import annotations

import tt_server.db.models  # noqa: F401
from tt_server.db import models
from tt_server.db.base import Base, WorkspaceScopedMixin


def test_workspace_scoping_is_enforced_by_the_mixin() -> None:
    """Every table with a mandatory workspace_id column inherits WorkspaceScopedMixin (architecture §4.6)."""
    unscoped_ok = {"people", "roles", "policy_templates", "habit_definitions"}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table = mapper.local_table
        has_ws = "workspace_id" in table.c and not table.c.workspace_id.nullable
        if has_ws:
            assert issubclass(cls, WorkspaceScopedMixin), table.name
        if table.name in unscoped_ok:
            assert not has_ws, table.name


def test_all_tables_have_standard_base_columns() -> None:
    for table in Base.metadata.sorted_tables:
        if table.name == "project_key_sequences":
            continue  # keyed by project_id
        assert {"id", "created_at", "updated_at", "created_by", "deleted_at"} <= set(table.c.keys()), (
            table.name
        )


def test_events_has_single_writer() -> None:
    import pathlib

    root = pathlib.Path(models.__file__).resolve().parents[2]
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name != "writer.py" and "db/models" not in str(p) and "EventRow(" in p.read_text()
    ]
    assert offenders == []


def test_orm_defaults_cover_schema_default_factories() -> None:
    """Schema fields with a default_factory (e.g. utcnow) must not be NOT NULL without an ORM default."""
    from tt_schema import ALL_MODELS

    base = {"id", "created_at", "updated_at", "created_by", "deleted_at"}
    missing = [
        f"{model.__name__}.{name}"
        for model in ALL_MODELS
        for name, field in model.model_fields.items()
        if name not in base
        and field.default_factory is not None
        and (col := getattr(models, model.__name__).__table__.c[name]).nullable is False
        and col.default is None
        and col.server_default is None
    ]
    assert missing == []
