from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Iterator

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    BalancePosting,
    Operator,
    Player,
    Transaction,
    TransactionDirection,
    TransactionSource,
    TransactionStatus,
    TransactionType,
    User,
    Wallet,
)


SessionFactory = Callable[[], Session]

TYPE_DIRECTIONS = {
    TransactionType.DEPOSIT: TransactionDirection.CREDIT,
    TransactionType.WITHDRAWAL: TransactionDirection.DEBIT,
    TransactionType.GAME_ENTRY: TransactionDirection.DEBIT,
    TransactionType.GAME_WIN: TransactionDirection.CREDIT,
}


class FinancialServiceError(Exception):
    pass


class ValidationError(FinancialServiceError):
    pass


class RecordNotFound(FinancialServiceError):
    pass


class InvalidTransition(FinancialServiceError):
    pass


class IdempotencyConflict(FinancialServiceError):
    def __init__(self, external_transaction_id: str):
        super().__init__(
            f"External transaction ID {external_transaction_id!r} already exists "
            "with a different immutable payload"
        )
        self.external_transaction_id = external_transaction_id


@dataclass(frozen=True)
class CreateTransactionCommand:
    external_transaction_id: str
    player_external_id: str
    operator_code: str
    country: str
    transaction_type: TransactionType
    direction: TransactionDirection
    amount_minor: int
    currency: str
    occurred_at: datetime
    actor_user_id: int
    note: str | None = None


@dataclass(frozen=True)
class CreationResult:
    transaction: Transaction
    created: bool
    idempotent: bool


@dataclass(frozen=True)
class ProcessingResult:
    transaction: Transaction
    posting: BalancePosting | None
    idempotent: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationError("occurred_at must include a timezone")
    return value.astimezone(UTC)


