from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.auth.permissions import administrator_required
from app.extensions import get_session
from app.models import User, UserRole
from app.services.user_service import UserManagementError, UserService, UserUpdate


blueprint = Blueprint("admin", __name__, url_prefix="/admin")


def _user_service() -> UserService:
    return UserService(current_app.extensions["database_session_factory"])


@blueprint.get("/users")
@administrator_required
def users():
    records = get_session().scalars(select(User).order_by(User.username)).all()
    return render_template("admin/users.html", users=records, roles=UserRole)


@blueprint.post("/users")
@administrator_required
def create_user():
    try:
        _user_service().create_user(
            actor_user_id=current_user.id,
            username=request.form.get("username", ""),
            password=request.form.get("password", ""),
            role=UserRole(request.form.get("role", "")),
        )
        flash("User created.", "success")
    except (ValueError, UserManagementError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.users"))


@blueprint.post("/users/<int:user_id>")
@administrator_required
def update_user(user_id: int):
    try:
        _user_service().update_user(
            actor_user_id=current_user.id,
            target_user_id=user_id,
            update=UserUpdate(
                role=UserRole(request.form.get("role", "")),
                is_active=request.form.get("is_active") == "on",
                reason=request.form.get("reason", ""),
            ),
        )
        flash("User updated.", "success")
    except (ValueError, UserManagementError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.users"))

