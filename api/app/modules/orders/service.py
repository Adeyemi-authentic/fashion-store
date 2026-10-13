"""Public interface of the orders module. Other modules import from here only (ADR-001)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.inventory import (
    StockBelowReserved,
    StockLevel,
    VariantNotFound,
    adjust_stock,
    available_qty,
)
from app.modules.orders.models import ORDER_STATUSES, Order, OrderItem
from app.shared.pagination import InvalidCursor, decode_cursor, encode_cursor

__all__ = [
    "ORDER_STATUSES",
    "OrderRow",
    "StockBelowReserved",
    "StockLevel",
    "VariantNotFound",
    "adjust_stock",
    "available_qty",
    "list_orders",
]

_SORT = "orders_newest"


@dataclass(frozen=True)
class OrderRow:
    id: uuid.UUID
    status: str
    total_kobo: int
    currency: str
    customer_id: uuid.UUID | None
    guest_email: str | None
    item_count: int
    needs_review: bool
    created_at: datetime


async def list_orders(
    session: AsyncSession, status: str | None = None, limit: int = 50, cursor: str | None = None
) -> tuple[list[OrderRow], str | None]:
    """Newest first, keyset-paginated on (created_at, id)."""
    items = (
        select(OrderItem.order_id, func.sum(OrderItem.qty).label("n"))
        .group_by(OrderItem.order_id)
        .subquery()
    )
    stmt = select(Order, func.coalesce(items.c.n, 0)).outerjoin(items, items.c.order_id == Order.id)
    if status:
        stmt = stmt.where(Order.status == status)
    if cursor:
        raw, raw_id = decode_cursor(cursor, _SORT)
        try:
            when, last_id = datetime.fromisoformat(str(raw)), uuid.UUID(raw_id)
        except (TypeError, ValueError) as exc:
            raise InvalidCursor("cursor is not valid") from exc
        stmt = stmt.where(
            or_(Order.created_at < when, and_(Order.created_at == when, Order.id < last_id))
        )
    rows = (
        await session.execute(
            stmt.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    result = [
        OrderRow(
            id=o.id,
            status=o.status,
            total_kobo=o.total_kobo,
            currency=o.currency,
            customer_id=o.customer_id,
            guest_email=o.guest_email,
            item_count=int(n),
            needs_review=o.needs_review,
            created_at=o.created_at,
        )
        for o, n in rows
    ]
    last = rows[-1][0] if rows else None
    next_cursor = (
        encode_cursor(_SORT, last.created_at.isoformat(), str(last.id))
        if has_more and last
        else None
    )
    return result, next_cursor
