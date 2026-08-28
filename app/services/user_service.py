from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app.models import AuditEvent, User, UserRole


SessionFactory = Callable[[], Session]


class UserManagementError(Exception):
    pass


class FinalAdministratorError(UserManagementError):
    pass


class UsernameConflict(UserManagementError):
    pass


@dataclass(frozen=True)
class UserUpdate:
    role: UserRole
    is_active: bool
    reason: str


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if len(normalized) < 3 or len(normalized) > 100:
        raise UserManagementError("Username must contain 3 to 100 characters")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < 12 or len(password) > 1024:
        raise UserManagementError("Password must contain 12 to 1024 characters")


class UserService:
    def __init__(self, session_factory: SessionFactory):
        self._session_factory = session_factory

    def create_user(
        self,
        *,
        actor_user_id: int,
        username: str,
        password: str,
        role: UserRole,
    ) -> User:
        normalized = normalize_username(username)
        validate_password(password)
        with self._session_factory() as session, session.begin():
            actor = self._require_administrator(session, actor_user_id)
            user = User(
                username=normalized,
                password_hash=generate_password_hash(password),
                role=role,
                is_active=True,
            )
            try:
                with session.begin_nested():
                    session.add(user)
                    session.flush()
            except IntegrityError as exc:
                raise UsernameConflict("Username already exists") from exc
            session.add(
                AuditEvent(
                    actor_user_id=actor.id,
                    action="user.created",
                    entity_type="user",
                    entity_id=str(user.id),
                    after_values={
                        "username": user.username,
                        "role": user.role.value,
                        "is_active": user.is_active,
                    },
                )
            )
            return user

    def update_user(
        self,
        *,
        actor_user_id: int,
        target_user_id: int,
        update: UserUpdate,
    ) -> User:
        if not update.reason.strip():
            raise UserManagementError("A reason is required")
        with self._session_factory() as session, session.begin():
            actor = self._require_administrator(session, actor_user_id)
            target = session.get(User, target_user_id)
            if target is None:
                raise UserManagementError("User was not found")
            removes_active_admin = (
                target.role == UserRole.ADMINISTRATOR
                and target.is_active
                and (update.role != UserRole.ADMINISTRATOR or not update.is_active)
            )
            if removes_active_admin:
                active_admin_statement = (
                    select(User.id)
                    .where(
                        User.role == UserRole.ADMINISTRATOR,
                        User.is_active.is_(True),
                    )
                    .order_by(User.id)
                )
                if session.get_bind().dialect.name == "postgresql":
                    active_admin_statement = active_admin_statement.with_for_update()
                active_admin_ids = session.scalars(active_admin_statement).all()
                if len(active_admin_ids) <= 1:
                    raise FinalAdministratorError(
                        "The final active administrator cannot be disabled or demoted"
                    )

            before = {"role": target.role.value, "is_active": target.is_active}
            target.role = update.role
            target.is_active = update.is_active
            session.add(
                AuditEvent(
                    actor_user_id=actor.id,
                    action="user.updated",
                    entity_type="user",
                    entity_id=str(target.id),
                    before_values=before,
                    after_values={
                        "role": target.role.value,
                        "is_active": target.is_active,
                    },
                    reason=update.reason.strip(),
                )
            )
            return target

    @staticmethod
    def _require_administrator(session: Session, actor_user_id: int) -> User:
        actor = session.get(User, actor_user_id)
        if (
            actor is None
            or not actor.is_active
            or actor.role != UserRole.ADMINISTRATOR
        ):
            raise UserManagementError("Active administrator access is required")
        return actor
