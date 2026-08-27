from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models import BalancePosting, Transaction, TransactionDirection, TransactionStatus, TransactionType, Wallet
from app.services.transaction_service import FinancialService
from tests.financial_helpers import command_for, seed_financial_context


def test_approval_posts_once_and_repeated_request_is_idempotent(session_factory) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=10_000)
    service = FinancialService(session_factory)
    created = service.create_transaction(
        command_for(
            context,
            "SYS-DEBIT-1",
            transaction_type=TransactionType.WITHDRAWAL,
            direction=TransactionDirection.DEBIT,
            amount_minor=8_000,
        )
    )

    approved = service.approve_transaction(
        created.transaction.id, actor_user_id=context.user_id
    )
    retried = service.approve_transaction(
        created.transaction.id, actor_user_id=context.user_id
    )

    assert approved.transaction.status == TransactionStatus.APPROVED
    assert approved.posting.balance_before_minor == 10_000
    assert approved.posting.delta_minor == -8_000
    assert approved.posting.balance_after_minor == 2_000
    assert retried.idempotent is True
    assert retried.posting.id == approved.posting.id
    with session_factory() as session:
        assert session.get(Wallet, context.wallet_id).current_balance_minor == 2_000
        assert session.scalar(select(func.count()).select_from(BalancePosting)) == 1


def test_insufficient_funds_transitions_pending_directly_to_failed(session_factory) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=10_000)
    service = FinancialService(session_factory)
    created = service.create_transaction(
        command_for(
            context,
            "SYS-TOO-LARGE",
            transaction_type=TransactionType.WITHDRAWAL,
            direction=TransactionDirection.DEBIT,
            amount_minor=12_000,
        )
    )

    failed = service.approve_transaction(
        created.transaction.id, actor_user_id=context.user_id
    )
    retried = service.approve_transaction(
        created.transaction.id, actor_user_id=context.user_id
    )

    assert failed.transaction.status == TransactionStatus.FAILED
    assert failed.transaction.status_reason == "Insufficient funds"
    assert failed.posting is None
    assert retried.idempotent is True
    with session_factory() as session:
        assert session.get(Wallet, context.wallet_id).current_balance_minor == 10_000
        assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0


def test_audit_failure_rolls_back_wallet_posting_and_status(
    session_factory, monkeypatch
) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=10_000)
    service = FinancialService(session_factory)
    created = service.create_transaction(
        command_for(
            context,
            "SYS-ROLLBACK",
            transaction_type=TransactionType.WITHDRAWAL,
            direction=TransactionDirection.DEBIT,
            amount_minor=2_000,
        )
    )
    original_audit = service._audit

    def failing_audit(*args, **kwargs):
        if kwargs.get("action") == "transaction.approved":
            raise RuntimeError("simulated audit failure")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(service, "_audit", failing_audit)
    with pytest.raises(RuntimeError, match="simulated audit failure"):
        service.approve_transaction(
            created.transaction.id, actor_user_id=context.user_id
        )

    with session_factory() as session:
        transaction = session.get(Transaction, created.transaction.id)
        wallet = session.get(Wallet, context.wallet_id)
        assert transaction.status == TransactionStatus.PENDING
        assert wallet.current_balance_minor == 10_000
        assert wallet.version == 0
        assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0


def test_two_concurrent_debits_cannot_spend_same_balance(session_factory) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=10_000)
    service = FinancialService(session_factory)
    debit_ids = []
    for suffix in ("A", "B"):
        created = service.create_transaction(
            command_for(
                context,
                f"SYS-CONCURRENT-{suffix}",
                transaction_type=TransactionType.WITHDRAWAL,
                direction=TransactionDirection.DEBIT,
                amount_minor=8_000,
            )
        )
        debit_ids.append(created.transaction.id)

    barrier = Barrier(2)

    def approve(transaction_id: str) -> TransactionStatus:
        worker_service = FinancialService(session_factory)
        barrier.wait()
        return worker_service.approve_transaction(
            transaction_id, actor_user_id=context.user_id
        ).transaction.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(approve, debit_ids))

    assert sorted(status.value for status in statuses) == ["APPROVED", "FAILED"]
    with session_factory() as session:
        wallet = session.get(Wallet, context.wallet_id)
        postings = session.scalars(select(BalancePosting)).all()
        assert wallet.current_balance_minor == 2_000
        assert len(postings) == 1
        assert postings[0].balance_before_minor == 10_000
        assert postings[0].delta_minor == -8_000
        assert postings[0].balance_after_minor == 2_000
