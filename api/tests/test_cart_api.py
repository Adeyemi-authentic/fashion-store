import asyncio
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cart import service as cart_service
from app.modules.cart.models import Cart
from app.modules.catalog.models import Product, Variant


@pytest.fixture
async def variant(session: AsyncSession) -> AsyncIterator[Variant]:
    """An in-stock variant of an active product with exactly 3 units available.
    Its stock is put back afterwards."""
    v = (
        await session.execute(
            select(Variant)
            .join(Product)
            .where(Product.status == "active", Variant.stock_on_hand > Variant.stock_reserved)
            .order_by(Variant.sku)
            .limit(1)
        )
    ).scalar_one()
    original = v.stock_on_hand
    await set_available(session, v, 3)
    yield v
    await session.execute(update(Variant).where(Variant.id == v.id).values(stock_on_hand=original))
    await session.commit()


async def set_available(session: AsyncSession, v: Variant, n: int) -> None:
    await session.execute(
        update(Variant).where(Variant.id == v.id).values(stock_on_hand=Variant.stock_reserved + n)
    )
    await session.commit()


async def add(client: AsyncClient, v: Variant, qty: int = 1):  # type: ignore[no-untyped-def]
    return await client.post("/cart/items", json={"variant_id": str(v.id), "qty": qty})


async def test_empty_cart_without_cookie_writes_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    before = await session.scalar(select(func.count()).select_from(Cart))
    resp = await client.get("/cart")
    assert resp.json() == {"items": [], "item_count": 0, "subtotal_kobo": 0, "can_checkout": False}
    assert "set-cookie" not in resp.headers
    assert await session.scalar(select(func.count()).select_from(Cart)) == before


async def test_add_sets_first_party_cookie_and_stores_only_a_hash(
    client: AsyncClient, session: AsyncSession, variant: Variant
) -> None:
    resp = await add(client, variant)
    assert resp.status_code == 200, resp.text
    cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    token = client.cookies["cart_token"]
    assert await session.scalar(select(Cart).where(Cart.session_token == token)) is None

    body = resp.json()
    assert body["item_count"] == 1
    line = body["items"][0]
    assert line["sku"] == variant.sku
    assert line["line_total_kobo"] == variant.price_kobo
    assert body["subtotal_kobo"] == variant.price_kobo
    assert body["can_checkout"] is True


async def test_adding_twice_increments_one_line(client: AsyncClient, variant: Variant) -> None:
    await add(client, variant)
    body = (await add(client, variant, 2)).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["qty"] == 3
    assert (await client.get("/cart")).json() == body


async def test_cannot_add_more_than_available(client: AsyncClient, variant: Variant) -> None:
    await add(client, variant, 2)
    resp = await add(client, variant, 2)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "code": "not_enough_stock",
        "available": 3,
        "in_cart": 2,
        "message": "Only 3 left",
    }
    assert (await client.get("/cart")).json()["items"][0]["qty"] == 2


async def test_concurrent_adds_cannot_both_take_the_last_unit(
    client: AsyncClient, session: AsyncSession, variant: Variant, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add(client, variant)  # creates the cart; both racers share its cookie
    await set_available(session, variant, 2)  # one more unit left for the two racers

    # Widen the gap between reading the line's qty and writing it, so both requests are
    # inside it at once. Without the cart row lock, both read qty=1 and both write qty=2.
    read_qty = cart_service._current_qty

    async def slow_read_qty(*args):  # type: ignore[no-untyped-def]
        qty = await read_qty(*args)
        await asyncio.sleep(0.2)
        return qty

    monkeypatch.setattr(cart_service, "_current_qty", slow_read_qty)
    results = await asyncio.gather(add(client, variant), add(client, variant))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await client.get("/cart")).json()["items"][0]["qty"] == 2


async def test_set_qty_and_remove(client: AsyncClient, variant: Variant) -> None:
    await add(client, variant)
    url = f"/cart/items/{variant.id}"
    assert (await client.patch(url, json={"qty": 3})).json()["item_count"] == 3
    assert (await client.patch(url, json={"qty": 4})).status_code == 409
    assert (await client.delete(url)).json()["items"] == []
    assert (await client.patch(url, json={"qty": 1})).status_code == 404


async def test_stock_drop_flags_line_but_lowering_still_works(
    client: AsyncClient, session: AsyncSession, variant: Variant
) -> None:
    await add(client, variant, 3)
    await set_available(session, variant, 1)
    body = (await client.get("/cart")).json()
    assert body["items"][0]["problem"] == "not_enough_stock"
    assert body["can_checkout"] is False

    body = (await client.patch(f"/cart/items/{variant.id}", json={"qty": 2})).json()
    assert body["items"][0]["qty"] == 2  # still too many, but closer: allowed
    body = (await client.patch(f"/cart/items/{variant.id}", json={"qty": 1})).json()
    assert body["items"][0]["problem"] is None
    assert body["can_checkout"] is True

    await set_available(session, variant, 0)
    assert (await client.get("/cart")).json()["items"][0]["problem"] == "sold_out"


async def test_draft_product_cannot_be_added_and_shows_unavailable(
    client: AsyncClient, session: AsyncSession, variant: Variant
) -> None:
    await add(client, variant)
    product_id = variant.product_id
    await session.execute(update(Product).where(Product.id == product_id).values(status="draft"))
    await session.commit()
    try:
        assert (await add(client, variant)).status_code == 404
        line = (await client.get("/cart")).json()["items"][0]
        assert line["problem"] == "unavailable"
    finally:
        await session.execute(
            update(Product).where(Product.id == product_id).values(status="active")
        )
        await session.commit()


async def test_unknown_cookie_is_an_empty_cart(client: AsyncClient) -> None:
    client.cookies.set("cart_token", "made-up")
    assert (await client.get("/cart")).json()["items"] == []


async def test_validation(client: AsyncClient, variant: Variant) -> None:
    assert (await add(client, variant, 0)).status_code == 422
    assert (await add(client, variant, 11)).status_code == 422
    resp = await client.post(
        "/cart/items", json={"variant_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert resp.status_code == 404


async def test_facets(client: AsyncClient) -> None:
    all_ = (await client.get("/facets")).json()
    adire = (await client.get("/facets", params={"category": "adire"})).json()
    assert all_["sizes"][:3] == ["XS", "S", "M"]
    assert "One Size" in all_["sizes"]
    assert "Deep Indigo" in adire["colours"] and "Orange/Teal" not in adire["colours"]
    assert adire["min_price_kobo"] <= adire["max_price_kobo"]
