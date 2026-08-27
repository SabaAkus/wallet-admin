from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask

from .config import Config
from .extensions import init_database


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    init_database(app)

    from .services.import_service import register_import_command

    register_import_command(app)
    return app

