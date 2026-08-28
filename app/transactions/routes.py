from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from app.auth.permissions import administrator_required, finance_required, viewer_required
from app.extensions import get_session
from app.models import (
    BalancePosting,
    Operator,
    Player,
    Transaction,
    TransactionDirection,
    TransactionSource,
    TransactionStatus,
    TransactionType,
    UserRole,
    Wallet,
)
from app.services.transaction_service import (
    CreateTransactionCommand,
    FinancialService,
    FinancialServiceError,
)
from app.web import paginate, pagination_parameters, parse_major_money


blueprint = Blueprint("transactions", __name__, url_prefix="/transactions")


def _financial_service() -> FinancialService:
    return FinancialService(current_app.extensions["database_session_factory"])


def _get_transaction(transaction_id: str) -> Transaction:
    transaction = get_session().get(Transaction, transaction_id)
    if transaction is None:
        abort(404)
    return transaction


def _parse_utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Date/time must be valid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@blueprint.get("")
@viewer_required
def list_transactions():
    session = get_session()
    statement = (
        select(Transaction, Player, Operator)
        .join(Player, Transaction.player_id == Player.id)
        .join(Operator, Transaction.operator_id == Operator.id)
    )
    filter_args: dict[str, str] = {}
    player = request.args.get("player", "").strip()
    operator = request.args.get("operator", "").strip()
    country = request.args.get("country", "").strip()
    status = request.args.get("status", "").strip().upper()
    transaction_type = request.args.get("type", "").strip().upper()
    source = request.args.get("source", "").strip().upper()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    if player:
        statement = statement.where(Player.external_player_id.contains(player))
        filter_args["player"] = player
    if operator:
        statement = statement.where(Operator.code == operator.upper())
        filter_args["operator"] = operator
    if country:
        statement = statement.where(Transaction.country == country)
        filter_args["country"] = country
    try:
        if status:
            statement = statement.where(Transaction.status == TransactionStatus(status))
            filter_args["status"] = status
        if transaction_type:
            statement = statement.where(
                Transaction.transaction_type == TransactionType(transaction_type)
            )
            filter_args["type"] = transaction_type
        if source:
            statement = statement.where(Transaction.source == TransactionSource(source))
            filter_args["source"] = source
    except ValueError:
        flash("One or more filters were invalid and were ignored.", "error")
    try:
        if date_from:
            start = datetime.combine(datetime.fromisoformat(date_from).date(), time.min, UTC)
            statement = statement.where(Transaction.occurred_at >= start)
            filter_args["date_from"] = date_from
        if date_to:
            end = datetime.combine(datetime.fromisoformat(date_to).date(), time.min, UTC) + timedelta(days=1)
            statement = statement.where(Transaction.occurred_at < end)
            filter_args["date_to"] = date_to
    except ValueError:
        flash("Date filters must use YYYY-MM-DD.", "error")

    page, per_page = pagination_parameters()
    page_data = paginate(
        session,
        statement.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()),
        page,
        per_page,
    )
    operators = session.scalars(select(Operator).order_by(Operator.name)).all()
    return render_template(
        "transactions/list.html",
        page_data=page_data,
        operators=operators,
        statuses=TransactionStatus,
        transaction_types=TransactionType,
        sources=TransactionSource,
        filter_args=filter_args,
    )


@blueprint.get("/new")
@finance_required
def new_transaction():
    session = get_session()
    wallets = session.execute(
        select(Wallet, Player)
        .join(Player, Wallet.player_id == Player.id)
        .where(Wallet.balance_initialized.is_(True))
        .order_by(Player.external_player_id, Wallet.currency)
    ).all()
    operators = session.scalars(
        select(Operator).where(Operator.is_active.is_(True)).order_by(Operator.name)
    ).all()
    return render_template(
        "transactions/new.html",
        wallets=wallets,
        operators=operators,
        transaction_types=[item for item in TransactionType if item != TransactionType.REVERSAL],
        directions=TransactionDirection,
        now=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M"),
    )


@blueprint.post("")
@finance_required
def create_transaction():
    session = get_session()
    try:
        wallet = session.get(Wallet, int(request.form.get("wallet_id", "0")))
        if wallet is None:
            raise ValueError("Wallet is required")
        player = session.get(Player, wallet.player_id)
        operator = session.get(Operator, int(request.form.get("operator_id", "0")))
        if player is None or operator is None:
            raise ValueError("Player and operator are required")
        command = CreateTransactionCommand(
            external_transaction_id=request.form.get("external_transaction_id", ""),
            player_external_id=player.external_player_id,
            operator_code=operator.code,
            country=request.form.get("country", ""),
            transaction_type=TransactionType(request.form.get("transaction_type", "")),
            direction=TransactionDirection(request.form.get("direction", "")),
            amount_minor=parse_major_money(request.form.get("amount", "")),
            currency=wallet.currency,
            occurred_at=_parse_utc_datetime(request.form.get("occurred_at", "")),
            actor_user_id=current_user.id,
            note=request.form.get("note"),
        )
        result = _financial_service().create_transaction(command)
        flash(
            "Existing idempotent transaction returned."
            if result.idempotent
            else "Pending transaction created.",
            "success",
        )
        return redirect(url_for("transactions.detail", transaction_id=result.transaction.id))
    except (ValueError, FinancialServiceError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("transactions.new_transaction"))