def _fingerprint(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@contextmanager
def _write_transaction(session: Session) -> Iterator[None]:
    """Own one short write transaction, using SQLite's explicit writer lock."""
    if session.in_transaction():
        raise RuntimeError("Financial service requires a fresh database session")

    if session.get_bind().dialect.name == "sqlite":
        # SQLAlchemy's implicit transaction has not sent a SQLite BEGIN statement
        # yet, so this upgrades it to a writer lock before any business reads.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield
            session.commit()
        except BaseException:
            session.rollback()
            raise
        return

    with session.begin():
        yield


class FinancialService:
    def __init__(self, session_factory: SessionFactory):
        self._session_factory = session_factory

    def create_transaction(
        self, command: CreateTransactionCommand
    ) -> CreationResult:
        normalized = self._normalize_command(command)
        fingerprint = self._ordinary_fingerprint(normalized)
        conflict = False
        result: CreationResult | None = None

        with self._session_factory() as session:
            with _write_transaction(session):
                existing = session.scalar(
                    select(Transaction).where(
                        Transaction.external_transaction_id
                        == normalized.external_transaction_id
                    )
                )
                if existing is not None:
                    if existing.payload_fingerprint == fingerprint:
                        self._audit(
                            session,
                            action="transaction.idempotent_retry",
                            transaction=existing,
                            actor_user_id=normalized.actor_user_id,
                            reason="Same external transaction ID and immutable payload",
                        )
                        result = CreationResult(existing, created=False, idempotent=True)
                    else:
                        self._audit(
                            session,
                            action="transaction.idempotency_conflict",
                            transaction=existing,
                            actor_user_id=normalized.actor_user_id,
                            reason="External transaction ID reused with different payload",
                            after_values={"incoming_fingerprint": fingerprint},
                        )
                        conflict = True
                else:
                    player, operator, wallet = self._validate_creation_references(
                        session, normalized
                    )
                    transaction = Transaction(
                        id=str(uuid.uuid4()),
                        external_transaction_id=normalized.external_transaction_id,
                        player_id=player.id,
                        wallet_id=wallet.id,
                        operator_id=operator.id,
                        country=normalized.country,
                        transaction_type=normalized.transaction_type,
                        direction=normalized.direction,
                        amount_minor=normalized.amount_minor,
                        currency=normalized.currency,
                        status=TransactionStatus.PENDING,
                        occurred_at=normalized.occurred_at,
                        created_by_user_id=normalized.actor_user_id,
                        source=TransactionSource.SYSTEM,
                        payload_fingerprint=fingerprint,
                        note=normalized.note,
                    )
                    try:
                        with session.begin_nested():
                            session.add(transaction)
                            session.flush()
                    except IntegrityError:
                        # The savepoint keeps PostgreSQL's surrounding transaction
                        # usable after a concurrent unique-ID conflict.
                        existing = session.scalar(
                            select(Transaction).where(
                                Transaction.external_transaction_id
                                == normalized.external_transaction_id
                            )
                        )
                        if existing is None:
                            raise
                        if existing.payload_fingerprint == fingerprint:
                            self._audit(
                                session,
                                action="transaction.idempotent_retry",
                                transaction=existing,
                                actor_user_id=normalized.actor_user_id,
                                reason="Concurrent idempotent retry",
                            )
                            result = CreationResult(
                                existing, created=False, idempotent=True
                            )
                        else:
                            self._audit(
                                session,
                                action="transaction.idempotency_conflict",
                                transaction=existing,
                                actor_user_id=normalized.actor_user_id,
                                reason="Concurrent ID reuse with different payload",
                                after_values={"incoming_fingerprint": fingerprint},
                            )
                            conflict = True
                    else:
                        self._audit(
                            session,
                            action="transaction.created",
                            transaction=transaction,
                            actor_user_id=normalized.actor_user_id,
                            after_values=self._transaction_audit_values(transaction),
                        )
                        result = CreationResult(
                            transaction, created=True, idempotent=False
                        )

        if conflict:
            raise IdempotencyConflict(normalized.external_transaction_id)
        if result is None:  # Defensive guard for an impossible code path.
            raise RuntimeError("Transaction creation produced no result")
        return result

    def approve_transaction(
        self, transaction_id: str, *, actor_user_id: int
    ) -> ProcessingResult:
        result: ProcessingResult | None = None
        with self._session_factory() as session:
            with _write_transaction(session):
                actor = self._validate_actor(session, actor_user_id)
                transaction = self._lock_transaction(session, transaction_id)
                if transaction.source != TransactionSource.SYSTEM:
                    raise InvalidTransition("Historical transactions cannot be processed")

                if transaction.status == TransactionStatus.APPROVED:
                    posting = session.scalar(
                        select(BalancePosting).where(
                            BalancePosting.transaction_id == transaction.id
                        )
                    )
                    if posting is None:
                        raise InvalidTransition(
                            "Approved system transaction is missing its posting"
                        )
                    result = ProcessingResult(
                        transaction, posting=posting, idempotent=True
                    )
                elif transaction.status == TransactionStatus.FAILED:
                    result = ProcessingResult(
                        transaction, posting=None, idempotent=True
                    )
                elif transaction.status != TransactionStatus.PENDING:
                    raise InvalidTransition(
                        f"Cannot approve a {transaction.status.value} transaction"
                    )
                else:
                    original: Transaction | None = None
                    if transaction.transaction_type == TransactionType.REVERSAL:
                        original = self._lock_reversal_original(session, transaction)
                        if original.status != TransactionStatus.APPROVED:
                            transaction.status = TransactionStatus.FAILED
                            transaction.status_reason = (
                                "Original transaction is no longer approved"
                            )
                            self._audit(
                                session,
                                action="transaction.approval_failed",
                                transaction=transaction,
                                actor_user_id=actor.id,
                                reason=transaction.status_reason,
                            )
                            result = ProcessingResult(
                                transaction, posting=None, idempotent=False
                            )

                    if result is None:
                        delta = (
                            transaction.amount_minor
                            if transaction.direction == TransactionDirection.CREDIT
                            else -transaction.amount_minor
                        )
                        posting = self._apply_balance_update(
                            session, transaction, delta, actor.id
                        )
                        if posting is None:
                            wallet = session.get(Wallet, transaction.wallet_id)
                            reason = (
                                "Wallet balance is not initialized"
                                if wallet is not None and not wallet.balance_initialized
                                else "Insufficient funds"
                            )
                            transaction.status = TransactionStatus.FAILED
                            transaction.status_reason = reason
                            self._audit(
                                session,
                                action="transaction.approval_failed",
                                transaction=transaction,
                                actor_user_id=actor.id,
                                reason=reason,
                            )
                            result = ProcessingResult(
                                transaction, posting=None, idempotent=False
                            )
                        else:
                            before_status = transaction.status
                            if original is not None:
                                original.status = TransactionStatus.REVERSED
                                original.status_reason = (
                                    f"Reversed by {transaction.external_transaction_id}"
                                )
                                self._audit(
                                    session,
                                    action="transaction.reversed",
                                    transaction=original,
                                    actor_user_id=actor.id,
                                    before_values={"status": TransactionStatus.APPROVED.value},
                                    after_values={
                                        "status": TransactionStatus.REVERSED.value,
                                        "reversal_transaction_id": transaction.id,
                                    },
                                )
                            transaction.status = TransactionStatus.APPROVED
                            transaction.status_reason = None
                            transaction.approved_at = _now()
                            self._audit(
                                session,
                                action="transaction.approved",
                                transaction=transaction,
                                actor_user_id=actor.id,
                                before_values={"status": before_status.value},
                                after_values={
                                    "status": TransactionStatus.APPROVED.value,
                                    "balance_after_minor": posting.balance_after_minor,
                                    "wallet_version": posting.wallet_version,
                                },
                            )
                            # Flush all status, posting, and audit changes before commit.
                            # Any constraint or persistence failure rolls back the wallet.
                            session.flush()
                            result = ProcessingResult(
                                transaction, posting=posting, idempotent=False
                            )

        if result is None:
            raise RuntimeError("Approval produced no result")
        return result

    def cancel_transaction(
        self, transaction_id: str, *, actor_user_id: int, reason: str
    ) -> ProcessingResult:
        if not reason.strip():
            raise ValidationError("Cancellation reason is required")
        result: ProcessingResult | None = None
        with self._session_factory() as session:
            with _write_transaction(session):
                self._validate_actor(session, actor_user_id)
                transaction = self._lock_transaction(session, transaction_id)
                if transaction.source != TransactionSource.SYSTEM:
                    raise InvalidTransition("Historical transactions cannot be cancelled")
                if transaction.status == TransactionStatus.CANCELLED:
                    result = ProcessingResult(
                        transaction, posting=None, idempotent=True
                    )
                elif transaction.status != TransactionStatus.PENDING:
                    raise InvalidTransition(
                        f"Cannot cancel a {transaction.status.value} transaction"
                    )
                else:
                    transaction.status = TransactionStatus.CANCELLED
                    transaction.status_reason = reason.strip()
                    self._audit(
                        session,
                        action="transaction.cancelled",
                        transaction=transaction,
                        actor_user_id=actor_user_id,
                        before_values={"status": TransactionStatus.PENDING.value},
                        after_values={
                            "status": TransactionStatus.CANCELLED.value,
                            "reason": transaction.status_reason,
                        },
                    )
                    result = ProcessingResult(
                        transaction, posting=None, idempotent=False
                    )
        if result is None:
            raise RuntimeError("Cancellation produced no result")
        return result

    def create_reversal(
        self,
        original_transaction_id: str,
        *,
        external_transaction_id: str,
        actor_user_id: int,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> CreationResult:
        external_id = external_transaction_id.strip()
        if not external_id:
            raise ValidationError("external_transaction_id is required")
        if not reason.strip():
            raise ValidationError("Reversal reason is required")
        occurred = _normalized_utc(occurred_at or _now())
        conflict = False
        result: CreationResult | None = None

        with self._session_factory() as session:
            with _write_transaction(session):
                actor = self._validate_actor(session, actor_user_id)
                original = self._lock_transaction(session, original_transaction_id)
                if original.source != TransactionSource.SYSTEM:
                    raise InvalidTransition("Only system transactions may be reversed")
                if original.transaction_type == TransactionType.REVERSAL:
                    raise InvalidTransition("A reversal transaction cannot be reversed")

                direction = (
                    TransactionDirection.DEBIT
                    if original.direction == TransactionDirection.CREDIT
                    else TransactionDirection.CREDIT
                )
                fingerprint = self._reversal_fingerprint(
                    external_id, original, direction
                )
                existing = session.scalar(
                    select(Transaction).where(
                        Transaction.external_transaction_id == external_id
                    )
                )
                if existing is not None:
                    if existing.payload_fingerprint == fingerprint:
                        self._audit(
                            session,
                            action="transaction.idempotent_retry",
                            transaction=existing,
                            actor_user_id=actor.id,
                            reason="Same reversal ID and immutable payload",
                        )
                        result = CreationResult(existing, False, True)
                    else:
                        self._audit(
                            session,
                            action="transaction.idempotency_conflict",
                            transaction=existing,
                            actor_user_id=actor.id,
                            reason="Reversal ID reused with different payload",
                            after_values={"incoming_fingerprint": fingerprint},
                        )
                        conflict = True
                elif original.status != TransactionStatus.APPROVED:
                    raise InvalidTransition("Only an approved transaction may be reversed")
                else:
                    reversal = Transaction(
                        id=str(uuid.uuid4()),
                        external_transaction_id=external_id,
                        player_id=original.player_id,
                        wallet_id=original.wallet_id,
                        operator_id=original.operator_id,
                        country=original.country,
                        transaction_type=TransactionType.REVERSAL,
                        direction=direction,
                        amount_minor=original.amount_minor,
                        currency=original.currency,
                        status=TransactionStatus.PENDING,
                        occurred_at=occurred,
                        created_by_user_id=actor.id,
                        source=TransactionSource.SYSTEM,
                        payload_fingerprint=fingerprint,
                        reverses_transaction_id=original.id,
                        note=reason.strip(),
                    )
                    try:
                        with session.begin_nested():
                            session.add(reversal)
                            session.flush()
                    except IntegrityError:
                        existing = session.scalar(
                            select(Transaction).where(
                                Transaction.external_transaction_id == external_id
                            )
                        )
                        if existing is None:
                            raise
                        if existing.payload_fingerprint == fingerprint:
                            result = CreationResult(existing, False, True)
                        else:
                            self._audit(
                                session,
                                action="transaction.idempotency_conflict",
                                transaction=existing,
                                actor_user_id=actor.id,
                                reason="Concurrent reversal ID conflict",
                            )
                            conflict = True
                    else:
                        self._audit(
                            session,
                            action="transaction.reversal_created",
                            transaction=reversal,
                            actor_user_id=actor.id,
                            after_values={
                                "original_transaction_id": original.id,
                                "reason": reason.strip(),
                            },
                        )
                        result = CreationResult(reversal, True, False)

        if conflict:
            raise IdempotencyConflict(external_id)
        if result is None:
            raise RuntimeError("Reversal creation produced no result")
        return result

    def _normalize_command(
        self, command: CreateTransactionCommand
    ) -> CreateTransactionCommand:
        external_id = command.external_transaction_id.strip()
        player_id = command.player_external_id.strip()
        operator_code = command.operator_code.strip().upper()
        country = command.country.strip()
        currency = command.currency.strip().upper()
        if not external_id or len(external_id) > 150:
            raise ValidationError("external_transaction_id is required and at most 150 characters")
        if not player_id:
            raise ValidationError("player_external_id is required")
        if not operator_code:
            raise ValidationError("operator_code is required")
        if not country:
            raise ValidationError("country is required")
        if len(currency) != 3 or not currency.isalpha():
            raise ValidationError("currency must be a three-letter code")
        if isinstance(command.amount_minor, bool) or not isinstance(
            command.amount_minor, int
        ):
            raise ValidationError("amount_minor must be an integer")
        if command.amount_minor <= 0:
            raise ValidationError("amount_minor must be greater than zero")
        if command.transaction_type == TransactionType.REVERSAL:
            raise ValidationError("Use create_reversal for reversal transactions")
        expected_direction = TYPE_DIRECTIONS.get(command.transaction_type)
        if expected_direction is None:
            raise ValidationError("Unsupported transaction type")
        if command.direction != expected_direction:
            raise ValidationError("direction conflicts with transaction type")
        return CreateTransactionCommand(
            external_transaction_id=external_id,
            player_external_id=player_id,
            operator_code=operator_code,
            country=country,
            transaction_type=command.transaction_type,
            direction=command.direction,
            amount_minor=command.amount_minor,
            currency=currency,
            occurred_at=_normalized_utc(command.occurred_at),
            actor_user_id=command.actor_user_id,
            note=command.note.strip() if command.note and command.note.strip() else None,
        )

    def _validate_creation_references(
        self, session: Session, command: CreateTransactionCommand
    ) -> tuple[Player, Operator, Wallet]:
        self._validate_actor(session, command.actor_user_id)
        player = session.scalar(
            select(Player).where(
                Player.external_player_id == command.player_external_id
            )
        )
        if player is None:
            raise RecordNotFound("Player was not found")
        operator = session.scalar(
            select(Operator).where(Operator.code == command.operator_code)
        )
        if operator is None or not operator.is_active:
            raise RecordNotFound("Active operator was not found")
        wallet = session.scalar(
            select(Wallet).where(
                Wallet.player_id == player.id, Wallet.currency == command.currency
            )
        )
        if wallet is None:
            raise RecordNotFound("Player wallet was not found")
        if not wallet.balance_initialized:
            raise ValidationError("Wallet balance is not initialized")
        return player, operator, wallet

    def _validate_actor(self, session: Session, actor_user_id: int) -> User:
        actor = session.get(User, actor_user_id)
        if actor is None or not actor.is_active:
            raise RecordNotFound("Active actor user was not found")
        return actor

    def _lock_transaction(self, session: Session, transaction_id: str) -> Transaction:
        statement = select(Transaction).where(Transaction.id == transaction_id)
        if session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        transaction = session.scalar(statement)
        if transaction is None:
            raise RecordNotFound("Transaction was not found")
        return transaction

    def _lock_reversal_original(
        self, session: Session, reversal: Transaction
    ) -> Transaction:
        if reversal.reverses_transaction_id is None:
            raise InvalidTransition("Reversal has no original transaction")
        original = self._lock_transaction(session, reversal.reverses_transaction_id)
        if original.source != TransactionSource.SYSTEM:
            raise InvalidTransition("Only system transactions may be reversed")
        if original.transaction_type == TransactionType.REVERSAL:
            raise InvalidTransition("A reversal transaction cannot be reversed")
        if original.wallet_id != reversal.wallet_id:
            raise InvalidTransition("Reversal wallet differs from original")
        if original.amount_minor != reversal.amount_minor:
            raise InvalidTransition("Reversal amount differs from original")
        expected_direction = (
            TransactionDirection.DEBIT
            if original.direction == TransactionDirection.CREDIT
            else TransactionDirection.CREDIT
        )
        if reversal.direction != expected_direction:
            raise InvalidTransition("Reversal direction is not opposite to original")
        return original

    def _apply_balance_update(
        self,
        session: Session,
        transaction: Transaction,
        delta_minor: int,
        actor_user_id: int,
    ) -> BalancePosting | None:
        statement = (
            update(Wallet)
            .where(
                Wallet.id == transaction.wallet_id,
                Wallet.balance_initialized.is_(True),
                Wallet.current_balance_minor + delta_minor >= 0,
            )
            .values(
                current_balance_minor=Wallet.current_balance_minor + delta_minor,
                version=Wallet.version + 1,
                updated_at=_now(),
            )
            .returning(Wallet.current_balance_minor, Wallet.version)
        )
        row = session.execute(statement).one_or_none()
        if row is None:
            return None
        balance_after, version = row
        posting = BalancePosting(
            transaction_id=transaction.id,
            wallet_id=transaction.wallet_id,
            delta_minor=delta_minor,
            balance_before_minor=balance_after - delta_minor,
            balance_after_minor=balance_after,
            wallet_version=version,
            posted_by_user_id=actor_user_id,
        )
        session.add(posting)
        session.flush()
        return posting

    def _ordinary_fingerprint(self, command: CreateTransactionCommand) -> str:
        return _fingerprint(
            {
                "external_player_id": command.player_external_id,
                "operator_code": command.operator_code,
                "country": command.country,
                "transaction_type": command.transaction_type.value,
                "direction": command.direction.value,
                "amount_minor": command.amount_minor,
                "currency": command.currency,
                "occurred_at": command.occurred_at.isoformat(),
                "source": TransactionSource.SYSTEM.value,
                "reverses_transaction_id": None,
            }
        )

    def _reversal_fingerprint(
        self,
        external_transaction_id: str,
        original: Transaction,
        direction: TransactionDirection,
    ) -> str:
        return _fingerprint(
            {
                "external_transaction_id": external_transaction_id,
                "player_id": original.player_id,
                "operator_id": original.operator_id,
                "country": original.country,
                "transaction_type": TransactionType.REVERSAL.value,
                "direction": direction.value,
                "amount_minor": original.amount_minor,
                "currency": original.currency,
                "source": TransactionSource.SYSTEM.value,
                "reverses_transaction_id": original.id,
            }
        )

    def _audit(
        self,
        session: Session,
        *,
        action: str,
        transaction: Transaction,
        actor_user_id: int | None = None,
        reason: str | None = None,
        before_values: dict[str, object] | None = None,
        after_values: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                actor_user_id=(
                    actor_user_id
                    if actor_user_id is not None
                    else transaction.created_by_user_id
                ),
                action=action,
                entity_type="transaction",
                entity_id=transaction.id,
                reason=reason,
                before_values=before_values,
                after_values=after_values,
            )
        )

    @staticmethod
    def _transaction_audit_values(transaction: Transaction) -> dict[str, object]:
        return {
            "external_transaction_id": transaction.external_transaction_id,
            "status": transaction.status.value,
            "transaction_type": transaction.transaction_type.value,
            "direction": transaction.direction.value,
            "amount_minor": transaction.amount_minor,
            "currency": transaction.currency,
        }
