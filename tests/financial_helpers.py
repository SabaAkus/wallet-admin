from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.models import Operator, Player, User, UserRole, Wallet
from app.services.transaction_service import (
    CreateTransactionCommand,
    FinancialService,
)
from app.models import TransactionDirection, TransactionType


@dataclass(frozen=True)
class FinancialContext:
    user_id: int
    player_id: int
    player_external_id: str
    operator_id: int
    operator_code: str
    wallet_id: int
    currency: str


def seed_financial_context(
    factory: sessionmaker[Session], *, starting_balance_minor: int = 10_000
) -> FinancialContext:
    with factory.begin() as session:
        user = User(
            username="finance-admin",
            password_hash="test-only",
            role=UserRole.ADMINISTRATOR,
        )
        operator = Operator(code="OP-A", name="Operator A")
        player = Player(external_player_id="PLAYER-A", current_country="Georgia")
        session.add_all([user, operator, player])
        session.flush()
        wallet = Wallet(
            player_id=player.id,
            currency="EUR",
            current_balance_minor=starting_balance_minor,
            balance_initialized=True,
            version=0,
        )
        session.add(wallet)
        session.flush()
        return FinancialContext(
            user_id=user.id,
            player_id=player.id,
            player_external_id=player.external_player_id,
            operator_id=operator.id,
            operator_code=operator.code,
            wallet_id=wallet.id,
            currency=wallet.currency,
        )


def command_for(
    context: FinancialContext,
    external_id: str,
    *,
    transaction_type: TransactionType = TransactionType.DEPOSIT,
    direction: TransactionDirection = TransactionDirection.CREDIT,
    amount_minor: int = 1_000,
) -> CreateTransactionCommand:
    return CreateTransactionCommand(
        external_transaction_id=external_id,
        player_external_id=context.player_external_id,
        operator_code=context.operator_code,
        country="Georgia",
        transaction_type=transaction_type,
        direction=direction,
        amount_minor=amount_minor,
        currency=context.currency,
        occurred_at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        actor_user_id=context.user_id,
    )


def create_and_approve(
    service: FinancialService,
    context: FinancialContext,
    external_id: str,
    *,
    transaction_type: TransactionType,
    direction: TransactionDirection,
    amount_minor: int,
):
    creation = service.create_transaction(
        command_for(
            context,
            external_id,
            transaction_type=transaction_type,
            direction=direction,
            amount_minor=amount_minor,
        )
    )
    return creation.transaction, service.approve_transaction(
        creation.transaction.id, actor_user_id=context.user_id
    )
