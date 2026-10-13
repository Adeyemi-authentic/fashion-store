"""The order state machine (docs/ARCHITECTURE.md §3). Every status change goes through here.

`transition()` locks the order row first, so the webhook, the callback check and the expiry
job can't interleave: whichever gets the lock second sees the first one's result and either
makes a valid next move or is refused.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.models import Order

ALLOWED: dict[str, frozenset[str]] = {
    "pending_payment": frozenset({"paid", "cancelled", "expired"}),
    # A late payment: re-reserve worked -> paid; stock gone -> refund.
    "expired": frozenset({"paid", "refund_pending"}),
    # Money arrived for an order that was cancelled first: give it back.
    "cancelled": frozenset({"refund_pending"}),
    "paid": frozenset({"fulfilled", "refund_pending"}),
    "refund_pending": frozenset({"refunded"}),
    "fulfilled": frozenset(),
    "refunded": frozenset(),
}

PAID_OR_LATER = frozenset({"paid", "fulfilled", "refund_pending", "refunded"})


class InvalidTransition(Exception):
    def __init__(self, order_id: uuid.UUID, current: str, wanted: str) -> None:
        super().__init__(f"order {order_id}: {current} -> {wanted} is not allowed")
        self.current = current
        self.wanted = wanted


class OrderNotFound(LookupError):
    pass


async def lock_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    """SELECT ... FOR UPDATE. Held until the caller's transaction ends."""
    order = (
        await session.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(
                populate_existing=True  # never act on a stale copy from earlier in the session
            )
        )
    ).scalar_one_or_none()
    if order is None:
        raise OrderNotFound(order_id)
    return order


async def transition(session: AsyncSession, order: Order, to: str) -> None:
    """Move a locked order to `to`. Doesn't commit. Raises InvalidTransition otherwise."""
    if to not in ALLOWED.get(order.status, frozenset()):
        raise InvalidTransition(order.id, order.status, to)
    order.status = to
    order.updated_at = func.now()  # type: ignore[assignment]
    await session.flush()
