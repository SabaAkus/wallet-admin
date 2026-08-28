from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pathlib import Path

from sqlalchemy import func, select
from werkzeug.security import check_password_hash

from app.models import (
    AuditEvent,
    BalancePosting,
    Transaction,
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    User,
    UserRole,
    Wallet,
)
from app.services.import_service import HistoricalImporter
from app.services.transaction_service import CreateTransactionCommand, FinancialService
from app.web import pagination_parameters
from tests.web_helpers import csrf_token, login, seed_web_context


def _create_form(context, external_id: str, *, amount: str = "10.00", kind: str = "DEPOSIT"):
    direction = "CREDIT" if kind in {"DEPOSIT", "GAME_WIN"} else "DEBIT"
    return {
        "external_transaction_id": external_id,
        "wallet_id": str(context.wallet_id),
        "operator_id": str(context.operator_id),
        "country": "Georgia",
        "transaction_type": kind,
        "direction": direction,
        "amount": amount,
        "occurred_at": "2026-08-27T12:00",
        "note": "Web workflow test",
    }


def _post_with_page_csrf(client, page_url: str, post_url: str, data: dict[str, str]):
    page = client.get(page_url)
    assert page.status_code == 200
    return client.post(
        post_url,
        data={**data, "csrf_token": csrf_token(page)},
        follow_redirects=False,
    )


def test_end_to_end_create_approve_cancel_reverse_and_audit(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)

    assert login(client, "finance").status_code == 302
    created = _post_with_page_csrf(
        client, "/transactions/new", "/transactions", _create_form(context, "WEB-APPROVE")
    )
    assert created.status_code == 302
    approved_id = created.headers["Location"].rsplit("/", 1)[-1]

    approved = _post_with_page_csrf(
        client,
        f"/transactions/{approved_id}",
        f"/transactions/{approved_id}/approve",
        {},
    )
    assert approved.status_code == 302

    cancelled = _post_with_page_csrf(
        client, "/transactions/new", "/transactions", _create_form(context, "WEB-CANCEL")
    )
    cancelled_id = cancelled.headers["Location"].rsplit("/", 1)[-1]
    response = _post_with_page_csrf(
        client,
        f"/transactions/{cancelled_id}",
        f"/transactions/{cancelled_id}/cancel",
        {"confirm": "yes", "reason": "Duplicate customer request"},
    )
    assert response.status_code == 302

    client.post("/logout", data={"csrf_token": csrf_token(client.get("/"))})
    assert login(client, "admin").status_code == 302
    reversal = _post_with_page_csrf(
        client,
        f"/transactions/{approved_id}/reverse",
        f"/transactions/{approved_id}/reverse",
        {
            "external_transaction_id": "WEB-REVERSAL",
            "reason": "Deposit was entered in error",
            "confirm": "yes",
        },
    )
    assert reversal.status_code == 302
    reversal_id = reversal.headers["Location"].rsplit("/", 1)[-1]
    audit_page = client.get("/audit")
    assert audit_page.status_code == 200
    assert b"transaction.reversed" in audit_page.data

    with session_factory() as session:
        original = session.get(Transaction, approved_id)
        cancelled_transaction = session.get(Transaction, cancelled_id)
        reversal_transaction = session.get(Transaction, reversal_id)
        wallet = session.get(Wallet, context.wallet_id)
        assert original.status == TransactionStatus.REVERSED
        assert cancelled_transaction.status == TransactionStatus.CANCELLED
        assert reversal_transaction.status == TransactionStatus.APPROVED
        assert wallet.current_balance_minor == 10_000
        assert session.scalar(select(func.count(BalancePosting.id))) == 2


def test_finance_operator_can_explicitly_fail_pending_transaction(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)
    login(client, "finance")
    created = _post_with_page_csrf(
        client, "/transactions/new", "/transactions", _create_form(context, "WEB-FAIL")
    )
    transaction_id = created.headers["Location"].rsplit("/", 1)[-1]
    response = _post_with_page_csrf(
        client,
        f"/transactions/{transaction_id}",
        f"/transactions/{transaction_id}/fail",
        {"reason": "Manual compliance rejection"},
    )
    assert response.status_code == 302
    with session_factory() as session:
        transaction = session.get(Transaction, transaction_id)
        assert transaction.status == TransactionStatus.FAILED
        assert transaction.status_reason == "Manual compliance rejection"
        assert session.scalar(select(func.count(BalancePosting.id))) == 0


