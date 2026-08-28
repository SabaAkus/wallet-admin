from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker
from werkzeug.security import generate_password_hash

from app.models import Operator, Player, User, UserRole, Wallet


PASSWORDS = {
    "viewer": "ViewerPass123!",
    "finance": "FinancePass123!",
    "admin": "AdminPass123!",
    "inactive": "InactivePass123!",
}


@dataclass(frozen=True)
class WebContext:
    viewer_id: int
    finance_id: int
    admin_id: int
    inactive_id: int
    operator_id: int
    player_id: int
    wallet_id: int


def seed_web_context(factory: sessionmaker[Session]) -> WebContext:
    with factory.begin() as session:
        users = {
            name: User(
                username=name,
                password_hash=generate_password_hash(PASSWORDS[name]),
                role={
                    "viewer": UserRole.VIEWER,
                    "finance": UserRole.FINANCE_OPERATOR,
                    "admin": UserRole.ADMINISTRATOR,
                    "inactive": UserRole.VIEWER,
                }[name],
                is_active=name != "inactive",
            )
            for name in PASSWORDS
        }
        operator = Operator(code="OP-WEB", name="Web Operator")
        player = Player(external_player_id="WEB-PLAYER", current_country="Georgia")
        session.add_all([*users.values(), operator, player])
        session.flush()
        wallet = Wallet(
            player_id=player.id,
            currency="EUR",
            current_balance_minor=10_000,
            balance_initialized=True,
            version=0,
        )
        session.add(wallet)
        session.flush()
        return WebContext(
            viewer_id=users["viewer"].id,
            finance_id=users["finance"].id,
            admin_id=users["admin"].id,
            inactive_id=users["inactive"].id,
            operator_id=operator.id,
            player_id=player.id,
            wallet_id=wallet.id,
        )


def csrf_token(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, response.get_data(as_text=True)
    return match.group(1)


def login(client, username: str, password: str | None = None):
    page = client.get("/login")
    return client.post(
        "/login",
        data={
            "csrf_token": csrf_token(page),
            "username": username,
            "password": password or PASSWORDS[username],
        },
        follow_redirects=False,
    )


def logout(client):
    page = client.get("/")
    return client.post(
        "/logout", data={"csrf_token": csrf_token(page)}, follow_redirects=False
    )
