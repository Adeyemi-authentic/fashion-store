"""What happens when Paystack says a charge succeeded. One function, used by both the webhook
and the callback check, so it doesn't matter which arrives first or how often (ADR-003 §4-5).
"""

import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cart.service import remove_purchased
from app.modules.orders import inventory
from app.modules.orders.models import Order, OrderItem
from app.modules.orders.state import PAID_OR_LATER, lock_order, transition
from app.modules.payments.paystack import Charge
from app.platform.outbox import enqueue

Outcome = Literal[
    "paid", "paid_late", "already_handled", "needs_review", "refund_pending", "unknown_order"
]


async def _items(session: AsyncSession, order_id: uuid.UUID) -> list[tuple[uuid.UUID, int]]:
    rows = await session.execute(
        select(OrderItem.variant_id, OrderItem.qty).where(OrderItem.order_id == order_id)
    )
    return [(v, q) for v, q in rows]


LOW_STOCK = 3  # units left at which the admin is told


async def _after_paid(
    session: AsyncSession, order: Order, items: list[tuple[uuid.UUID, int]]
) -> None:
    if order.cart_id:
        await remove_purchased(session, order.cart_id, items)
    await enqueue(session, "order_confirmation_email", {"order_id": str(order.id)})
    # Alert once, on the sale that crosses the line, not on every sale below it.
    for variant_id, available, qty in await inventory.levels_after_sale(session, items):
        if available <= LOW_STOCK < available + qty:
            await enqueue(
                session, "low_stock_alert", {"variant_id": str(variant_id), "available": available}
            )


async def _refund(session: AsyncSession, order: Order, charge: Charge, reason: str) -> None:
    await transition(session, order, "refund_pending")
    payload = {"order_id": str(order.id), "reference": charge.reference}
    await enqueue(
        session,
        "refund_payment",
        {**payload, "transaction_id": charge.transaction_id, "amount_kobo": charge.amount_kobo},
    )
    await enqueue(session, "late_payment_apology_email", payload)
    await enqueue(session, "admin_alert", {**payload, "reason": reason})


async def apply_successful_charge(session: AsyncSession, charge: Charge) -> Outcome:
    """Idempotent; doesn't commit. The caller has already checked the charge is genuine
    (webhook signature, or our own call to Paystack's verify API)."""
    order_id = await session.scalar(
        select(Order.id).where(Order.paystack_reference == charge.reference)
    )
    if order_id is None:
        return "unknown_order"
    # Everything below runs with the order row locked: a webhook and a callback check for the
    # same order take turns, and the second one sees the first one's result.
    order = await lock_order(session, order_id)
    if order.status in PAID_OR_LATER:
        return "already_handled"

    if charge.amount_kobo != order.total_kobo or charge.currency != order.currency:
        # Don't fulfil, don't reject (a 4xx makes Paystack retry forever): flag for a human.
        order.needs_review = True
        order.review_reason = (
            f"Charged {charge.amount_kobo} {charge.currency}, "
            f"order total is {order.total_kobo} {order.currency}"
        )
        order.updated_at = func.now()  # type: ignore[assignment]
        await enqueue(
            session,
            "admin_alert",
            {
                "order_id": str(order.id),
                "reference": charge.reference,
                "reason": order.review_reason,
            },
        )
        return "needs_review"

    items = await _items(session, order.id)

    if order.status == "pending_payment":
        if await inventory.convert_holds_to_sale(session, order.id, items):
            await transition(session, order, "paid")
            await _after_paid(session, order, items)
            return "paid"
        # Paid after the hold ran out, and nothing has marked the order expired yet (the worker
        # may be asleep). Expire it now, then treat it like any late payment.
        await inventory.release_order_holds(session, order.id)
        await transition(session, order, "expired")

    if order.status == "cancelled":
        await _refund(session, order, charge, "payment arrived for a cancelled order")
        return "refund_pending"

    # status == "expired": a late payment. Sell from free stock if every item is still there.
    await inventory.release_order_holds(session, order.id)  # leftovers, if any
    if await inventory.sell_without_hold(session, items):
        await transition(session, order, "paid")
        await _after_paid(session, order, items)
        return "paid_late"
    await _refund(session, order, charge, "late payment and the stock is gone")
    return "refund_pending"


async def expire_overdue_orders(session: AsyncSession, limit: int = 100) -> int:
    """Tidy-up job (Phase 5 schedules it): pending orders whose holds have run out become
    `expired` and their holds are released. Correctness never waits for this: availability
    already ignores expired holds. Doesn't commit (the job commits it together with its done
    mark); returns how many orders it expired."""
    from app.modules.orders.models import Reservation

    overdue = (
        select(Order.id)
        .where(
            Order.status == "pending_payment",
            select(Reservation.id)
            .where(
                Reservation.order_id == Order.id,
                Reservation.released_at.is_(None),
                Reservation.expires_at <= func.now(),
            )
            .exists(),
        )
        .limit(limit)
        # Two copies of the job (or a webhook busy with one of these orders) skip each
        # other's rows instead of waiting.
        .with_for_update(skip_locked=True)
    )
    ids = list((await session.execute(overdue)).scalars())
    for order_id in ids:
        order = await lock_order(session, order_id)
        await inventory.release_order_holds(session, order_id)
        await transition(session, order, "expired")
    return len(ids)


async def cancel_order(session: AsyncSession, order_id: uuid.UUID) -> None:
    """Cancel an unpaid order and give its held stock back now. Raises InvalidTransition if
    it isn't pending_payment. If the customer pays anyway, apply_successful_charge refunds
    them (cancelled -> refund_pending). Doesn't commit."""
    order = await lock_order(session, order_id)
    await transition(session, order, "cancelled")  # checks the move first
    await inventory.release_order_holds(session, order.id)


async def apply_refund_event(session: AsyncSession, transaction_reference: str, status: str) -> str:
    """Paystack reports on a refund (webhook). processed -> the order is `refunded`; failed ->
    a human must sort it out. Idempotent; doesn't commit."""
    order_id = await session.scalar(
        select(Order.id).where(Order.paystack_reference == transaction_reference)
    )
    if order_id is None:
        return "unknown_order"
    order = await lock_order(session, order_id)
    if status == "processed":
        if order.status == "refunded":
            return "already_handled"
        if order.status != "refund_pending":
            return "ignored"
        await transition(session, order, "refunded")
        return "refunded"
    if status == "failed":
        order.needs_review = True
        order.review_reason = "Paystack reported the refund failed"
        await enqueue(
            session,
            "admin_alert",
            {"order_id": str(order.id), "reason": "refund failed at Paystack: refund by hand"},
        )
        return "needs_review"
    return "ignored"  # pending / processing: nothing to change yet
