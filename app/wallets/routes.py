from __future__ import annotations

from flask import Blueprint, abort, render_template, request
from sqlalchemy import select

from app.auth.permissions import viewer_required
from app.extensions import get_session
from app.models import BalancePosting, Player, Transaction, Wallet
from app.web import paginate, pagination_parameters


blueprint = Blueprint("wallets", __name__, url_prefix="/wallets")


@blueprint.get("")
@viewer_required
def list_wallets():
    session = get_session()
    statement = select(Wallet, Player).join(Player, Wallet.player_id == Player.id)
    player = request.args.get("player", "").strip()
    currency = request.args.get("currency", "").strip().upper()
    filter_args: dict[str, str] = {}
    if player:
        statement = statement.where(Player.external_player_id.contains(player))
        filter_args["player"] = player
    if currency:
        statement = statement.where(Wallet.currency == currency)
        filter_args["currency"] = currency
    page, per_page = pagination_parameters()
    page_data = paginate(
        session,
        statement.order_by(Player.external_player_id, Wallet.currency),
        page,
        per_page,
    )
    return render_template(
        "wallets/list.html", page_data=page_data, filter_args=filter_args
    )


@blueprint.get("/<int:wallet_id>")
@viewer_required
def detail(wallet_id: int):
    session = get_session()
    wallet = session.get(Wallet, wallet_id)
    if wallet is None:
        abort(404)
    player = session.get(Player, wallet.player_id)
    transactions = session.scalars(
        select(Transaction)
        .where(Transaction.wallet_id == wallet.id)
        .order_by(Transaction.occurred_at.desc())
        .limit(100)
    ).all()
    postings = session.scalars(
        select(BalancePosting)
        .where(BalancePosting.wallet_id == wallet.id)
        .order_by(BalancePosting.wallet_version.desc())
        .limit(100)
    ).all()
    return render_template(
        "wallets/detail.html",
        wallet=wallet,
        player=player,
        transactions=transactions,
        postings=postings,
    )

