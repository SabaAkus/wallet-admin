from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, flash, redirect, render_template, request, session as flask_session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import select
from werkzeug.security import check_password_hash

from app.extensions import get_session
from app.models import AuditEvent, User


blueprint = Blueprint("auth", __name__)


@blueprint.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if request.method == "GET":
        return render_template("auth/login.html")

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    session = get_session()
    credentials_have_valid_length = len(username) <= 100 and len(password) <= 1024
    user = (
        session.scalar(select(User).where(User.username == username))
        if credentials_have_valid_length
        else None
    )
    valid = bool(
        user is not None
        and user.is_active
        and check_password_hash(user.password_hash, password)
    )
    if valid:
        user.last_login_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                action="auth.login_succeeded",
                entity_type="user",
                entity_id=str(user.id),
                ip_address=request.remote_addr,
            )
        )
        session.commit()
        login_user(user)
        flask_session.permanent = True
        flash("Signed in successfully.", "success")
        return redirect(url_for("dashboard.index"))

    session.add(
        AuditEvent(
            actor_user_id=None,
            action="auth.login_failed",
            entity_type="username",
            entity_id=(username[:100] or "<empty>"),
            reason="Invalid credentials or inactive account",
            ip_address=request.remote_addr,
        )
    )
    session.commit()
    flash("Invalid username or password.", "error")
    return render_template("auth/login.html"), 401


@blueprint.post("/logout")
@login_required
def logout():
    user_id = current_user.id
    session = get_session()
    session.add(
        AuditEvent(
            actor_user_id=user_id,
            action="auth.logout",
            entity_type="user",
            entity_id=str(user_id),
            ip_address=request.remote_addr,
        )
    )
    session.commit()
    logout_user()
    flash("Signed out.", "success")
    return redirect(url_for("auth.login"))
