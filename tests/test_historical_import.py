from __future__ import annotations

import json

from sqlalchemy import func, select

from app.models import (
    AuditEvent,
    BalancePosting,
    Operator,
    Player,
    Transaction,
    Wallet,
)
from app.services.import_service import HistoricalImporter


def test_import_cli_outputs_structured_report(app, fixture_path) -> None:
    result = app.test_cli_runner().invoke(
        args=["import-historical", str(fixture_path("historical_sample.csv"))]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows_seen"] == 3
    assert payload["imported_transactions"] == 3
    assert len(payload["wallet_anchors"]) == 2


def test_import_preserves_snapshot_without_replaying_effects(session, fixture_path) -> None:
    report = HistoricalImporter(session).import_csv(
        fixture_path("historical_sample.csv")
    )

    assert report.rows_seen == 3
    assert report.imported_transactions == 3
    assert report.rejected_rows == []
    assert report.operators_created == 2
    assert report.players_created == 2
    assert report.wallets_created == 2
    assert session.scalar(select(func.count()).select_from(Transaction)) == 3
    assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0

    player = session.scalar(
        select(Player).where(Player.external_player_id == "PLAYER-1")
    )
    wallet = session.scalar(
        select(Wallet).where(Wallet.player_id == player.id, Wallet.currency == "USD")
    )
    anchor = session.get(Transaction, wallet.historical_anchor_transaction_id)
    assert wallet.balance_initialized is True
    assert wallet.current_balance_minor == 8_000
    assert wallet.version == 0
    assert anchor.external_transaction_id == "TX-002"
    assert anchor.source_balance_after_minor == 8_000


def test_same_id_and_payload_is_idempotent(session, fixture_path) -> None:
    importer = HistoricalImporter(session)
    importer.import_csv(fixture_path("historical_sample.csv"))
    retry_report = importer.import_csv(fixture_path("historical_sample.csv"))

    assert retry_report.imported_transactions == 0
    assert retry_report.idempotent_duplicates == 3
    assert retry_report.conflicting_duplicates == 0
    assert session.scalar(select(func.count()).select_from(Transaction)) == 3
    assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0


def test_same_id_and_different_payload_is_rejected_and_audited(
    session, fixture_path
) -> None:
    importer = HistoricalImporter(session)
    importer.import_csv(fixture_path("historical_sample.csv"))
    conflict_report = importer.import_csv(fixture_path("historical_conflict.csv"))

    assert conflict_report.imported_transactions == 0
    assert conflict_report.conflicting_duplicates == 1
    assert len(conflict_report.rejected_rows) == 1
    original = session.scalar(
        select(Transaction).where(Transaction.external_transaction_id == "TX-001")
    )
    assert original.amount_minor == 10_000
    audit_count = session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "historical_import.duplicate_conflict")
    )
    assert audit_count == 1


def test_conflicting_latest_balances_leave_wallet_uninitialized(
    session, fixture_path
) -> None:
    report = HistoricalImporter(session).import_csv(
        fixture_path("historical_ambiguous.csv")
    )

    wallet = session.scalar(select(Wallet))
    assert report.ambiguous_wallets == 1
    assert wallet.balance_initialized is False
    assert wallet.current_balance_minor == 0
    assert wallet.historical_anchor_transaction_id is None
    assert session.scalar(select(func.count()).select_from(Transaction)) == 2
    assert session.scalar(select(func.count()).select_from(BalancePosting)) == 0
    assert any("conflicting balance-after" in warning for warning in report.warnings)


def test_invalid_rows_are_reported_without_partial_transactions(
    session, fixture_path
) -> None:
    report = HistoricalImporter(session).import_csv(
        fixture_path("historical_invalid.csv")
    )

    assert report.rows_seen == 2
    assert report.imported_transactions == 0
    assert len(report.rejected_rows) == 2
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0
    rejected_audits = session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "historical_import.row_rejected")
    )
    assert rejected_audits == 2


def test_rejected_reversal_leaves_no_partial_support_records(
    session, fixture_path
) -> None:
    report = HistoricalImporter(session).import_csv(
        fixture_path("historical_invalid_reversal.csv")
    )

    assert len(report.rejected_rows) == 1
    assert "referenced reversal transaction was not found" in report.rejected_rows[0].message
    assert session.scalar(select(func.count()).select_from(Transaction)) == 0
    assert session.scalar(select(func.count()).select_from(Wallet)) == 0
    assert session.scalar(select(func.count()).select_from(Player)) == 0
    assert session.scalar(select(func.count()).select_from(Operator)) == 0
