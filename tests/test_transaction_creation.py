from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, BalancePosting, Transaction, TransactionSource, TransactionStatus
from app.services.transaction_service import (
    FinancialService,
    IdempotencyConflict,
    ValidationError,
)
from tests.financial_helpers import command_for, seed_financial_context


def test_create_system_transaction_in_pending_status(session_factory) -> None:
    context = seed_financial_context(session_factory)
    service = FinancialService(session_factory)

    result = service.create_transaction(command_for(context, "SYS-CREATE-1"))

    assert result.created is True
    assert result.idempotent is False
    assert result.transaction.status == TransactionStatus.PENDING
    assert result.transaction.source == TransactionSource.SYSTEM
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Transaction)) == 1
        assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0
        assert session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "transaction.created")
        ) == 1


def test_creation_validates_type_direction(session_factory) -> None:
    context = seed_financial_context(session_factory)
    service = FinancialService(session_factory)
    command = command_for(context, "SYS-BAD-DIRECTION")
    command = replace(command, direction=command.direction.DEBIT)

    with pytest.raises(ValidationError, match="direction conflicts"):
        service.create_transaction(command)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Transaction)) == 0


def test_creation_idempotency_and_conflict_audit(session_factory) -> None:
    context = seed_financial_context(session_factory)
    service = FinancialService(session_factory)
    command = command_for(context, "SYS-IDEMPOTENT")

    created = service.create_transaction(command)
    retried = service.create_transaction(command)

    assert retried.idempotent is True
    assert retried.transaction.id == created.transaction.id

    with pytest.raises(IdempotencyConflict):
        service.create_transaction(replace(command, amount_minor=2_000))

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Transaction)) == 1
        assert session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "transaction.idempotency_conflict")
        ) == 1


def test_concurrent_same_payload_creation_is_idempotent(session_factory) -> None:
    context = seed_financial_context(session_factory)
    command = command_for(context, "SYS-CONCURRENT-IDEMPOTENT")
    barrier = Barrier(2)

    def create():
        barrier.wait()
        return FinancialService(session_factory).create_transaction(command)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))

    assert sum(result.created for result in results) == 1
    assert sum(result.idempotent for result in results) == 1
    assert len({result.transaction.id for result in results}) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Transaction)) == 1
