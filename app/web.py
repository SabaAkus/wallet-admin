from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import ceil

from flask import request
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Page:
    items: list
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.per_page))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def pagination_parameters(default_per_page: int = 20) -> tuple[int, int]:
    try:
        page = min(10_000, max(1, int(request.args.get("page", "1"))))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", str(default_per_page)))
    except ValueError:
        per_page = default_per_page
    return page, min(100, max(5, per_page))


def paginate(session: Session, statement: Select, page: int, per_page: int) -> Page:
    count_statement = select(func.count()).select_from(
        statement.order_by(None).subquery()
    )
    total = session.scalar(count_statement) or 0
    items = list(
        session.execute(statement.offset((page - 1) * per_page).limit(per_page)).all()
    )
    return Page(items=items, page=page, per_page=per_page, total=total)


def parse_major_money(value: str) -> int:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("Amount must be a valid decimal") from exc
    if not amount.is_finite():
        raise ValueError("Amount must be a finite decimal")
    minor = amount * 100
    if minor != minor.to_integral_value() or minor <= 0:
        raise ValueError("Amount must be positive with at most two decimal places")
    return int(minor)


def format_money(minor_units: int | None, currency: str) -> str:
    if minor_units is None:
        return "Unavailable"
    sign = "-" if minor_units < 0 else ""
    absolute = abs(minor_units)
    major, minor = divmod(absolute, 100)
    return f"{currency} {sign}{major:,}.{minor:02d}"


def format_utc(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