@blueprint.get("/<transaction_id>")
@viewer_required
def detail(transaction_id: str):
    session = get_session()
    transaction = _get_transaction(transaction_id)
    player = session.get(Player, transaction.player_id)
    operator = session.get(Operator, transaction.operator_id)
    posting = session.scalar(
        select(BalancePosting).where(BalancePosting.transaction_id == transaction.id)
    )
    can_finance = current_user.role in {
        UserRole.FINANCE_OPERATOR,
        UserRole.ADMINISTRATOR,
    }
    return render_template(
        "transactions/detail.html",
        transaction=transaction,
        player=player,
        operator=operator,
        posting=posting,
        can_process=(
            transaction.source == TransactionSource.SYSTEM
            and transaction.status == TransactionStatus.PENDING
            and (
                (
                    transaction.transaction_type != TransactionType.REVERSAL
                    and can_finance
                )
                or (
                    transaction.transaction_type == TransactionType.REVERSAL
                    and current_user.role == UserRole.ADMINISTRATOR
                )
            )
        ),
        can_reverse=(
            current_user.role == UserRole.ADMINISTRATOR
            and transaction.source == TransactionSource.SYSTEM
            and transaction.status == TransactionStatus.APPROVED
            and transaction.transaction_type != TransactionType.REVERSAL
        ),
    )


@blueprint.post("/<transaction_id>/approve")
@finance_required
def approve(transaction_id: str):
    try:
        result = _financial_service().approve_transaction(
            transaction_id, actor_user_id=current_user.id
        )
        category = "success" if result.transaction.status == TransactionStatus.APPROVED else "error"
        flash(
            f"Transaction is {result.transaction.status.value.lower()}."
            + (f" {result.transaction.status_reason}" if result.transaction.status_reason else ""),
            category,
        )
    except FinancialServiceError as exc:
        flash(str(exc), "error")
    return redirect(url_for("transactions.detail", transaction_id=transaction_id))


@blueprint.post("/<transaction_id>/fail")
@finance_required
def fail(transaction_id: str):
    try:
        _financial_service().fail_transaction(
            transaction_id,
            actor_user_id=current_user.id,
            reason=request.form.get("reason", ""),
        )
        flash("Transaction marked failed.", "success")
    except FinancialServiceError as exc:
        flash(str(exc), "error")
    return redirect(url_for("transactions.detail", transaction_id=transaction_id))


@blueprint.post("/<transaction_id>/cancel")
@finance_required
def cancel(transaction_id: str):
    if request.form.get("confirm") != "yes":
        flash("Cancellation must be confirmed.", "error")
        return redirect(url_for("transactions.detail", transaction_id=transaction_id))
    try:
        _financial_service().cancel_transaction(
            transaction_id,
            actor_user_id=current_user.id,
            reason=request.form.get("reason", ""),
        )
        flash("Transaction cancelled.", "success")
    except FinancialServiceError as exc:
        flash(str(exc), "error")
    return redirect(url_for("transactions.detail", transaction_id=transaction_id))


@blueprint.route("/<transaction_id>/reverse", methods=["GET", "POST"])
@administrator_required
def reverse(transaction_id: str):
    transaction = _get_transaction(transaction_id)
    if (
        transaction.source != TransactionSource.SYSTEM
        or transaction.status != TransactionStatus.APPROVED
        or transaction.transaction_type == TransactionType.REVERSAL
    ):
        abort(409)
    if request.method == "GET":
        return render_template("transactions/reverse.html", transaction=transaction)
    if request.form.get("confirm") != "yes":
        flash("Reversal must be confirmed.", "error")
        return redirect(url_for("transactions.reverse", transaction_id=transaction_id))
    try:
        creation = _financial_service().create_reversal(
            transaction.id,
            external_transaction_id=request.form.get("external_transaction_id", ""),
            actor_user_id=current_user.id,
            reason=request.form.get("reason", ""),
        )
        result = _financial_service().approve_transaction(
            creation.transaction.id, actor_user_id=current_user.id
        )
        flash(
            f"Reversal is {result.transaction.status.value.lower()}."
            + (f" {result.transaction.status_reason}" if result.transaction.status_reason else ""),
            "success" if result.transaction.status == TransactionStatus.APPROVED else "error",
        )
        return redirect(
            url_for("transactions.detail", transaction_id=creation.transaction.id)
        )
    except FinancialServiceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("transactions.reverse", transaction_id=transaction_id))
