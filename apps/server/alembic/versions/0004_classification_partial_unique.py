"""classification_partial_unique

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.drop_constraint(
        op.f("uq_classifications_target_type_target_id_dimension_id_value_id"),
        "classifications",
        type_="unique",
    )
    op.create_index(
        "ix_classifications_dimension_value", "classifications", ["dimension_id", "value_id"], unique=False
    )
    op.create_index(
        "ix_classifications_target", "classifications", ["target_type", "target_id"], unique=False
    )
    op.create_index(
        "uq_classifications_active",
        "classifications",
        ["target_type", "target_id", "dimension_id", "value_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:

    op.drop_index(
        "uq_classifications_active",
        table_name="classifications",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_classifications_target", table_name="classifications")
    op.drop_index("ix_classifications_dimension_value", table_name="classifications")
    # soft-deleted rows may repeat a (target, dimension, value); the old full constraint cannot hold them
    op.execute("DELETE FROM classifications WHERE deleted_at IS NOT NULL")
    op.create_unique_constraint(
        op.f("uq_classifications_target_type_target_id_dimension_id_value_id"),
        "classifications",
        ["target_type", "target_id", "dimension_id", "value_id"],
        postgresql_nulls_not_distinct=False,
    )
