"""Read-only views of orders for customers (callback page, account page)."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.models import Order, OrderItem


@dataclass(frozen=True)
class Line:
    name: str
    sku: str
    qty: int
    unit_price_kobo: int


@dataclass(frozen=True)
class Receipt:
    reference: str
    status: str
    total_kobo: int
    created_at: datetime
    items: list[Line]


def _customer_status(order: Order) -> str:
    # A flagged payment is a problem for us, not something to show as "pending" forever.
    return (
        "under_review" if order.needs_review and order.status == "pending_payment" else order.status
    )


async def _with_items(session: AsyncSession, orders: list[Order]) -> list[Receipt]:
    rows = await session.execute(
        select(OrderItem)
        .where(OrderItem.order_id.in_([o.id for o in orders]))
        .order_by(OrderItem.sku_snapshot)
    )
    items: dict[uuid.UUID, list[Line]] = defaultdict(list)
    for i in rows.scalars():
        items[i.order_id].append(Line(i.name_snapshot, i.sku_snapshot, i.qty, i.unit_price_kobo))
    return [
        Receipt(o.paystack_reference, _customer_status(o), o.total_kobo, o.created_at, items[o.id])
        for o in orders
    ]


async def find(session: AsyncSession, reference: str) -> Receipt | None:
    order = (
        await session.execute(
            select(Order)
            .where(Order.paystack_reference == reference)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return (await _with_items(session, [order]))[0] if order else None


async def for_customer(
    session: AsyncSession, customer_id: uuid.UUID, limit: int = 50
) -> list[Receipt]:
    orders = list(
        (
            await session.execute(
                select(Order)
                .where(Order.customer_id == customer_id)
                .order_by(Order.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return await _with_items(session, orders) if orders else []
