"""Delete what the Playwright tests create on the real (Neon) database: accounts
e2e-*@example.com, products named "E2E …", and orders placed by e2e buyers. Stock those
orders took is given back (sold units returned to the shelf, holds released) first, so the
catalog ends exactly where it started."""

import asyncio

from sqlalchemy import delete, or_, select, update

from app.db import dispose_engine, get_sessionmaker
from app.modules.accounts.models import Customer
from app.modules.catalog.models import Product, Variant
from app.modules.orders import inventory
from app.modules.orders.models import IdempotencyKey, Order, OrderItem
from app.modules.payments.models import PaymentEvent
from app.platform.models import OutboxJob

E2E_EMAIL = "e2e-%@example.com"
SOLD = ("paid", "fulfilled")  # stock already left the shelf for these


async def main() -> None:
    async with get_sessionmaker()() as session:
        e2e_customers = select(Customer.id).where(Customer.email.like(E2E_EMAIL))
        orders = list(
            (
                await session.execute(
                    select(Order)
                    .where(
                        or_(Order.guest_email.like(E2E_EMAIL), Order.customer_id.in_(e2e_customers))
                    )
                    # Locked, so two clean-ups at once can't both return the same order's stock:
                    # the second waits, then no longer sees the orders the first deleted.
                    .with_for_update()
                )
            ).scalars()
        )
        for order in orders:
            await inventory.release_order_holds(session, order.id)
            if order.status in SOLD:
                items = await session.execute(
                    select(OrderItem.variant_id, OrderItem.qty).where(
                        OrderItem.order_id == order.id
                    )
                )
                for variant_id, qty in items:
                    await session.execute(
                        update(Variant)
                        .where(Variant.id == variant_id)
                        .values(stock_on_hand=Variant.stock_on_hand + qty)
                    )
        ids = [o.id for o in orders]
        refs = [str(i) for i in ids]
        if ids:
            await session.execute(delete(IdempotencyKey).where(IdempotencyKey.order_id.in_(ids)))
            await session.execute(delete(PaymentEvent).where(PaymentEvent.order_id.in_(ids)))
            await session.execute(
                delete(OutboxJob).where(OutboxJob.payload["order_id"].astext.in_(refs))
            )
            await session.execute(delete(Order).where(Order.id.in_(ids)))
        products = await session.execute(delete(Product).where(Product.name.like("E2E %")))
        customers = await session.execute(delete(Customer).where(Customer.email.like(E2E_EMAIL)))
        mismatches = await inventory.reserved_mismatches(session)
        await session.commit()
    await dispose_engine()
    print(
        f"removed {len(ids)} orders (stock returned), {products.rowcount} products, "  # type: ignore[attr-defined]
        f"{customers.rowcount} accounts; reserved-counter mismatches: {len(mismatches)}"  # type: ignore[attr-defined]
    )


if __name__ == "__main__":
    asyncio.run(main())
