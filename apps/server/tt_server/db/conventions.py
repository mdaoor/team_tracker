"""Schema conventions applied to every table once all models are imported (Phase 13 R3.1, R4.4; ADR-0049).

* every foreign key gets an index that starts with its columns unless one already exists (primary key, unique constraint
  or index), named `ix_<table>_<cols>` (hashed past 63 bytes);
* every foreign key states its delete rule: CASCADE for rows that are part of their parent, RESTRICT otherwise.

Migrations `0011`/`0012` carry the same facts as explicit statements; `test_round_trip` fails if the two ever disagree.
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa

CASCADE: frozenset[tuple[str, str]] = frozenset(
    {
        ("checklist_items", "checklist_id"),
        ("checklists", "work_item_id"),
        ("approval_decisions", "approval_id"),
        ("risk_links", "risk_id"),
        ("risk_actions", "risk_id"),
        ("custom_field_values", "work_item_id"),
        ("custom_field_values", "field_definition_id"),
        ("weights", "work_item_id"),
        ("assignments", "work_item_id"),
        ("estimates", "work_item_id"),
        ("baselines", "work_item_id"),
        ("work_item_links", "from_item_id"),
        ("work_item_links", "to_item_id"),
        ("stage_transition_rules", "process_id"),
        ("project_key_sequences", "project_id"),
    }
)


def index_name(table: str, cols: tuple[str, ...]) -> str:
    name = f"ix_{table}_{'_'.join(cols)}"
    if len(name) <= 63:
        return name
    return f"ix_{table[:30]}_{hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:10]}"  # noqa: S324


def _covered(table: sa.Table, cols: tuple[str, ...]) -> bool:
    n = len(cols)
    if tuple(c.name for c in table.primary_key.columns)[:n] == cols:
        return True
    for ix in table.indexes:
        if tuple(c.name for c in ix.columns)[:n] == cols:
            return True
    for uc in table.constraints:
        if isinstance(uc, sa.UniqueConstraint) and tuple(c.name for c in uc.columns)[:n] == cols:
            return True
    return False


def apply(metadata: sa.MetaData) -> None:
    for table in metadata.sorted_tables:
        if table.name.startswith("procrastinate"):
            continue
        for fk in [c for c in table.constraints if isinstance(c, sa.ForeignKeyConstraint)]:
            cols = tuple(c.name for c in fk.columns)
            fk.ondelete = "CASCADE" if (table.name, cols[0]) in CASCADE else "RESTRICT"
            if not _covered(table, cols):
                sa.Index(index_name(table.name, cols), *[table.c[c] for c in cols])
