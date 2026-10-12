from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product
from app.shared.pagination import encode_cursor


async def fetch_all(client: AsyncClient, **params: object) -> list[dict]:
    """Follow next_cursor until the end and return every item."""
    items: list[dict] = []
    cursor = None
    for _ in range(100):  # guard against a cursor loop
        query = {**params, **({"cursor": cursor} if cursor else {})}
        resp = await client.get("/products", params=query)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("pagination did not finish")


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.json() == {"status": "ok"}


async def test_first_page_shape(client: AsyncClient) -> None:
    resp = await client.get("/products")
    body = resp.json()
    assert len(body["items"]) == 24
    assert body["next_cursor"]
    card = body["items"][0]
    assert set(card) == {"id", "slug", "name", "category", "price_kobo", "in_stock", "image"}
    assert isinstance(card["price_kobo"], int)  # kobo, never a float (ADR-004)
    assert card["image"]["alt"].endswith("(AI-generated image)")


@pytest.mark.parametrize("sort", ["newest", "price_asc", "price_desc", "name"])
async def test_pagination_walks_every_product_once_in_order(
    client: AsyncClient, seeded: int, sort: str
) -> None:
    items = await fetch_all(client, sort=sort, limit=17)  # odd size: pages don't line up
    ids = [i["id"] for i in items]
    assert len(ids) == seeded
    assert len(set(ids)) == seeded

    if sort == "price_asc":
        prices = [i["price_kobo"] for i in items]
        assert prices == sorted(prices)
    elif sort == "price_desc":
        prices = [i["price_kobo"] for i in items]
        assert prices == sorted(prices, reverse=True)

    # Small pages must give exactly the same order as big pages: the keyset condition and
    # ORDER BY agree (this also covers name sorting, whose order depends on the DB collation).
    big_pages = await fetch_all(client, sort=sort, limit=60)
    assert ids == [i["id"] for i in big_pages]


async def test_newest_sort_is_by_created_at(client: AsyncClient, session: AsyncSession) -> None:
    items = await fetch_all(client, sort="newest", limit=50)
    created = dict((await session.execute(select(Product.slug, Product.created_at))).all())
    times = [created[i["slug"]] for i in items]
    assert times == sorted(times, reverse=True)


async def test_category_filter(client: AsyncClient) -> None:
    items = await fetch_all(client, category="adire", limit=60)
    assert len(items) == 24
    assert {i["category"] for i in items} == {"adire"}


async def test_price_filter_in_kobo(client: AsyncClient) -> None:
    items = await fetch_all(client, min_price_kobo=2_000_000, max_price_kobo=5_000_000)
    assert items
    assert all(2_000_000 <= i["price_kobo"] <= 5_000_000 for i in items)


async def test_size_and_colour_filters(client: AsyncClient) -> None:
    items = await fetch_all(client, category="adire", colour="deep indigo", size="M")
    assert items
    for item in items:
        detail = (await client.get(f"/products/{item['slug']}")).json()
        assert any(v["colour"] == "Deep Indigo" and v["size"] == "M" for v in detail["variants"])


async def test_in_stock_filter_splits_the_catalog(client: AsyncClient, seeded: int) -> None:
    in_stock = await fetch_all(client, in_stock="true")
    sold_out = await fetch_all(client, in_stock="false")
    assert all(i["in_stock"] for i in in_stock)
    assert not any(i["in_stock"] for i in sold_out)
    assert sold_out, "seed should include some sold-out products"
    assert len(in_stock) + len(sold_out) == seeded


async def test_filters_and_cursor_together(client: AsyncClient) -> None:
    unpaged = (await client.get("/products", params={"category": "ankara", "limit": 60})).json()
    paged = await fetch_all(client, category="ankara", limit=5)
    assert [i["id"] for i in paged] == [i["id"] for i in unpaged["items"]]


async def test_bad_cursor_is_400(client: AsyncClient) -> None:
    resp = await client.get("/products", params={"cursor": "not-a-cursor"})
    assert resp.status_code == 400


async def test_cursor_from_other_sort_is_400(client: AsyncClient) -> None:
    first = (await client.get("/products", params={"sort": "price_asc"})).json()
    resp = await client.get("/products", params={"sort": "name", "cursor": first["next_cursor"]})
    assert resp.status_code == 400
    assert "different sort" in resp.json()["detail"]


async def test_tampered_cursor_value_is_400_not_500(client: AsyncClient) -> None:
    cursor = encode_cursor("newest", "not-a-date", "also-not-a-uuid")
    resp = await client.get("/products", params={"sort": "newest", "cursor": cursor})
    assert resp.status_code == 400


async def test_limit_is_bounded(client: AsyncClient) -> None:
    assert (await client.get("/products", params={"limit": 0})).status_code == 422
    assert (await client.get("/products", params={"limit": 61})).status_code == 422


async def test_product_detail(client: AsyncClient) -> None:
    slug = (await client.get("/products", params={"in_stock": "true"})).json()["items"][0]["slug"]
    detail = (await client.get(f"/products/{slug}")).json()
    assert detail["slug"] == slug
    assert detail["variants"]
    assert detail["price_kobo"] == min(v["price_kobo"] for v in detail["variants"])
    assert detail["in_stock"] == any(v["available"] > 0 for v in detail["variants"])
    assert {"fabric", "occasion", "gender"} <= set(detail["attributes"])


async def test_unknown_product_is_404(client: AsyncClient) -> None:
    assert (await client.get("/products/no-such-thing")).status_code == 404


async def test_draft_products_are_hidden(client: AsyncClient, session: AsyncSession) -> None:
    slug = (await client.get("/products")).json()["items"][0]["slug"]
    await session.execute(update(Product).where(Product.slug == slug).values(status="draft"))
    await session.commit()
    try:
        assert (await client.get(f"/products/{slug}")).status_code == 404
        listed = await fetch_all(client, limit=60)
        assert slug not in {i["slug"] for i in listed}
    finally:
        await session.execute(update(Product).where(Product.slug == slug).values(status="active"))
        await session.commit()


async def test_categories(client: AsyncClient, seeded: int) -> None:
    cats = (await client.get("/categories")).json()
    assert len(cats) == 7
    assert sum(c["product_count"] for c in cats) == seeded


def test_cursor_round_trip() -> None:
    from app.shared.pagination import decode_cursor

    when = datetime(2026, 1, 1).isoformat()
    assert decode_cursor(encode_cursor("newest", when, "abc"), "newest") == (when, "abc")