def test_transaction_filters_and_pagination(client, session_factory) -> None:
    context = seed_web_context(session_factory)
    service = FinancialService(session_factory)
    for number in range(12):
        service.create_transaction(
            CreateTransactionCommand(
                external_transaction_id=f"FILTER-{number:02d}",
                player_external_id="WEB-PLAYER",
                operator_code="OP-WEB",
                country="Georgia" if number < 11 else "Canada",
                transaction_type=TransactionType.DEPOSIT,
                direction=TransactionDirection.CREDIT,
                amount_minor=100 + number,
                currency="EUR",
                occurred_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=number),
                actor_user_id=context.finance_id,
            )
        )

    login(client, "viewer")
    first_page = client.get(
        "/transactions?player=WEB&operator=OP-WEB&country=Georgia&status=PENDING"
        "&type=DEPOSIT&source=SYSTEM&date_from=2026-08-01&date_to=2026-08-31"
        "&per_page=5&page=1"
    )
    assert first_page.status_code == 200
    assert b"FILTER-10" in first_page.data
    assert b"FILTER-05" not in first_page.data
    assert b"FILTER-11" not in first_page.data
    assert b"Page 1 of 3" in first_page.data

    second_page = client.get(
        "/transactions?country=Georgia&per_page=5&page=2"
    )
    assert second_page.status_code == 200
    assert b"FILTER-05" in second_page.data
    assert b"FILTER-10" not in second_page.data


def test_wallet_filters_and_pagination(client, session_factory) -> None:
    seed_web_context(session_factory)
    login(client, "viewer")
    response = client.get("/wallets?player=WEB-PLAYER&currency=EUR&per_page=5")
    assert response.status_code == 200
    assert b"WEB-PLAYER" in response.data
    assert b"EUR 100.00" in response.data


def test_nonfinite_amount_is_validation_error_and_page_is_bounded(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)
    login(client, "finance")
    response = _post_with_page_csrf(
        client,
        "/transactions/new",
        "/transactions",
        _create_form(context, "NONFINITE-AMOUNT", amount="Infinity"),
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/transactions/new")
    with session_factory() as session:
        assert session.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.external_transaction_id == "NONFINITE-AMOUNT"
            )
        ) == 0

    bounded = client.get("/transactions?page=999999999999&per_page=999999")
    assert bounded.status_code == 200
    with client.application.test_request_context(
        "/transactions?page=999999999999&per_page=999999"
    ):
        assert pagination_parameters() == (10_000, 100)


def test_administrator_user_change_is_audited(client, session_factory) -> None:
    context = seed_web_context(session_factory)
    login(client, "admin")
    response = _post_with_page_csrf(
        client,
        "/admin/users",
        f"/admin/users/{context.viewer_id}",
        {
            "role": "FINANCE_OPERATOR",
            "is_active": "on",
            "reason": "Approved responsibility change",
        },
    )
    assert response.status_code == 302
    with session_factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "user.updated")
        )
        assert event is not None
        assert event.actor_user_id == context.admin_id
        assert event.reason == "Approved responsibility change"
        assert event.before_values["role"] == "VIEWER"
        assert event.after_values["role"] == "FINANCE_OPERATOR"


def test_administrator_creates_user_with_hashed_password(client, session_factory) -> None:
    seed_web_context(session_factory)
    login(client, "admin")
    response = _post_with_page_csrf(
        client,
        "/admin/users",
        "/admin/users",
        {
            "username": "new-finance-user",
            "password": "OneTimePassword123!",
            "role": "FINANCE_OPERATOR",
        },
    )
    assert response.status_code == 302
    with session_factory() as session:
        user = session.scalar(select(User).where(User.username == "new-finance-user"))
        assert user.role == UserRole.FINANCE_OPERATOR
        assert user.password_hash != "OneTimePassword123!"
        assert check_password_hash(user.password_hash, "OneTimePassword123!")
        assert session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "user.created")
        ) == 1


def test_historical_pending_transaction_is_visibly_read_only_and_cannot_process(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)
    source = Path(__file__).resolve().parent / "fixtures" / "historical_sample.csv"
    with session_factory() as session:
        HistoricalImporter(session).import_csv(source)
    with session_factory() as session:
        historical = session.scalar(
            select(Transaction).where(
                Transaction.external_transaction_id == "TX-003"
            )
        )
        assert historical.status == TransactionStatus.PENDING

    login(client, "admin")
    page = client.get(f"/transactions/{historical.id}")
    assert page.status_code == 200
    assert b"Historical source record" in page.data
    assert b"Process pending transaction" not in page.data
    response = client.post(
        f"/transactions/{historical.id}/approve",
        data={"csrf_token": csrf_token(page)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Historical transactions cannot be processed" in response.data
    with session_factory() as session:
        unchanged = session.get(Transaction, historical.id)
        assert unchanged.status == TransactionStatus.PENDING
        assert session.scalar(select(func.count(BalancePosting.id))) == 0
