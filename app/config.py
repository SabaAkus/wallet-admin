from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "instance" / "wallet_admin.db"


class Config:
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    )
    SECRET_KEY = os.environ.get("SECRET_KEY", "development-only-not-for-production")
    TESTING = False


class TestConfig(Config):
    TESTING = True

