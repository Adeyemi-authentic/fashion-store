"""Stock holds (ADR-003). Every test ends by checking the invariant: each variant's
stock_reserved equals the sum of its unreleased reservations."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.modules.catalog.models import Product, Variant
from app.modules.orders import inventory
from app.modules.orders.models import Order, Reservation

SOON = timedelta(minutes=15)


def later() -> datetime:
    return datetime.now(UTC) + SOON


@pytest.fixture
async def stock(session: AsyncSession) -> AsyncIterator[list[Variant]]:
    """Three variants of active products, each set to 10 on hand and nothing reserved.
    Orders made by the test are deleted and stock put back afterwards."""
    variants = list(
        (
            await session.execute(
                select(Variant)
                .join(Product)
                .where(Product.status == "active")
                .order_by(Variant.sku)
                .offset(40)
                .limit(3)
            )
        ).scalars()
    )
    saved = {v.id: (v.stock_on_hand, v.stock_reserved) for v in variants}
    session.expunge_all()  # detached: a rollback in a test won't expire their attributes
    await session.execute(
        update(Variant).where(Variant.id.in_(saved)).values(stock_on_hand=10, stock_reserved=0)
    )
    await session.commit()
    yield variants
    await session.rollback()
    order_ids = select(Reservation.order_id).where(Reservation.variant_id.in_(saved))
    await session.execute(delete(Order).where(Order.id.in_(order_ids)))  # reservations cascade
    for vid, (on_hand, reserved) in saved.items():
        await session.execute(
            update(Variant)
            .where(Variant.id == vid)
            .values(stock_on_hand=on_hand, stock_reserved=reserved)
        )
    await session.commit()
    async with get_sessionmaker()() as s:
        assert await inventory.reserved_mismatches(s) == []


async def new_order(session: AsyncSession) -> uuid.UUID:
    order = Order(
        guest_email="inv@example.com", total_kobo=100, paystack_reference=f"t-{uuid.uuid4()}"
    )
    session.add(order)
    await session.flush()
    return order.id


async def levels(session: AsyncSession, v: Variant) -> tuple[int, int, int]:
    """(on_hand, reserved counter, available as shoppers see it)"""
    row = (
        await session.execute(
            select(Variant.stock_on_hand, Variant.stock_reserved, inventory.available_qty()).where(
                Variant.id == v.id
            )
        )
    ).one()
    return tuple(row)  # type: ignore[return-value]


async def test_reserve_is_all_or_nothing(session: AsyncSession, stock: list[Variant]) -> None:
    a, b, _ = stock
    order_id = await new_order(session)
    with pytest.raises(inventory.OutOfStock) as err:
        await inventory.reserve(session, order_id, [(a.id, 2), (b.id, 11)], later())
    assert err.value.variant_ids == [b.id]
    await session.rollback()
    assert await levels(session, a) == (10, 0, 10)  # a's 2 units were not kept


async def test_same_variant_twice_is_one_hold(session: AsyncSession, stock: list[Variant]) -> None:
    a, _, _ = stock
    order_id = await new_order(session)
    await inventory.reserve(session, order_id, [(a.id, 2), (a.id, 3)], later())
    await session.commit()
    assert await levels(session, a) == (10, 5, 5)
    count = await session.scalar(select(Reservation.qty).where(Reservation.order_id == order_id))
    assert count == 5


async def test_thirty_buyers_ten_units_exactly_ten_holds(stock: list[Variant]) -> None:
    a = stock[0]

    async def buyer() -> bool:
        async with get_sessionmaker()() as s:
            order_id = await new_order(s)
            try:
                await inventory.reserve(s, order_id, [(a.id, 1)], later())
                await s.commit()
                return True
            except inventory.OutOfStock:
                await s.rollback()
                return False

    results = await asyncio.gather(*(buyer() for _ in range(30)))
    assert results.count(True) == 10
    async with get_sessionmaker()() as s:
        assert await levels(s, a) == (10, 10, 0)
        assert await inventory.reserved_mismatches(s) == []


async def test_opposite_item_order_does_not_deadlock(stock: list[Variant]) -> None:
    """Carts [a, b] and [b, a] checking out together many times. Locks are always taken in
    variant-id order, so Postgres never has to abort one with a deadlock error."""
    a, b, _ = stock

    async def buyer(items: list[tuple[uuid.UUID, int]]) -> None:
        async with get_sessionmaker()() as s:
            order_id = await new_order(s)
            try:
                await inventory.reserve(s, order_id, items, later())
                await s.commit()
            except inventory.OutOfStock:
                await s.rollback()

    await asyncio.gather(
        *(buyer([(a.id, 1), (b.id, 1)] if i % 2 else [(b.id, 1), (a.id, 1)]) for i in range(16))
    )
    async with get_sessionmaker()() as s:
        assert (await levels(s, a))[1] == (await levels(s, b))[1] == 10
        assert await inventory.reserved_mismatches(s) == []


async def expire_holds(session: AsyncSession, order_id: uuid.UUID) -> None:
    await session.execute(
        update(Reservation)
        .where(Reservation.order_id == order_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


async def test_expired_hold_counts_as_stock_with_nothing_releasing_it(
    session: AsyncSession, client: AsyncClient, stock: list[Variant]
) -> None:
    """The worker is 'stopped': no job ever runs. Once the hold passes expires_at, shoppers
    see the stock again, because availability ignores expired holds at read time."""
    a = stock[0]
    order_id = await new_order(session)
    await inventory.reserve(session, order_id, [(a.id, 10)], later())
    await session.commit()
    slug = await session.scalar(select(Product.slug).where(Product.id == a.product_id))
    # The live endpoint the product page uses (the product detail itself is cached).
    available = {
        v["variant_id"]: v["available"]
        for v in (await client.get(f"/products/{slug}/stock")).json()
    }
    assert available[str(a.id)] == 0

    await expire_holds(session, order_id)
    assert await levels(session, a) == (10, 10, 10)  # counter not tidied, but shoppers see 10
    # The live endpoint the product page uses (the product detail itself is cached).
    available = {
        v["variant_id"]: v["available"]
        for v in (await client.get(f"/products/{slug}/stock")).json()
    }
    assert available[str(a.id)] == 10
    assert (
        await client.post("/cart/items", json={"variant_id": str(a.id), "qty": 10})
    ).status_code == 200

    # The next checkout on that variant tidies the expired hold as it goes.
    other = await new_order(session)
    await inventory.reserve(session, other, [(a.id, 4)], later())
    await session.commit()
    assert await levels(session, a) == (10, 4, 6)
    assert await inventory.reserved_mismatches(session) == []


async def test_paid_in_time_converts_the_hold(session: AsyncSession, stock: list[Variant]) -> None:
    a, b, _ = stock
    order_id = await new_order(session)
    items = [(a.id, 2), (b.id, 1)]
    await inventory.reserve(session, order_id, items, later())
    await session.commit()
    assert await inventory.convert_holds_to_sale(session, order_id, items) is True
    await session.commit()
    assert await levels(session, a) == (8, 0, 8)
    assert await levels(session, b) == (9, 0, 9)


async def test_expired_hold_is_not_converted(session: AsyncSession, stock: list[Variant]) -> None:
    a = stock[0]
    order_id = await new_order(session)
    await inventory.reserve(session, order_id, [(a.id, 2)], later())
    await session.commit()
    await expire_holds(session, order_id)
    assert await inventory.convert_holds_to_sale(session, order_id, [(a.id, 2)]) is False
    await session.rollback()
    assert await levels(session, a) == (10, 2, 10)  # untouched (and the 2 already count as free)


async def test_late_sale_takes_free_stock_or_nothing(
    session: AsyncSession, stock: list[Variant]
) -> None:
    a, b, _ = stock
    late = await new_order(session)
    await inventory.reserve(session, late, [(a.id, 3), (b.id, 1)], later())
    await session.commit()
    await expire_holds(session, late)

    # Someone else holds 9 of b in the meantime, leaving 1: the late order can still have it.
    other = await new_order(session)
    await inventory.reserve(session, other, [(b.id, 9)], later())
    await session.commit()
    await inventory.release_order_holds(session, late)
    assert await inventory.sell_without_hold(session, [(a.id, 3), (b.id, 1)]) is True
    await session.commit()
    assert await levels(session, a) == (7, 0, 7)
    assert await levels(session, b) == (9, 9, 0)

    # Now b is gone: a second late sale must change nothing at all, not even a.
    assert await inventory.sell_without_hold(session, [(a.id, 1), (b.id, 1)]) is False
    await session.commit()
    assert await levels(session, a) == (7, 0, 7)
    assert await levels(session, b) == (9, 9, 0)
