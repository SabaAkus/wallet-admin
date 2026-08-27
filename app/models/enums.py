from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    pass


class UserRole(StringEnum):
    VIEWER = "VIEWER"
    FINANCE_OPERATOR = "FINANCE_OPERATOR"
    ADMINISTRATOR = "ADMINISTRATOR"


class TransactionType(StringEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    GAME_ENTRY = "GAME_ENTRY"
    GAME_WIN = "GAME_WIN"
    REVERSAL = "REVERSAL"


class TransactionDirection(StringEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class TransactionStatus(StringEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REVERSED = "REVERSED"


class TransactionSource(StringEnum):
    HISTORICAL_IMPORT = "HISTORICAL_IMPORT"
    SYSTEM = "SYSTEM"

