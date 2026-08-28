from __future__ import annotations

import secrets

import click
from flask import Flask
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.models import AuditEvent, User, UserRole
from app.services.user_service import normalize_username, validate_password


def register_auth_commands(app: Flask) -> None:
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_admin(username: str, password: str) -> None:
        """Create an active initial administrator with a securely hashed password."""
        normalized = normalize_username(username)
        validate_password(password)
        session_factory = app.extensions["database_session_factory"]
        with session_factory() as session, session.begin():
            if session.scalar(select(User).where(User.username == normalized)):
                raise click.ClickException("Username already exists")
            user = User(
                username=normalized,
                password_hash=generate_password_hash(password),
                role=UserRole.ADMINISTRATOR,
                is_active=True,
            )
            session.add(user)
            session.flush()
            session.add(
                AuditEvent(
                    actor_user_id=user.id,
                    action="user.initial_admin_created",
                    entity_type="user",
                    entity_id=str(user.id),
                    after_values={"username": user.username, "role": user.role.value},
                )
            )
        click.echo(f"Created administrator {normalized!r}.")

    @app.cli.command("setup-demo-users")
    @click.option("--prefix", default="demo", show_default=True)
    def setup_demo_users(prefix: str) -> None:
        """Create one random-password demo user for each role."""
        normalized_prefix = normalize_username(prefix)
        definitions = [
            (f"{normalized_prefix}-admin", UserRole.ADMINISTRATOR),
            (f"{normalized_prefix}-finance", UserRole.FINANCE_OPERATOR),
            (f"{normalized_prefix}-viewer", UserRole.VIEWER),
        ]
        credentials = [(username, secrets.token_urlsafe(15), role) for username, role in definitions]
        session_factory = app.extensions["database_session_factory"]
        with session_factory() as session, session.begin():
            existing = session.scalars(
                select(User).where(User.username.in_([item[0] for item in definitions]))
            ).all()
            if existing:
                raise click.ClickException(
                    "Demo usernames already exist; choose a different --prefix"
                )
            users = []
            for username, password, role in credentials:
                user = User(
                    username=username,
                    password_hash=generate_password_hash(password),
                    role=role,
                    is_active=True,
                )
                session.add(user)
                users.append(user)
            session.flush()
            admin = users[0]
            for user in users:
                session.add(
                    AuditEvent(
                        actor_user_id=admin.id,
                        action="user.demo_created",
                        entity_type="user",
                        entity_id=str(user.id),
                        after_values={"username": user.username, "role": user.role.value},
                    )
                )
        click.echo("Demo credentials (shown once):")
        for username, password, role in credentials:
            click.echo(f"{role.value}: {username} / {password}")
