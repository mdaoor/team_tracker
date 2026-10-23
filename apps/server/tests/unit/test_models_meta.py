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
