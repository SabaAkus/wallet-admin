from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import click
from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.extensions import get_session
from app.models import (
    AuditEvent,
    Operator,
    Player,
    Transaction,
    TransactionDirection,
    TransactionSource,
    TransactionStatus,
    TransactionType,
    Wallet,
)


REQUIRED_COLUMNS = {
    "transaction_id",
    "player_id",
    "country",
    "operator",
    "type",
    "amount",
    "currency",
    "status",
    "balance_after",
    "date_time",
}

COLUMN_ALIASES = {
    "transactionid": "transaction_id",
    "playerid": "player_id",
    "balanceafter": "balance_after",
    "datetime": "date_time",
    "date": "date_time",
    "timestamp": "date_time",
    "transaction_type": "type",
    "reverses_transaction": "reverses_transaction_id",
    "reverses_external_transaction_id": "reverses_transaction_id",
}

TYPE_DIRECTIONS = {
    TransactionType.DEPOSIT: TransactionDirection.CREDIT,
    TransactionType.WITHDRAWAL: TransactionDirection.DEBIT,
    TransactionType.GAME_ENTRY: TransactionDirection.DEBIT,
    TransactionType.GAME_WIN: TransactionDirection.CREDIT,
}


@dataclass(frozen=True)
class ImportIssue:
    row_number: int | None
    external_transaction_id: str | None
    message: str


@dataclass(frozen=True)
class WalletAnchorResult:
    player_id: str
    currency: str
    external_transaction_id: str | None
    balance_minor: int | None
    ambiguous: bool


