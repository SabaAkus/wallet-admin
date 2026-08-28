from __future__ import annotations

import sqlite3
from typing import Any

from flask import Flask, current_app
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, scoped_session, sessionmaker


login_manager = LoginManager()
csrf = CSRFProtect()


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        if not isinstance(dbapi_connection, sqlite3.Connection):
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def init_database(app: Flask) -> None:
    database_url = app.config["DATABASE_URL"]
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 5}

    engine = create_engine(database_url, future=True, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        _configure_sqlite(engine)

    raw_session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    session_factory = scoped_session(raw_session_factory)
    app.extensions["database_engine"] = engine
    app.extensions["database_session"] = session_factory
    app.extensions["database_session_factory"] = raw_session_factory

    @app.teardown_appcontext
    def remove_database_session(_exception: BaseException | None = None) -> None:
        session_factory.remove()


def init_web_extensions(app: Flask) -> None:
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please sign in to continue."
    login_manager.init_app(app)
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        from app.models import User

        if not user_id.isdigit():
            return None
        user = get_session().get(User, int(user_id))
        if user is None or not user.is_active:
            return None
        return user


def get_engine() -> Engine:
    return current_app.extensions["database_engine"]


def get_session() -> Session:
    return current_app.extensions["database_session"]()
