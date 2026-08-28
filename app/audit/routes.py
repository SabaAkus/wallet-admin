from __future__ import annotations

from flask import Blueprint, render_template, request
from sqlalchemy import select

from app.auth.permissions import administrator_required
from app.extensions import get_session
from app.models import AuditEvent, User
from app.web import paginate, pagination_parameters


blueprint = Blueprint("audit", __name__, url_prefix="/audit")


@blueprint.get("")
@administrator_required
def list_events():
    session = get_session()
    statement = select(AuditEvent, User).outerjoin(
        User, AuditEvent.actor_user_id == User.id
    )
    action = request.args.get("action", "").strip()
    entity_type = request.args.get("entity_type", "").strip()
    filter_args: dict[str, str] = {}
    if action:
        statement = statement.where(AuditEvent.action.contains(action))
        filter_args["action"] = action
    if entity_type:
        statement = statement.where(AuditEvent.entity_type == entity_type)
        filter_args["entity_type"] = entity_type
    page, per_page = pagination_parameters()
    page_data = paginate(
        session,
        statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()),
        page,
        per_page,
    )
    return render_template(
        "audit/list.html", page_data=page_data, filter_args=filter_args
    )

