from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import (
    TransactionDirection,
    TransactionSource,
    TransactionStatus,
    TransactionType,
)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_transaction_positive_amount"),
        CheckConstraint("length(currency) = 3", name="ck_transaction_currency_length"),
        CheckConstraint(
            "transaction_type IN ('DEPOSIT', 'WITHDRAWAL', 'GAME_ENTRY', 'GAME_WIN', 'REVERSAL')",
            name="transactiontype",
        ),
        CheckConstraint(
            "direction IN ('CREDIT', 'DEBIT')", name="transactiondirection"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'FAILED', 'CANCELLED', 'REVERSED')",
            name="transactionstatus",
        ),
        CheckConstraint(
            "source IN ('HISTORICAL_IMPORT', 'SYSTEM')", name="transactionsource"
        ),
        CheckConstraint(
            "(transaction_type = 'DEPOSIT' AND direction = 'CREDIT') OR "
            "(transaction_type = 'WITHDRAWAL' AND direction = 'DEBIT') OR "
            "(transaction_type = 'GAME_ENTRY' AND direction = 'DEBIT') OR "
            "(transaction_type = 'GAME_WIN' AND direction = 'CREDIT') OR "
            "transaction_type = 'REVERSAL'",
            name="ck_transaction_type_direction",
        ),
        CheckConstraint(
            "(transaction_type = 'REVERSAL' AND reverses_transaction_id IS NOT NULL) OR "
            "(transaction_type != 'REVERSAL' AND reverses_transaction_id IS NULL)",
            name="ck_transaction_reversal_link",
        ),
        CheckConstraint(
            "reverses_transaction_id IS NULL OR reverses_transaction_id != id",
            name="ck_transaction_not_self_reversal",
        ),
        CheckConstraint(
            "source != 'SYSTEM' OR source_balance_after_minor IS NULL",
            name="ck_system_transaction_has_no_source_balance",
        ),
        CheckConstraint(
            "source != 'SYSTEM' OR created_by_user_id IS NOT NULL",
            name="ck_system_transaction_has_actor",
        ),
        Index(
            "uq_approved_reversal_per_original",
            "reverses_transaction_id",
            unique=True,
            sqlite_where=text(
                "transaction_type = 'REVERSAL' AND status = 'APPROVED'"
            ),
            postgresql_where=text(
                "transaction_type = 'REVERSAL' AND status = 'APPROVED'"
            ),
        ),
        Index("ix_transactions_occurred_at", "occurred_at"),
        Index("ix_transactions_player_occurred", "player_id", "occurred_at"),
        Index("ix_transactions_operator_occurred", "operator_id", "occurred_at"),
        Index("ix_transactions_status_occurred", "status", "occurred_at"),
        Index("ix_transactions_type_occurred", "transaction_type", "occurred_at"),
        Index("ix_transactions_country_occurred", "country", "occurred_at"),
        Index("ix_transactions_wallet_occurred", "wallet_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    external_transaction_id: Mapped[str] = mapped_column(
        String(150), nullable=False, unique=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT"), nullable=False
    )
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False
    )
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("operators.id", ondelete="RESTRICT"), nullable=False
    )
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType,
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        Enum(
            TransactionDirection,
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    source: Mapped[TransactionSource] = mapped_column(
        Enum(
            TransactionSource,
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    source_balance_after_minor: Mapped[int | None] = mapped_column(BigInteger)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reverses_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT")
    )
    note: Mapped[str | None] = mapped_column(Text)
