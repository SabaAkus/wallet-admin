from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BalancePosting(Base):
    __tablename__ = "balance_postings"
    __table_args__ = (
        UniqueConstraint("wallet_id", "wallet_version", name="uq_posting_wallet_version"),
        CheckConstraint("delta_minor != 0", name="ck_posting_nonzero_delta"),
        CheckConstraint(
            "balance_after_minor = balance_before_minor + delta_minor",
            name="ck_posting_balance_math",
        ),
        CheckConstraint(
            "balance_before_minor >= 0 AND balance_after_minor >= 0",
            name="ck_posting_nonnegative_balances",
        ),
        CheckConstraint("wallet_version > 0", name="ck_posting_positive_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    delta_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_before_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    wallet_version: Mapped[int] = mapped_column(Integer, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.current_timestamp()
    )
    posted_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

