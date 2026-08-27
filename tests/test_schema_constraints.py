from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    BalancePosting,
    Operator,
    Player,
    Transaction,
    TransactionDirection,
    TransactionSource,
    TransactionStatus,
    TransactionType,
    User,
    UserRole,
    Wallet,
)


def test_migration_creates_exact_primary_tables(app) -> None:
    inspector = inspect(app.extensions["database_engine"])
    application_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert application_tables == {
        "users",
        "operators",
        "players",
        "wallets",
        "transactions",
        "balance_postings",
        "audit_events",
    }


def test_wallet_is_unique_by_player_and_currency(session) -> None:
    player = Player(external_player_id="P-UNIQUE")
    session.add(player)
    session.flush()
    session.add(Wallet(player_id=player.id, currency="USD"))
    session.flush()

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Wallet(player_id=player.id, currency="USD"))
            session.flush()


def _supporting_records(session):
    user = User(
        username=f"admin-{uuid.uuid4()}",
        password_hash="not-a-real-hash",
        role=UserRole.ADMINISTRATOR,
    )
    operator = Operator(code=f"OP-{uuid.uuid4()}", name="Operator")
    player = Player(external_player_id=f"P-{uuid.uuid4()}")
    session.add_all([user, operator, player])
    session.flush()
    wallet = Wallet(
        player_id=player.id,
        currency="USD",
        current_balance_minor=10_000,
        balance_initialized=True,
    )
    session.add(wallet)
    session.flush()
    return user, operator, player, wallet


def _transaction(session, *, direction=TransactionDirection.CREDIT):
    user, operator, player, wallet = _supporting_records(session)
    transaction = Transaction(
        id=str(uuid.uuid4()),
        external_transaction_id=f"TX-{uuid.uuid4()}",
        player_id=player.id,
        wallet_id=wallet.id,
        operator_id=operator.id,
        country="Georgia",
        transaction_type=TransactionType.DEPOSIT,
        direction=direction,
        amount_minor=1_000,
        currency="USD",
        status=TransactionStatus.PENDING,
        occurred_at=datetime.now(UTC),
        created_by_user_id=user.id,
        source=TransactionSource.SYSTEM,
        payload_fingerprint="a" * 64,
    )
    return user, wallet, transaction


def test_database_rejects_type_direction_mismatch(session) -> None:
    _, _, transaction = _transaction(
        session, direction=TransactionDirection.DEBIT
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(transaction)
            session.flush()


def test_database_rejects_duplicate_external_transaction_id(session) -> None:
    _, _, first = _transaction(session)
    session.add(first)
    session.flush()

    user, operator, player, wallet = _supporting_records(session)
    duplicate = Transaction(
        id=str(uuid.uuid4()),
        external_transaction_id=first.external_transaction_id,
        player_id=player.id,
        wallet_id=wallet.id,
        operator_id=operator.id,
        country="Georgia",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=2_000,
        currency="USD",
        status=TransactionStatus.PENDING,
        occurred_at=datetime.now(UTC),
        created_by_user_id=user.id,
        source=TransactionSource.SYSTEM,
        payload_fingerprint="b" * 64,
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(duplicate)
            session.flush()

    assert session.scalar(select(Transaction).where(Transaction.id == first.id)) is first


def test_database_rejects_invalid_posting_math(session) -> None:
    user, wallet, transaction = _transaction(session)
    session.add(transaction)
    session.flush()
    invalid_posting = BalancePosting(
        transaction_id=transaction.id,
        wallet_id=wallet.id,
        delta_minor=1_000,
        balance_before_minor=10_000,
        balance_after_minor=10_500,
        wallet_version=1,
        posted_by_user_id=user.id,
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(invalid_posting)
            session.flush()


def test_wallet_anchor_has_database_foreign_key(app) -> None:
    inspector = inspect(app.extensions["database_engine"])
    anchor_fks = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys("wallets")
        if foreign_key["constrained_columns"] == ["historical_anchor_transaction_id"]
    ]
    assert len(anchor_fks) == 1
    assert anchor_fks[0]["referred_table"] == "transactions"


def test_partial_unique_index_allows_failed_reversals_but_only_one_approved(
    session,
) -> None:
    user, operator, player, wallet = _supporting_records(session)
    original = Transaction(
        id=str(uuid.uuid4()),
        external_transaction_id=f"TX-{uuid.uuid4()}",
        player_id=player.id,
        wallet_id=wallet.id,
        operator_id=operator.id,
        country="Georgia",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=1_000,
        currency="USD",
        status=TransactionStatus.APPROVED,
        occurred_at=datetime.now(UTC),
        created_by_user_id=user.id,
        source=TransactionSource.SYSTEM,
        payload_fingerprint="c" * 64,
    )
    session.add(original)
    session.flush()

    reversals = []
    for suffix in ("1", "2"):
        reversal = Transaction(
            id=str(uuid.uuid4()),
            external_transaction_id=f"REV-{uuid.uuid4()}",
            player_id=player.id,
            wallet_id=wallet.id,
            operator_id=operator.id,
            country="Georgia",
            transaction_type=TransactionType.REVERSAL,
            direction=TransactionDirection.DEBIT,
            amount_minor=1_000,
            currency="USD",
            status=TransactionStatus.FAILED,
            occurred_at=datetime.now(UTC),
            created_by_user_id=user.id,
            source=TransactionSource.SYSTEM,
            payload_fingerprint=suffix * 64,
            reverses_transaction_id=original.id,
        )
        session.add(reversal)
        reversals.append(reversal)
    session.flush()  # Multiple failed attempts are allowed.

    reversals[0].status = TransactionStatus.APPROVED
    session.flush()
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            reversals[1].status = TransactionStatus.APPROVED
            session.flush()
