from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "instance" / "wallet_admin.db"


class Config:
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    )
    SECRET_KEY = os.environ.get("SECRET_KEY", "development-only-not-for-production")
    TESTING = False
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    WTF_CSRF_TIME_LIMIT = 8 * 60 * 60


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True


class TestConfig(Config):
    TESTING = True
