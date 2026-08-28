from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import Flask, render_template
from flask_wtf.csrf import CSRFError

from .config import Config, ProductionConfig
from .extensions import init_database, init_web_extensions


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    production = os.environ.get("APP_ENV", "development").lower() == "production"
    app.config.from_object(ProductionConfig if production else Config)
    if config:
        app.config.update(config)
    if production and app.config["SECRET_KEY"] == "development-only-not-for-production":
        raise RuntimeError("SECRET_KEY must be set when APP_ENV=production")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    init_database(app)
    init_web_extensions(app)

    from .auth.commands import register_auth_commands
    from .auth.routes import blueprint as auth_blueprint
    from .admin.routes import blueprint as admin_blueprint
    from .audit.routes import blueprint as audit_blueprint
    from .dashboard.routes import blueprint as dashboard_blueprint
    from .services.import_service import register_import_command
    from .transactions.routes import blueprint as transactions_blueprint
    from .wallets.routes import blueprint as wallets_blueprint
    from .web import format_money, format_utc

    register_import_command(app)
    register_auth_commands(app)
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(dashboard_blueprint)
    app.register_blueprint(transactions_blueprint)
    app.register_blueprint(wallets_blueprint)
    app.register_blueprint(audit_blueprint)
    app.register_blueprint(admin_blueprint)

    app.add_template_filter(format_money, "money")
    app.add_template_filter(format_utc, "utc")

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return render_template("errors/400.html", message=error.description), 400

    return app
