from __future__ import annotations

import re

import pytest
from sqlalchemy import func, select
from werkzeug.security import check_password_hash

from app import create_app
from app.models import AuditEvent, Transaction, TransactionStatus, User, UserRole
from app.services.transaction_service import (
    FinancialService,
    PermissionDenied,
    TransactionDirection,
    TransactionType,
)
from app.services.user_service import FinalAdministratorError, UserService, UserUpdate
from tests.financial_helpers import FinancialContext, command_for
from tests.web_helpers import PASSWORDS, csrf_token, login, seed_web_context


def test_local_and_production_session_cookie_settings(
    app, client, session_factory, monkeypatch
) -> None:
    seed_web_context(session_factory)
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.debug is False
    login(client, "viewer")
    with client.session_transaction() as browser_session:
        assert browser_session.permanent is True

    monkeypatch.setenv("APP_ENV", "production")
    production_app = create_app(
        {
            "DATABASE_URL": app.config["DATABASE_URL"],
            "SECRET_KEY": "production-test-key",
            "TESTING": True,
        }
    )
    try:
        assert production_app.config["SESSION_COOKIE_SECURE"] is True
        assert production_app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert production_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    finally:
        production_app.extensions["database_session"].remove()
        production_app.extensions["database_engine"].dispose()


def test_production_refuses_development_secret(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="SECRET_KEY must be set"):
        create_app({"SECRET_KEY": "development-only-not-for-production"})


@pytest.mark.parametrize(
    ("username", "path", "expected"),
    [
        ("viewer", "/", 200),
        ("viewer", "/wallets", 200),
        ("viewer", "/transactions", 200),
        ("viewer", "/transactions/new", 403),
        ("viewer", "/audit", 403),
        ("viewer", "/admin/users", 403),
        ("finance", "/", 200),
        ("finance", "/transactions/new", 200),
        ("finance", "/audit", 403),
        ("finance", "/admin/users", 403),
        ("admin", "/", 200),
        ("admin", "/transactions/new", 200),
        ("admin", "/audit", 200),
        ("admin", "/admin/users", 200),
    ],
)
def test_role_page_matrix(client, session_factory, username, path, expected) -> None:
    seed_web_context(session_factory)
    assert login(client, username).status_code == 302
    assert client.get(path).status_code == expected


def test_login_success_failure_and_inactive_accounts_are_audited(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)
    assert login(client, "viewer", "wrong-password").status_code == 401
    assert login(client, "inactive").status_code == 401
    assert login(client, "viewer").status_code == 302

    with session_factory() as session:
        viewer = session.get(User, context.viewer_id)
        assert viewer.last_login_at is not None
        assert session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "auth.login_failed")
        ) == 2
        assert session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "auth.login_succeeded")
        ) == 1


def test_oversized_credentials_fail_cleanly_and_audit_safely(
    client, session_factory
) -> None:
    seed_web_context(session_factory)
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf_token(page),
            "username": "x" * 10_000,
            "password": "x" * 10_000,
        },
    )
    assert response.status_code == 401
    assert b"Traceback" not in response.data
    with session_factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "auth.login_failed")
        )
        assert len(event.entity_id) == 100


