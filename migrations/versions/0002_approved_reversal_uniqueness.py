"""Allow failed reversal attempts but only one approved reversal.

Revision ID: 0002
Revises: 0001
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPROVED_REVERSAL_PREDICATE = (
    "transaction_type = 'REVERSAL' AND status = 'APPROVED'"
)


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_constraint("uq_transaction_single_reversal", type_="unique")

    op.create_index(
        "uq_approved_reversal_per_original",
        "transactions",
        ["reverses_transaction_id"],
        unique=True,
        sqlite_where=sa.text(APPROVED_REVERSAL_PREDICATE),
        postgresql_where=sa.text(APPROVED_REVERSAL_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("uq_approved_reversal_per_original", table_name="transactions")
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_transaction_single_reversal", ["reverses_transaction_id"]
        )
