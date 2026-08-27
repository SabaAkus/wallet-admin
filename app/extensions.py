from __future__ import annotations

import sqlite3
from typing import Any

from flask import Flask, current_app
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, scoped_session, sessionmaker


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

    session_factory = scoped_session(
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    )
    app.extensions["database_engine"] = engine
    app.extensions["database_session"] = session_factory

    @app.teardown_appcontext
    def remove_database_session(_exception: BaseException | None = None) -> None:
        session_factory.remove()


def get_engine() -> Engine:
    return current_app.extensions["database_engine"]


def get_session() -> Session:
    return current_app.extensions["database_session"]()