@dataclass
class ImportReport:
    source_path: str
    rows_seen: int = 0
    imported_transactions: int = 0
    idempotent_duplicates: int = 0
    conflicting_duplicates: int = 0
    operators_created: int = 0
    players_created: int = 0
    wallets_created: int = 0
    rejected_rows: list[ImportIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    wallet_anchors: list[WalletAnchorResult] = field(default_factory=list)

    @property
    def ambiguous_wallets(self) -> int:
        return sum(anchor.ambiguous for anchor in self.wallet_anchors)

    def warn_once(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["ambiguous_wallets"] = self.ambiguous_wallets
        return result


@dataclass(frozen=True)
class HistoricalRow:
    row_number: int
    external_transaction_id: str
    external_player_id: str
    country: str
    operator_code: str
    operator_name: str
    transaction_type: TransactionType
    direction: TransactionDirection
    amount_minor: int
    currency: str
    status: TransactionStatus
    source_balance_after_minor: int
    occurred_at: datetime
    reverses_external_transaction_id: str | None
    payload_fingerprint: str


class RowValidationError(ValueError):
    pass


def _normalize_label(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return COLUMN_ALIASES.get(normalized, normalized)


def _required_text(row: dict[str, str], name: str) -> str:
    value = (row.get(name) or "").strip()
    if not value:
        raise RowValidationError(f"{name} is required")
    return value


def _parse_money(value: str, field_name: str) -> int:
    cleaned = value.strip().replace(",", "")
    try:
        decimal_value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise RowValidationError(f"{field_name} is not a valid decimal amount") from exc

    minor_value = decimal_value * 100
    if minor_value != minor_value.to_integral_value():
        raise RowValidationError(f"{field_name} has more than two decimal places")
    return int(minor_value)


def _parse_datetime(value: str) -> tuple[datetime, bool]:
    candidate = value.strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        for date_format in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
        ):
            try:
                parsed = datetime.strptime(candidate, date_format)
                break
            except ValueError:
                continue
    if parsed is None:
        raise RowValidationError("date_time is not a supported timestamp")

    assumed_utc = parsed.tzinfo is None
    if assumed_utc:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed, assumed_utc


def _parse_enum(enum_type: type[Any], raw_value: str, field_name: str) -> Any:
    normalized = _normalize_label(raw_value).upper()
    try:
        return enum_type(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise RowValidationError(f"{field_name} must be one of: {allowed}") from exc


def _fingerprint(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _parse_row(
    source: dict[str, str], row_number: int, report: ImportReport
) -> HistoricalRow:
    external_transaction_id = _required_text(source, "transaction_id")
    external_player_id = _required_text(source, "player_id")
    country = _required_text(source, "country")
    operator_name = _required_text(source, "operator")
    operator_code = operator_name.upper()
    currency = _required_text(source, "currency").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise RowValidationError("currency must be a three-letter code")

    transaction_type = _parse_enum(
        TransactionType, _required_text(source, "type"), "type"
    )
    status = _parse_enum(TransactionStatus, _required_text(source, "status"), "status")
    amount_minor = _parse_money(_required_text(source, "amount"), "amount")
    if amount_minor <= 0:
        raise RowValidationError("amount must be greater than zero")
    balance_after_minor = _parse_money(
        _required_text(source, "balance_after"), "balance_after"
    )
    occurred_at, assumed_utc = _parse_datetime(_required_text(source, "date_time"))
    if assumed_utc:
        report.warn_once("Naive source timestamps were interpreted as UTC.")

    supplied_direction = (source.get("direction") or "").strip()
    reverses_external_id = (source.get("reverses_transaction_id") or "").strip() or None
    if transaction_type == TransactionType.REVERSAL:
        if not supplied_direction:
            raise RowValidationError("historical REVERSAL requires an explicit direction")
        if not reverses_external_id:
            raise RowValidationError(
                "historical REVERSAL requires reverses_transaction_id"
            )
        direction = _parse_enum(
            TransactionDirection, supplied_direction, "direction"
        )
    else:
        direction = TYPE_DIRECTIONS[transaction_type]
        if supplied_direction:
            parsed_direction = _parse_enum(
                TransactionDirection, supplied_direction, "direction"
            )
            if parsed_direction != direction:
                raise RowValidationError("direction conflicts with transaction type")

    fingerprint_payload = {
        "external_player_id": external_player_id,
        "country": country,
        "operator_code": operator_code,
        "transaction_type": transaction_type.value,
        "direction": direction.value,
        "amount_minor": amount_minor,
        "currency": currency,
        "status": status.value,
        "source_balance_after_minor": balance_after_minor,
        "occurred_at": occurred_at.isoformat(),
        "reverses_external_transaction_id": reverses_external_id,
        "source": TransactionSource.HISTORICAL_IMPORT.value,
    }
    return HistoricalRow(
        row_number=row_number,
        external_transaction_id=external_transaction_id,
        external_player_id=external_player_id,
        country=country,
        operator_code=operator_code,
        operator_name=operator_name,
        transaction_type=transaction_type,
        direction=direction,
        amount_minor=amount_minor,
        currency=currency,
        status=status,
        source_balance_after_minor=balance_after_minor,
        occurred_at=occurred_at,
        reverses_external_transaction_id=reverses_external_id,
        payload_fingerprint=_fingerprint(fingerprint_payload),
    )


class HistoricalImporter:
    def __init__(self, session: Session):
        self.session = session

    def import_csv(self, source_path: Path) -> ImportReport:
        report = ImportReport(source_path=str(source_path.resolve()))
        report.warn_once(
            "Money values are interpreted as major currency units with two decimal places."
        )

        with source_path.open("r", encoding="utf-8-sig", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None:
                raise ValueError("Historical CSV has no header row")
            normalized_headers = [_normalize_label(header) for header in reader.fieldnames]
            missing = sorted(REQUIRED_COLUMNS - set(normalized_headers))
            if missing:
                raise ValueError(f"Historical CSV is missing columns: {', '.join(missing)}")

            touched_wallet_ids: set[int] = set()
            with self.session.begin():
                for row_number, raw_row in enumerate(reader, start=2):
                    report.rows_seen += 1
                    normalized_row = {
                        _normalize_label(key): value for key, value in raw_row.items() if key
                    }
                    external_id = (normalized_row.get("transaction_id") or "").strip() or None
                    try:
                        parsed = _parse_row(normalized_row, row_number, report)
                    except RowValidationError as exc:
                        report.rejected_rows.append(
                            ImportIssue(row_number, external_id, str(exc))
                        )
                        self._audit(
                            action="historical_import.row_rejected",
                            entity_id=external_id or f"row:{row_number}",
                            reason=str(exc),
                            after_values={"row_number": row_number},
                        )
                        continue

                    try:
                        # A row-level savepoint prevents rejected relationship or
                        # reversal validation from leaving empty support records.
                        with self.session.begin_nested():
                            wallet_id = self._insert_row(parsed, report)
                    except RowValidationError as exc:
                        report.rejected_rows.append(
                            ImportIssue(row_number, external_id, str(exc))
                        )
                        self._audit(
                            action="historical_import.row_rejected",
                            entity_id=external_id or f"row:{row_number}",
                            reason=str(exc),
                            after_values={"row_number": row_number},
                        )
                        continue

                    if wallet_id is not None:
                        touched_wallet_ids.add(wallet_id)

                self._refresh_wallet_anchors(touched_wallet_ids, report)
                self._refresh_player_countries(report)
        return report

    def _insert_row(self, row: HistoricalRow, report: ImportReport) -> int | None:
        existing = self.session.scalar(
            select(Transaction).where(
                Transaction.external_transaction_id == row.external_transaction_id
            )
        )
        if existing is not None:
            return self._handle_existing(existing, row, report)

        operator = self.session.scalar(
            select(Operator).where(Operator.code == row.operator_code)
        )
        if operator is None:
            operator = Operator(code=row.operator_code, name=row.operator_name)
            self.session.add(operator)
            self.session.flush()
            report.operators_created += 1

        player = self.session.scalar(
            select(Player).where(Player.external_player_id == row.external_player_id)
        )
        if player is None:
            player = Player(external_player_id=row.external_player_id)
            self.session.add(player)
            self.session.flush()
            report.players_created += 1

        wallet = self.session.scalar(
            select(Wallet).where(
                Wallet.player_id == player.id, Wallet.currency == row.currency
            )
        )
        if wallet is None:
            wallet = Wallet(
                player_id=player.id,
                currency=row.currency,
                current_balance_minor=0,
                balance_initialized=False,
                version=0,
            )
            self.session.add(wallet)
            self.session.flush()
            report.wallets_created += 1

        reverses_id: str | None = None
        if row.reverses_external_transaction_id:
            original = self.session.scalar(
                select(Transaction).where(
                    Transaction.external_transaction_id
                    == row.reverses_external_transaction_id
                )
            )
            if original is None:
                raise RowValidationError("referenced reversal transaction was not found")
            if original.wallet_id != wallet.id:
                raise RowValidationError("reversal and original use different wallets")
            expected_direction = (
                TransactionDirection.DEBIT
                if original.direction == TransactionDirection.CREDIT
                else TransactionDirection.CREDIT
            )
            if row.direction != expected_direction:
                raise RowValidationError("reversal direction is not opposite to original")
            reverses_id = original.id

        transaction = Transaction(
            id=str(uuid.uuid4()),
            external_transaction_id=row.external_transaction_id,
            player_id=player.id,
            wallet_id=wallet.id,
            operator_id=operator.id,
            country=row.country,
            transaction_type=row.transaction_type,
            direction=row.direction,
            amount_minor=row.amount_minor,
            currency=row.currency,
            status=row.status,
            occurred_at=row.occurred_at,
            # The source has an occurrence timestamp, not a separate approval
            # timestamp, so historical approved_at remains unknown.
            approved_at=None,
            source=TransactionSource.HISTORICAL_IMPORT,
            source_balance_after_minor=row.source_balance_after_minor,
            payload_fingerprint=row.payload_fingerprint,
            reverses_transaction_id=reverses_id,
        )

        # The savepoint is required for PostgreSQL safety: a concurrent unique-ID
        # conflict rolls back only this insert, not the surrounding import.
        try:
            with self.session.begin_nested():
                self.session.add(transaction)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(Transaction).where(
                    Transaction.external_transaction_id == row.external_transaction_id
                )
            )
            if existing is None:
                raise RowValidationError("row violates a database integrity constraint")
            return self._handle_existing(existing, row, report)

        report.imported_transactions += 1
        self._audit(
            action="historical_import.transaction_created",
            entity_id=transaction.id,
            after_values={
                "external_transaction_id": transaction.external_transaction_id,
                "source_balance_after_minor": transaction.source_balance_after_minor,
            },
        )
        return wallet.id

    def _handle_existing(
        self, existing: Transaction, row: HistoricalRow, report: ImportReport
    ) -> int | None:
        if existing.payload_fingerprint == row.payload_fingerprint:
            report.idempotent_duplicates += 1
            self._audit(
                action="historical_import.idempotent_retry",
                entity_id=existing.id,
                reason="Same external transaction ID and immutable payload",
            )
            return existing.wallet_id

        report.conflicting_duplicates += 1
        message = "external transaction ID already exists with a different payload"
        report.rejected_rows.append(
            ImportIssue(row.row_number, row.external_transaction_id, message)
        )
        self._audit(
            action="historical_import.duplicate_conflict",
            entity_id=existing.id,
            reason=message,
            after_values={"incoming_fingerprint": row.payload_fingerprint},
        )
        return existing.wallet_id

    def _refresh_wallet_anchors(
        self, wallet_ids: Iterable[int], report: ImportReport
    ) -> None:
        for wallet_id in sorted(set(wallet_ids)):
            wallet = self.session.get(Wallet, wallet_id)
            if wallet is None:
                continue
            player = self.session.get(Player, wallet.player_id)
            candidates = list(
                self.session.scalars(
                    select(Transaction)
                    .where(
                        Transaction.wallet_id == wallet.id,
                        Transaction.source == TransactionSource.HISTORICAL_IMPORT,
                        Transaction.source_balance_after_minor.is_not(None),
                    )
                    .order_by(Transaction.occurred_at.desc())
                )
            )
            if not candidates:
                wallet.balance_initialized = False
                wallet.historical_anchor_transaction_id = None
                report.wallet_anchors.append(
                    WalletAnchorResult(
                        player_id=player.external_player_id,
                        currency=wallet.currency,
                        external_transaction_id=None,
                        balance_minor=None,
                        ambiguous=True,
                    )
                )
                continue

            latest_time = candidates[0].occurred_at
            latest = [item for item in candidates if item.occurred_at == latest_time]
            balances = {item.source_balance_after_minor for item in latest}
            if len(balances) != 1:
                wallet.current_balance_minor = 0
                wallet.balance_initialized = False
                wallet.historical_anchor_transaction_id = None
                warning = (
                    f"Wallet {player.external_player_id}/{wallet.currency} has conflicting "
                    f"balance-after values at its latest timestamp; no anchor was selected."
                )
                report.warn_once(warning)
                report.wallet_anchors.append(
                    WalletAnchorResult(
                        player_id=player.external_player_id,
                        currency=wallet.currency,
                        external_transaction_id=None,
                        balance_minor=None,
                        ambiguous=True,
                    )
                )
                continue

            balance = next(iter(balances))
            if balance is None or balance < 0:
                wallet.current_balance_minor = 0
                wallet.balance_initialized = False
                wallet.historical_anchor_transaction_id = None
                report.warn_once(
                    f"Wallet {player.external_player_id}/{wallet.currency} has a negative "
                    "latest source balance; it was preserved but not used as current balance."
                )
                report.wallet_anchors.append(
                    WalletAnchorResult(
                        player_id=player.external_player_id,
                        currency=wallet.currency,
                        external_transaction_id=None,
                        balance_minor=None,
                        ambiguous=True,
                    )
                )
                continue

            anchor = min(latest, key=lambda item: item.external_transaction_id)
            if len(latest) > 1:
                report.warn_once(
                    f"Wallet {player.external_player_id}/{wallet.currency} has multiple "
                    "latest rows with the same balance; the lowest transaction ID was "
                    "used only as the provenance anchor."
                )
            wallet.current_balance_minor = balance
            wallet.balance_initialized = True
            wallet.historical_anchor_transaction_id = anchor.id
            report.wallet_anchors.append(
                WalletAnchorResult(
                    player_id=player.external_player_id,
                    currency=wallet.currency,
                    external_transaction_id=anchor.external_transaction_id,
                    balance_minor=balance,
                    ambiguous=False,
                )
            )

    def _refresh_player_countries(self, report: ImportReport) -> None:
        players = self.session.scalars(select(Player)).all()
        for player in players:
            transactions = list(
                self.session.scalars(
                    select(Transaction)
                    .where(
                        Transaction.player_id == player.id,
                        Transaction.source == TransactionSource.HISTORICAL_IMPORT,
                    )
                    .order_by(Transaction.occurred_at.desc())
                )
            )
            if not transactions:
                continue
            latest_time = transactions[0].occurred_at
            countries = {
                transaction.country
                for transaction in transactions
                if transaction.occurred_at == latest_time
            }
            if len(countries) == 1:
                player.current_country = next(iter(countries))
            else:
                player.current_country = None
                report.warn_once(
                    f"Player {player.external_player_id} has conflicting countries at "
                    "the latest timestamp; current_country was left unset."
                )

    def _audit(
        self,
        *,
        action: str,
        entity_id: str,
        reason: str | None = None,
        after_values: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=None,
                action=action,
                entity_type="transaction",
                entity_id=entity_id,
                after_values=after_values,
                reason=reason,
            )
        )


def register_import_command(app: Flask) -> None:
    @app.cli.command("import-historical")
    @click.argument(
        "source_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
    )
    def import_historical_command(source_path: Path) -> None:
        """Import historical CSV rows without replaying their financial effects."""
        report = HistoricalImporter(get_session()).import_csv(source_path)
        click.echo(json.dumps(report.to_dict(), indent=2, default=str))
