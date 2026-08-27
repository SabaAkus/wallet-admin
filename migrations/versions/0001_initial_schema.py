"""Create the seven-table wallet admin schema.

Revision ID: 0001
Revises:
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "VIEWER",
                "FINANCE_OPERATOR",
                "ADMINISTRATOR",
                name="userrole",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "operators",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("code", name="uq_operators_code"),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_player_id", sa.String(100), nullable=False),
        sa.Column("current_country", sa.String(100)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("external_player_id", name="uq_players_external_player_id"),
    )

    # The anchor FK is added after transactions to resolve the deliberate
    # wallets <-> transactions cycle without adding another table.
    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "current_balance_minor", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "balance_initialized", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("historical_anchor_transaction_id", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "current_balance_minor >= 0", name="ck_wallet_nonnegative_balance"
        ),
        sa.CheckConstraint("version >= 0", name="ck_wallet_nonnegative_version"),
        sa.CheckConstraint("length(currency) = 3", name="ck_wallet_currency_length"),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name="fk_wallets_player_id", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("player_id", "currency", name="uq_wallet_player_currency"),
    )
    op.create_index("ix_wallets_player_id", "wallets", ["player_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_transaction_id", sa.String(150), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(100), nullable=False),
        sa.Column(
            "transaction_type",
            sa.Enum(
                "DEPOSIT",
                "WITHDRAWAL",
                "GAME_ENTRY",
                "GAME_WIN",
                "REVERSAL",
                name="transactiontype",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.Enum(
                "CREDIT",
                "DEBIT",
                name="transactiondirection",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "APPROVED",
                "FAILED",
                "CANCELLED",
                "REVERSED",
                name="transactionstatus",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer()),
        sa.Column(
            "source",
            sa.Enum(
                "HISTORICAL_IMPORT",
                "SYSTEM",
                name="transactionsource",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_balance_after_minor", sa.BigInteger()),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("reverses_transaction_id", sa.String(36)),
        sa.Column("note", sa.Text()),
        sa.CheckConstraint("amount_minor > 0", name="ck_transaction_positive_amount"),
        sa.CheckConstraint("length(currency) = 3", name="ck_transaction_currency_length"),
        sa.CheckConstraint(
            "(transaction_type = 'DEPOSIT' AND direction = 'CREDIT') OR "
            "(transaction_type = 'WITHDRAWAL' AND direction = 'DEBIT') OR "
            "(transaction_type = 'GAME_ENTRY' AND direction = 'DEBIT') OR "
            "(transaction_type = 'GAME_WIN' AND direction = 'CREDIT') OR "
            "transaction_type = 'REVERSAL'",
            name="ck_transaction_type_direction",
        ),
        sa.CheckConstraint(
            "(transaction_type = 'REVERSAL' AND reverses_transaction_id IS NOT NULL) OR "
            "(transaction_type != 'REVERSAL' AND reverses_transaction_id IS NULL)",
            name="ck_transaction_reversal_link",
        ),
        sa.CheckConstraint(
            "reverses_transaction_id IS NULL OR reverses_transaction_id != id",
            name="ck_transaction_not_self_reversal",
        ),
        sa.CheckConstraint(
            "source != 'SYSTEM' OR source_balance_after_minor IS NULL",
            name="ck_system_transaction_has_no_source_balance",
        ),
        sa.CheckConstraint(
            "source != 'SYSTEM' OR created_by_user_id IS NOT NULL",
            name="ck_system_transaction_has_actor",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name="fk_transactions_player_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.id"], name="fk_transactions_wallet_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["operators.id"], name="fk_transactions_operator_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_transactions_created_by", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reverses_transaction_id"],
            ["transactions.id"],
            name="fk_transactions_reverses",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "external_transaction_id", name="uq_transactions_external_transaction_id"
        ),
        sa.UniqueConstraint(
            "reverses_transaction_id", name="uq_transaction_single_reversal"
        ),
    )
    op.create_index("ix_transactions_occurred_at", "transactions", ["occurred_at"])
    op.create_index(
        "ix_transactions_player_occurred", "transactions", ["player_id", "occurred_at"]
    )
    op.create_index(
        "ix_transactions_operator_occurred", "transactions", ["operator_id", "occurred_at"]
    )
    op.create_index(
        "ix_transactions_status_occurred", "transactions", ["status", "occurred_at"]
    )
    op.create_index(
        "ix_transactions_type_occurred", "transactions", ["transaction_type", "occurred_at"]
    )
    op.create_index(
        "ix_transactions_country_occurred", "transactions", ["country", "occurred_at"]
    )
    op.create_index(
        "ix_transactions_wallet_occurred", "transactions", ["wallet_id", "occurred_at"]
    )

    op.create_table(
        "balance_postings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.String(36), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("delta_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_before_minor", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=False),
        sa.Column("wallet_version", sa.Integer(), nullable=False),
        sa.Column(
            "posted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("posted_by_user_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("delta_minor != 0", name="ck_posting_nonzero_delta"),
        sa.CheckConstraint(
            "balance_after_minor = balance_before_minor + delta_minor",
            name="ck_posting_balance_math",
        ),
        sa.CheckConstraint(
            "balance_before_minor >= 0 AND balance_after_minor >= 0",
            name="ck_posting_nonnegative_balances",
        ),
        sa.CheckConstraint("wallet_version > 0", name="ck_posting_positive_version"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_balance_postings_transaction_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.id"], name="fk_balance_postings_wallet_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["posted_by_user_id"],
            ["users.id"],
            name="fk_balance_postings_posted_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("transaction_id", name="uq_balance_postings_transaction_id"),
        sa.UniqueConstraint("wallet_id", "wallet_version", name="uq_posting_wallet_version"),
    )
    op.create_index("ix_balance_postings_wallet_id", "balance_postings", ["wallet_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("before_values", sa.JSON()),
        sa.Column("after_values", sa.JSON()),
        sa.Column("reason", sa.Text()),
        sa.Column("request_id", sa.String(100)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_audit_events_actor", ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_audit_entity", "audit_events", ["entity_type", "entity_id"])
    op.create_index(
        "ix_audit_actor_created", "audit_events", ["actor_user_id", "created_at"]
    )

    with op.batch_alter_table("wallets", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_wallets_historical_anchor_transaction_id_transactions",
            "transactions",
            ["historical_anchor_transaction_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("wallets", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "fk_wallets_historical_anchor_transaction_id_transactions",
            type_="foreignkey",
        )

    op.drop_index("ix_audit_actor_created", table_name="audit_events")
    op.drop_index("ix_audit_entity", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_balance_postings_wallet_id", table_name="balance_postings")
    op.drop_table("balance_postings")
    op.drop_index("ix_transactions_wallet_occurred", table_name="transactions")
    op.drop_index("ix_transactions_country_occurred", table_name="transactions")
    op.drop_index("ix_transactions_type_occurred", table_name="transactions")
    op.drop_index("ix_transactions_status_occurred", table_name="transactions")
    op.drop_index("ix_transactions_operator_occurred", table_name="transactions")
    op.drop_index("ix_transactions_player_occurred", table_name="transactions")
    op.drop_index("ix_transactions_occurred_at", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_wallets_player_id", table_name="wallets")
    op.drop_table("wallets")
    op.drop_table("players")
    op.drop_table("operators")
    op.drop_table("users")
