from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import abort
from flask_login import current_user, login_required

from app.models import UserRole


VIEW_ROLES = {
    UserRole.VIEWER,
    UserRole.FINANCE_OPERATOR,
    UserRole.ADMINISTRATOR,
}
FINANCE_ROLES = {UserRole.FINANCE_OPERATOR, UserRole.ADMINISTRATOR}
ADMIN_ROLES = {UserRole.ADMINISTRATOR}


def roles_required(*allowed_roles: UserRole):
    allowed = set(allowed_roles)

    def decorator(view: Callable[..., Any]):
        @wraps(view)
        @login_required
        def wrapped(*args: Any, **kwargs: Any):
            if not current_user.is_active or current_user.role not in allowed:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


viewer_required = roles_required(*VIEW_ROLES)
finance_required = roles_required(*FINANCE_ROLES)
administrator_required = roles_required(*ADMIN_ROLES)

