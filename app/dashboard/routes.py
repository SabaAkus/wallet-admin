from __future__ import annotations

from flask import Blueprint, render_template
from sqlalchemy import func, select

from app.auth.permissions import viewer_required
from app.extensions import get_session
from app.models import Transaction, TransactionStatus, Wallet


blueprint = Blueprint("dashboard", __name__)


@blueprint.get("/")
@viewer_required
def index():
    session = get_session()
    approved_rows = session.execute(
        select(
            Transaction.currency,
            Transaction.transaction_type,
            func.sum(Transaction.amount_minor),
            func.count(Transaction.id),
        )
        .where(Transaction.status == TransactionStatus.APPROVED)
        .group_by(Transaction.currency, Transaction.transaction_type)
        .order_by(Transaction.currency, Transaction.transaction_type)
    ).all()
    pending_rows = session.execute(
        select(Transaction.currency, func.count(Transaction.id), func.sum(Transaction.amount_minor))
        .where(Transaction.status == TransactionStatus.PENDING)
        .group_by(Transaction.currency)
        .order_by(Transaction.currency)
    ).all()
    wallet_rows = session.execute(
        select(Wallet.currency, func.sum(Wallet.current_balance_minor), func.count(Wallet.id))
        .where(Wallet.balance_initialized.is_(True))
        .group_by(Wallet.currency)
        .order_by(Wallet.currency)
    ).all()
    return render_template(
        "dashboard/index.html",
        approved_rows=approved_rows,
        pending_rows=pending_rows,
        wallet_rows=wallet_rows,
    )

