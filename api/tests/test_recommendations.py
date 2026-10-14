import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.modules.catalog.models import Product, Variant
from app.modules.orders import inventory
from app.modules.orders.models import IdempotencyKey, Order, OrderItem
from app.modules.payments.models import PaymentEvent
from app.platform.models import OutboxJob


@pytest.fixture
async def three(session: AsyncSession) -> AsyncIterator[list[tuple[str, Variant]]]:
    """One variant from each of three different visible products, 10 available each.
    Afterwards their orders are deleted and stock put back (like the `stocked` fixture)."""
    rows = (
        await session.execute(
            select(Product.slug, Variant)
            .join(Variant, Variant.product_id == Product.id)
            .where(Product.status == "active")
            .ext(distinct_on(Product.slug))
            .order_by(Product.slug, Variant.sku)
            .offset(40)
            .limit(3)
        )
    ).all()
    saved = {v.id: (v.stock_on_hand, v.stock_reserved) for _, v in rows}
    await session.execute(
        update(Variant).where(Variant.id.in_(saved)).values(stock_on_hand=10, stock_reserved=0)
    )
    await session.commit()
    session.expunge_all()
    yield [(slug, v) for slug, v in rows]
    await session.rollback()
    orders = select(OrderItem.order_id).where(OrderItem.variant_id.in_(saved))
    await session.execute(delete(IdempotencyKey).where(IdempotencyKey.order_id.in_(orders)))
    await session.execute(delete(OutboxJob))
    await session.execute(delete(PaymentEvent))
    await session.execute(delete(Order).where(Order.id.in_(orders)))
    for vid, (on_hand, reserved) in saved.items():
        await session.execute(
            update(Variant)
            .where(Variant.id == vid)
            .values(stock_on_hand=on_hand, stock_reserved=reserved)
        )
    await session.commit()
    async with get_sessionmaker()() as s:
        assert await inventory.reserved_mismatches(s) == []


async def buy(client: AsyncClient, paystack, variants: list[Variant], pay: bool = True) -> None:  # type: ignore[no-untyped-def]
    for v in variants:
        r = await client.post("/cart/items", json={"variant_id": str(v.id), "qty": 1})
        assert r.status_code in (200, 201), r.text
    out = (
        await client.post(
            "/checkout",
            json={"email": "together@example.com"},
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
    ).json()
    if pay:
        paystack.pay(out["reference"])
        status = (await client.get(f"/orders/by-reference/{out['reference']}")).json()["status"]
        assert status == "paid"


async def test_bought_together_counts_paid_orders_only(make_client, paystack, three) -> None:  # type: ignore[no-untyped-def]
    (a_slug, a), (b_slug, b), (c_slug, c) = three
    await buy(make_client(), paystack, [a, b])
    await buy(make_client(), paystack, [a, b])
    await buy(make_client(), paystack, [a, c])  # only once: below the threshold of 2
    await buy(make_client(), paystack, [a, c], pay=False)  # unpaid: doesn't count

    viewer = make_client()
    recs = (await viewer.get(f"/products/{a_slug}/recommendations")).json()
    assert [p["slug"] for p in recs["bought_together"]] == [b_slug]
    # And the other way round: b's page shows a.
    recs_b = (await viewer.get(f"/products/{b_slug}/recommendations")).json()
    assert [p["slug"] for p in recs_b["bought_together"]] == [a_slug]


async def test_no_orders_means_no_bought_together_row(client: AsyncClient) -> None:
    slug = (await client.get("/products", params={"limit": 1})).json()["items"][0]["slug"]
    recs = (await client.get(f"/products/{slug}/recommendations")).json()
    assert recs["bought_together"] == []
    assert len(recs["similar"]) == 4


async def test_similar_items_are_close_visible_in_stock_and_not_itself(
    client: AsyncClient, session: AsyncSession
) -> None:
    slug = "ready-to-wear-shitta-linen-shirt"
    recs = (await client.get(f"/products/{slug}/recommendations")).json()
    similar = recs["similar"]
    assert similar and all(p["in_stock"] and p["slug"] != slug for p in similar)
    assert "linen-shirt" in similar[0]["slug"]  # its nearest neighbour is another linen shirt


async def test_sold_out_products_are_not_recommended(
    client: AsyncClient, session: AsyncSession
) -> None:
    slug = "ready-to-wear-shitta-linen-shirt"
    first = (await client.get(f"/products/{slug}/recommendations")).json()["similar"][0]
    saved = list(
        (
            await session.execute(
                select(Variant.id, Variant.stock_on_hand).where(
                    Variant.product_id == uuid.UUID(first["id"])
                )
            )
        ).all()
    )
    await session.execute(
        update(Variant).where(Variant.id.in_([v for v, _ in saved])).values(stock_on_hand=0)
    )
    await session.commit()
    try:
        from app.platform.cache import catalog_cache

        catalog_cache.clear()
        similar = (await client.get(f"/products/{slug}/recommendations")).json()["similar"]
        assert first["id"] not in {p["id"] for p in similar}
    finally:
        for vid, qty in saved:
            await session.execute(
                update(Variant).where(Variant.id == vid).values(stock_on_hand=qty)
            )
        await session.commit()


async def test_product_without_an_embedding_gets_its_category(
    client: AsyncClient, session: AsyncSession
) -> None:
    product = (
        await session.execute(select(Product).where(Product.slug == "adire-ikoyi-boubou"))
    ).scalar_one()
    saved = (product.embedding, product.embedding_hash)
    product.embedding, product.embedding_hash = None, None
    await session.commit()
    try:
        similar = (await client.get("/products/adire-ikoyi-boubou/recommendations")).json()
        assert similar["similar"] and all(p["category"] == "adire" for p in similar["similar"])
    finally:
        await session.execute(
            update(Product)
            .where(Product.slug == "adire-ikoyi-boubou")
            .values(embedding=saved[0], embedding_hash=saved[1])
        )
        await session.commit()


async def test_unknown_product_is_404(client: AsyncClient) -> None:
    assert (await client.get("/products/no-such-thing/recommendations")).status_code == 404
