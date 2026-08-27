from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models import BalancePosting, Transaction, TransactionDirection, TransactionStatus, TransactionType, Wallet
from app.services.transaction_service import FinancialService, InvalidTransition
from tests.financial_helpers import command_for, create_and_approve, seed_financial_context


def test_cancel_pending_system_transaction(session_factory) -> None:
    context = seed_financial_context(session_factory)
    service = FinancialService(session_factory)
    created = service.create_transaction(command_for(context, "SYS-CANCEL"))

    cancelled = service.cancel_transaction(
        created.transaction.id, actor_user_id=context.user_id, reason="Entered twice"
    )
    retried = service.cancel_transaction(
        created.transaction.id, actor_user_id=context.user_id, reason="Entered twice"
    )

    assert cancelled.transaction.status == TransactionStatus.CANCELLED
    assert retried.idempotent is True
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0


def test_approved_transaction_cannot_be_cancelled(session_factory) -> None:
    context = seed_financial_context(session_factory)
    service = FinancialService(session_factory)
    transaction, _ = create_and_approve(
        service,
        context,
        "SYS-NO-CANCEL",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=1_000,
    )

    with pytest.raises(InvalidTransition):
        service.cancel_transaction(
            transaction.id, actor_user_id=context.user_id, reason="Too late"
        )


def test_failed_reversal_does_not_block_later_success(session_factory) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=0)
    service = FinancialService(session_factory)
    original, _ = create_and_approve(
        service,
        context,
        "SYS-ORIGINAL-DEPOSIT",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=10_000,
    )
    create_and_approve(
        service,
        context,
        "SYS-SPEND",
        transaction_type=TransactionType.WITHDRAWAL,
        direction=TransactionDirection.DEBIT,
        amount_minor=9_000,
    )
    failed_reversal = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-FAILED",
        actor_user_id=context.user_id,
        reason="Incorrect deposit",
        occurred_at=datetime(2026, 8, 21, 11, 0, tzinfo=UTC),
    )
    failed_result = service.approve_transaction(
        failed_reversal.transaction.id, actor_user_id=context.user_id
    )
    assert failed_result.transaction.status == TransactionStatus.FAILED
    assert failed_result.transaction.status_reason == "Insufficient funds"

    create_and_approve(
        service,
        context,
        "SYS-REPLENISH",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=10_000,
    )
    successful_reversal = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-SUCCESS",
        actor_user_id=context.user_id,
        reason="Retry after recovery",
        occurred_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    success_result = service.approve_transaction(
        successful_reversal.transaction.id, actor_user_id=context.user_id
    )

    assert success_result.transaction.status == TransactionStatus.APPROVED
    with session_factory() as session:
        assert session.get(Transaction, original.id).status == TransactionStatus.REVERSED
        wallet = session.get(Wallet, context.wallet_id)
        assert wallet.current_balance_minor == 1_000
        reversal_attempts = session.scalars(
            select(Transaction).where(Transaction.reverses_transaction_id == original.id)
        ).all()
        assert {item.status for item in reversal_attempts} == {
            TransactionStatus.FAILED,
            TransactionStatus.APPROVED,
        }


def test_second_pending_reversal_fails_after_first_is_approved(session_factory) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=0)
    service = FinancialService(session_factory)
    original, _ = create_and_approve(
        service,
        context,
        "SYS-ORIGINAL-2",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=5_000,
    )
    first = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-FIRST",
        actor_user_id=context.user_id,
        reason="First attempt",
    )
    second = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-SECOND",
        actor_user_id=context.user_id,
        reason="Second attempt",
    )

    first_result = service.approve_transaction(
        first.transaction.id, actor_user_id=context.user_id
    )
    second_result = service.approve_transaction(
        second.transaction.id, actor_user_id=context.user_id
    )

    assert first_result.transaction.status == TransactionStatus.APPROVED
    assert second_result.transaction.status == TransactionStatus.FAILED
    assert second_result.transaction.status_reason == "Original transaction is no longer approved"
    with session_factory() as session:
        approved_reversals = session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.reverses_transaction_id == original.id,
                Transaction.status == TransactionStatus.APPROVED,
            )
        )
        assert approved_reversals == 1


def test_reversal_creation_retry_is_idempotent_with_server_timestamp(
    session_factory,
) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=0)
    service = FinancialService(session_factory)
    original, _ = create_and_approve(
        service,
        context,
        "SYS-ORIGINAL-IDEMPOTENT-REV",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=1_000,
    )

    first = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-IDEMPOTENT",
        actor_user_id=context.user_id,
        reason="Correct original",
    )
    retry = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-IDEMPOTENT",
        actor_user_id=context.user_id,
        reason="Correct original",
    )

    assert first.created is True
    assert retry.idempotent is True
    assert retry.transaction.id == first.transaction.id


def test_reversal_audit_failure_rolls_back_wallet_posting_and_statuses(
    session_factory, monkeypatch
) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=0)
    service = FinancialService(session_factory)
    original, _ = create_and_approve(
        service,
        context,
        "SYS-ORIGINAL-ROLLBACK",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=2_000,
    )
    reversal = service.create_reversal(
        original.id,
        external_transaction_id="SYS-REV-ROLLBACK",
        actor_user_id=context.user_id,
        reason="Exercise rollback",
    )
    original_audit = service._audit

    def failing_audit(*args, **kwargs):
        if kwargs.get("action") == "transaction.reversed":
            raise RuntimeError("simulated reversal audit failure")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(service, "_audit", failing_audit)
    with pytest.raises(RuntimeError, match="simulated reversal audit failure"):
        service.approve_transaction(
            reversal.transaction.id, actor_user_id=context.user_id
        )

    with session_factory() as session:
        assert session.get(Transaction, original.id).status == TransactionStatus.APPROVED
        assert (
            session.get(Transaction, reversal.transaction.id).status
            == TransactionStatus.PENDING
        )
        assert session.get(Wallet, context.wallet_id).current_balance_minor == 2_000
        assert session.scalar(select(func.count()).select_from(BalancePosting)) == 1


def test_concurrent_reversal_attempts_allow_exactly_one_approval(session_factory) -> None:
    context = seed_financial_context(session_factory, starting_balance_minor=0)
    service = FinancialService(session_factory)
    original, _ = create_and_approve(
        service,
        context,
        "SYS-ORIGINAL-CONCURRENT",
        transaction_type=TransactionType.DEPOSIT,
        direction=TransactionDirection.CREDIT,
        amount_minor=5_000,
    )
    reversal_ids = [
        service.create_reversal(
            original.id,
            external_transaction_id=f"SYS-REV-CONCURRENT-{suffix}",
            actor_user_id=context.user_id,
            reason="Concurrent test",
        ).transaction.id
        for suffix in ("A", "B")
    ]
    barrier = Barrier(2)

    def approve(reversal_id: str) -> TransactionStatus:
        worker_service = FinancialService(session_factory)
        barrier.wait()
        return worker_service.approve_transaction(
            reversal_id, actor_user_id=context.user_id
        ).transaction.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(approve, reversal_ids))

    assert sorted(status.value for status in statuses) == ["APPROVED", "FAILED"]
    with session_factory() as session:
        assert session.get(Transaction, original.id).status == TransactionStatus.REVERSED
        assert session.get(Wallet, context.wallet_id).current_balance_minor == 0
        approved_reversals = session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.reverses_transaction_id == original.id,
                Transaction.status == TransactionStatus.APPROVED,
            )
        )
        assert approved_reversals == 1