def test_login_ignores_untrusted_next_redirect(client, session_factory) -> None:
    seed_web_context(session_factory)
    page = client.get("/login?next=https://attacker.invalid/collect")
    response = client.post(
        "/login?next=https://attacker.invalid/collect",
        data={
            "csrf_token": csrf_token(page),
            "username": "viewer",
            "password": PASSWORDS["viewer"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_inactive_user_loses_session_access(client, session_factory) -> None:
    context = seed_web_context(session_factory)
    assert login(client, "viewer").status_code == 302
    page = client.get("/")
    token = csrf_token(page)
    with session_factory.begin() as session:
        session.get(User, context.viewer_id).is_active = False

    assert client.get("/").status_code == 302
    assert client.post("/logout", data={"csrf_token": token}).status_code == 302


def test_csrf_rejects_state_change_without_token(client, session_factory) -> None:
    seed_web_context(session_factory)
    login(client, "admin")
    assert client.post("/logout").status_code == 400
    assert client.post("/admin/users", data={}).status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/logout",
        "/transactions",
        "/transactions/example/approve",
        "/transactions/example/fail",
        "/transactions/example/cancel",
        "/transactions/example/reverse",
        "/admin/users",
        "/admin/users/1",
    ],
)
def test_every_state_changing_route_requires_csrf(client, path) -> None:
    assert client.post(path, data={}).status_code == 400


def test_viewer_direct_posts_are_rejected_and_leave_transaction_pending(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)
    financial_context = FinancialContext(
        user_id=context.admin_id,
        player_id=context.player_id,
        player_external_id="WEB-PLAYER",
        operator_id=context.operator_id,
        operator_code="OP-WEB",
        wallet_id=context.wallet_id,
        currency="EUR",
    )
    service = FinancialService(session_factory)
    transaction = service.create_transaction(
        command_for(financial_context, "WEB-PENDING-ACTION")
    ).transaction
    login(client, "viewer")
    token = csrf_token(client.get("/"))

    for action, data in (
        ("approve", {}),
        ("fail", {"reason": "No"}),
        ("cancel", {"reason": "No", "confirm": "yes"}),
    ):
        response = client.post(
            f"/transactions/{transaction.id}/{action}",
            data={"csrf_token": token, **data},
        )
        assert response.status_code == 403

    with session_factory() as session:
        assert session.get(type(transaction), transaction.id).status == TransactionStatus.PENDING


@pytest.mark.parametrize(
    ("username", "path", "data"),
    [
        ("viewer", "/transactions", {}),
        ("viewer", "/admin/users", {}),
        ("finance", "/admin/users", {}),
    ],
)
def test_unauthorized_direct_post_matrix(
    client, session_factory, username, path, data
) -> None:
    seed_web_context(session_factory)
    login(client, username)
    token = csrf_token(client.get("/"))
    response = client.post(path, data={"csrf_token": token, **data})
    assert response.status_code == 403


def test_finance_operator_cannot_post_reversal_directly(
    client, session_factory
) -> None:
    context = seed_web_context(session_factory)
    financial_context = FinancialContext(
        user_id=context.admin_id,
        player_id=context.player_id,
        player_external_id="WEB-PLAYER",
        operator_id=context.operator_id,
        operator_code="OP-WEB",
        wallet_id=context.wallet_id,
        currency="EUR",
    )
    service = FinancialService(session_factory)
    original = service.create_transaction(
        command_for(
            financial_context,
            "ADMIN-APPROVED-FOR-ROUTE",
            transaction_type=TransactionType.DEPOSIT,
            direction=TransactionDirection.CREDIT,
        )
    ).transaction
    service.approve_transaction(original.id, actor_user_id=context.admin_id)

    login(client, "finance")
    token = csrf_token(client.get("/"))
    response = client.post(
        f"/transactions/{original.id}/reverse",
        data={
            "csrf_token": token,
            "external_transaction_id": "FINANCE-ROUTE-REVERSAL",
            "reason": "Not permitted",
            "confirm": "yes",
        },
    )
    assert response.status_code == 403

    with session_factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.reverses_transaction_id == original.id)
        ) == 0


def test_service_layer_permission_checks_all_roles(session_factory) -> None:
    context = seed_web_context(session_factory)
    service = FinancialService(session_factory)
    viewer_context = FinancialContext(
        user_id=context.viewer_id,
        player_id=context.player_id,
        player_external_id="WEB-PLAYER",
        operator_id=context.operator_id,
        operator_code="OP-WEB",
        wallet_id=context.wallet_id,
        currency="EUR",
    )
    with pytest.raises(PermissionDenied):
        service.create_transaction(command_for(viewer_context, "VIEWER-DIRECT"))

    finance_context = FinancialContext(**{**viewer_context.__dict__, "user_id": context.finance_id})
    original = service.create_transaction(
        command_for(
            finance_context,
            "FINANCE-ALLOWED",
            transaction_type=TransactionType.DEPOSIT,
            direction=TransactionDirection.CREDIT,
        )
    ).transaction
    service.approve_transaction(original.id, actor_user_id=context.finance_id)
    with pytest.raises(PermissionDenied):
        service.create_reversal(
            original.id,
            external_transaction_id="FINANCE-REV-DENIED",
            actor_user_id=context.finance_id,
            reason="Not allowed",
        )


def test_final_active_administrator_cannot_be_disabled_or_demoted(
    session_factory,
) -> None:
    context = seed_web_context(session_factory)
    service = UserService(session_factory)
    with pytest.raises(FinalAdministratorError):
        service.update_user(
            actor_user_id=context.admin_id,
            target_user_id=context.admin_id,
            update=UserUpdate(
                role=UserRole.VIEWER,
                is_active=False,
                reason="Would remove final administrator",
            ),
        )

    with session_factory() as session:
        admin = session.get(User, context.admin_id)
        assert admin.role == UserRole.ADMINISTRATOR
        assert admin.is_active is True


def test_initial_admin_and_random_demo_cli_commands(app, session_factory) -> None:
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "initial-admin"],
        input="LongInitialPass123!\nLongInitialPass123!\n",
    )
    assert result.exit_code == 0, result.output
    demo = runner.invoke(args=["setup-demo-users", "--prefix", "showcase"])
    assert demo.exit_code == 0, demo.output
    assert "shown once" in demo.output
    assert "showcase-admin" in demo.output

    with session_factory() as session:
        initial = session.scalar(select(User).where(User.username == "initial-admin"))
        assert initial.role == UserRole.ADMINISTRATOR
        assert check_password_hash(initial.password_hash, "LongInitialPass123!")
        demo_users = session.scalars(
            select(User).where(User.username.like("showcase-%"))
        ).all()
        assert {user.role for user in demo_users} == set(UserRole)
        assert all("Pass" not in user.password_hash for user in demo_users)
