from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("player_id", "currency", name="uq_wallet_player_currency"),
        CheckConstraint("current_balance_minor >= 0", name="ck_wallet_nonnegative_balance"),
        CheckConstraint("version >= 0", name="ck_wallet_nonnegative_version"),
        CheckConstraint("length(currency) = 3", name="ck_wallet_currency_length"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    current_balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    balance_initialized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    historical_anchor_transaction_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "transactions.id",
            name="fk_wallets_historical_anchor_transaction_id_transactions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
