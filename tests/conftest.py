from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.orm import Session, sessionmaker

from app import create_app


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def migrated_database(database_url: str) -> str:
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.fixture
def app(migrated_database: str):
    application = create_app(
        {
            "TESTING": True,
            "DATABASE_URL": migrated_database,
            "SECRET_KEY": "test-only",
        }
    )
    yield application
    application.extensions["database_session"].remove()
    application.extensions["database_engine"].dispose()


@pytest.fixture
def session(app) -> Session:
    database_session = app.extensions["database_session"]()
    yield database_session
    database_session.rollback()
    database_session.close()


@pytest.fixture
def session_factory(app):
    return sessionmaker(
        bind=app.extensions["database_engine"],
        autoflush=False,
        expire_on_commit=False,
    )


@pytest.fixture
def fixture_path():
    def get_fixture(name: str) -> Path:
        return FIXTURES / name

    return get_fixture
