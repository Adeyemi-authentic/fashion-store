"""POST /checkout: turn the cart into a pending order with stock held, then start a Paystack
payment. See docs/ARCHITECTURE.md §4.

Idempotency (the client sends an `Idempotency-Key` per checkout attempt):
- first request with a key: claims it (`in_progress`), creates the order, stores the
  response, marks it `done`
- same key and body, already done: the stored response, nothing created twice
- same key while the first is still running: 409 + Retry-After (the client retries and then
  gets the stored response)
- same key, different body: 422 (a client bug; never guess which one was meant)
Keys are scoped per buyer, so two shoppers can't collide or read each other's responses.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.cart.service import CartOwner, checkout_lines
from app.modules.orders import inventory
from app.modules.orders.models import IdempotencyKey, Order, OrderItem
from app.modules.orders.state import lock_order, transition
from app.modules.payments.paystack import PaymentProvider, PaystackError


@dataclass
class CheckoutError(Exception):
    status_code: int
    detail: Any
    headers: dict[str, str] = field(default_factory=dict)


def _request_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


async def _claim(session: AsyncSession, key: str, request_hash: str) -> dict[str, Any] | None:
    """Claim the key, or return the stored response of a finished request with this key.

    INSERT ... ON CONFLICT DO NOTHING: if another request with this key is mid-transaction,
    Postgres makes this INSERT wait until that one commits or rolls back, so exactly one of
    them owns the key.
    """
    claimed = await session.scalar(
        insert(IdempotencyKey)
        .values(key=key, request_hash=request_hash, status="in_progress")
        .on_conflict_do_nothing()
        .returning(IdempotencyKey.key)
    )
    if claimed:
        return None
    row = (
        await session.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))
    ).scalar_one()
    if row.request_hash != request_hash:
        raise CheckoutError(422, "This Idempotency-Key was already used for a different request")
    if row.status == "done" and row.response_body is not None:
        return row.response_body
    raise CheckoutError(409, "This checkout is still being processed", {"Retry-After": "1"})


async def start_checkout(
    session: AsyncSession,
    provider: PaymentProvider,
    *,
    owner: CartOwner,
    customer_id: uuid.UUID | None,
    email: str,
    idempotency_key: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    if owner.ref is None:
        raise CheckoutError(422, "Your bag is empty")
    key = f"{owner.ref}:{idempotency_key}"

    # --- Transaction 1: claim the key, hold stock, create the order -------------------------
    stored = await _claim(session, key, _request_hash(body))
    if stored is not None:
        await session.rollback()
        return stored
    cart_id, lines = await checkout_lines(session, owner)
    if not lines:
        await session.rollback()  # also un-claims the key
        raise CheckoutError(422, "Your bag is empty")
    if any(line.problem for line in lines):
        await session.rollback()
        raise CheckoutError(
            409,
            {
                "code": "bag_needs_attention",
                "message": "Some items in your bag are sold out or no longer available",
                "skus": [line.sku for line in lines if line.problem],
            },
        )

    now: datetime = (await session.execute(select(func.now()))).scalar_one()
    expires_at = now + timedelta(minutes=get_settings().hold_minutes)  # database clock
    order = Order(
        customer_id=customer_id,
        guest_email=None if customer_id else email.lower(),
        cart_id=cart_id,
        status="pending_payment",
        total_kobo=sum(line.unit_price_kobo * line.qty for line in lines),
        paystack_reference=f"ff_{uuid.uuid4().hex}",  # unguessable: the callback page uses it
    )
    session.add(order)
    await session.flush()
    try:
        await inventory.reserve(
            session, order.id, [(line.variant_id, line.qty) for line in lines], expires_at
        )
    except inventory.OutOfStock as exc:
        await session.rollback()
        skus = [line.sku for line in lines if line.variant_id in exc.variant_ids]
        raise CheckoutError(
            409,
            {"code": "out_of_stock", "message": "Some items just sold out", "skus": skus},
        ) from exc
    session.add_all(
        OrderItem(
            order_id=order.id,
            variant_id=line.variant_id,
            qty=line.qty,
            unit_price_kobo=line.unit_price_kobo,  # copied now; never re-read (ADR-004)
            name_snapshot=line.name,
            sku_snapshot=line.sku,
        )
        for line in lines
    )
    await session.execute(
        update(IdempotencyKey).where(IdempotencyKey.key == key).values(order_id=order.id)
    )
    order_id, reference, total = order.id, order.paystack_reference, order.total_kobo
    await session.commit()

    # --- Outside any transaction: Paystack can be slow, and must never hold our locks -------
    try:
        authorization_url = await provider.initialize(
            email=email,
            amount_kobo=total,
            reference=reference,
            callback_url=f"{get_settings().public_site_url}/checkout/callback",
            metadata={"order_id": str(order_id)},
        )
    except PaystackError as exc:
        # Undo, so nothing is left holding stock and the same key can simply be retried.
        locked = await lock_order(session, order_id)
        await inventory.release_order_holds(session, order_id)
        await transition(session, locked, "cancelled")
        await session.execute(delete(IdempotencyKey).where(IdempotencyKey.key == key))
        await session.commit()
        raise CheckoutError(502, "Payments are unavailable right now. Please try again.") from exc

    # --- Transaction 2: remember the answer for retries --------------------------------------
    response = {
        "order_id": str(order_id),
        "reference": reference,
        "authorization_url": authorization_url,
        "total_kobo": total,
        "expires_at": expires_at.isoformat(),
    }
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.key == key)
        .values(status="done", response_body=response)
    )
    await session.commit()
    return response
