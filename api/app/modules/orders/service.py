"""Public interface of the orders module. Other modules import from here only (ADR-001)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.availability import available_qty
from app.modules.orders.inventory import (
    StockBelowReserved,
    StockLevel,
    VariantNotFound,
    adjust_stock,
)
from app.modules.orders.models import ORDER_STATUSES, Order, OrderItem
from app.modules.orders.payment import (
    Outcome,
    apply_refund_event,
    apply_successful_charge,
    cancel_order,
    expire_overdue_orders,
)
from app.modules.orders.state import InvalidTransition, OrderNotFound
from app.shared.pagination import InvalidCursor, decode_cursor, encode_cursor

__all__ = [
    "ORDER_STATUSES",
    "delete_old_idempotency_keys",
    "apply_refund_event",
    "InvalidTransition",
    "OrderNotFound",
    "cancel_order",
    "order_id_for_reference",
    "Outcome",
    "apply_successful_charge",
    "expire_overdue_orders",
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


async def order_id_for_reference(session: AsyncSession, reference: str) -> uuid.UUID | None:
    return await session.scalar(select(Order.id).where(Order.paystack_reference == reference))


async def delete_old_idempotency_keys(session: AsyncSession, older_than: timedelta) -> int:
    """A retry after a day is a new checkout, not a repeat. Also clears keys stuck
    'in_progress' by a crash between commit and Paystack. Doesn't commit."""
    from sqlalchemy import delete

    from app.modules.orders.models import IdempotencyKey

    result = await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.created_at < func.now() - older_than)
    )
    return result.rowcount  # type: ignore[attr-defined,no-any-return]


# Orders that count as a real purchase for "frequently bought together".
BOUGHT = ("paid", "fulfilled")


async def bought_together(
    session: AsyncSession, product_id: uuid.UUID, min_orders: int = 2, limit: int = 8
) -> list[tuple[uuid.UUID, int]]:
    """Other products that appear in the same paid orders as this one, with how many
    orders they shared, most first. `min_orders` keeps one shopper's odd basket from
    becoming a recommendation for everyone."""
    from app.modules.catalog.models import Variant

    with_product = (
        select(OrderItem.order_id)
        .join(Variant, Variant.id == OrderItem.variant_id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(Variant.product_id == product_id, Order.status.in_(BOUGHT))
    )
    shared = func.count(func.distinct(OrderItem.order_id))
    rows = await session.execute(
        select(Variant.product_id, shared)
        .join(Variant, Variant.id == OrderItem.variant_id)
        .where(OrderItem.order_id.in_(with_product), Variant.product_id != product_id)
        .group_by(Variant.product_id)
        .having(shared >= min_orders)
        .order_by(shared.desc(), Variant.product_id)
        .limit(limit)
    )
    return [(pid, int(n)) for pid, n in rows]
