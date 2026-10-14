import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product, Variant
from app.platform.cache import TTLCache, catalog_cache
from tests.conftest import paid_order, sign_up


def cache_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.cache"]


async def test_second_read_is_a_cache_hit_in_the_logs(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """PLAN 'done when': a cache hit shows up in the logs."""
    caplog.set_level(logging.INFO, logger="app.cache")
    first = await client.get("/products", params={"category": "adire"})
    second = await client.get("/products", params={"category": "adire"})
    assert first.json() == second.json()
    assert cache_lines(caplog) == ["cache miss", "cache hit"]
    hit = next(r for r in caplog.records if r.getMessage() == "cache hit")
    assert hit.cache == "catalog" and "adire" in hit.key  # type: ignore[attr-defined]


async def test_admin_edit_is_visible_at_once(
    client: AsyncClient, make_client, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    admin = make_client()
    await sign_up(admin, role="admin")
    product = await session.scalar(
        select(Product).where(Product.status == "active").order_by(Product.slug).limit(1)
    )
    assert product is not None
    slug, old_name, pid = product.slug, product.name, product.id
    assert (await client.get(f"/products/{slug}")).json()["name"] == old_name  # now cached
    try:
        await admin.patch(f"/admin/products/{pid}", json={"name": "Renamed In Admin"})
        assert (await client.get(f"/products/{slug}")).json()["name"] == "Renamed In Admin"
    finally:
        await admin.patch(f"/admin/products/{pid}", json={"name": old_name})


async def test_stock_endpoint_is_live_while_the_page_is_cached(
    client: AsyncClient, make_client, stocked: list[Variant], paystack, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked
    slug = await session.scalar(select(Product.slug).where(Product.id == a.product_id))

    def available(rows: list[dict]) -> int:
        return next(r["available"] for r in rows if r["variant_id"] == str(a.id))

    await client.get(f"/products/{slug}")  # cached with 5 available
    assert available((await client.get(f"/products/{slug}/stock")).json()) == 5
    await paid_order(make_client(), paystack, a, qty=2)
    assert available((await client.get(f"/products/{slug}/stock")).json()) == 3  # live
    cached = (await client.get(f"/products/{slug}")).json()["variants"]
    assert next(v["available"] for v in cached if v["id"] == str(a.id)) == 5  # cached page


async def test_entries_expire_and_the_cache_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.platform.cache as module

    clock = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    cache = TTLCache("t", ttl_seconds=60, max_entries=2)
    loads: list[str] = []

    async def load(k: str) -> str:
        loads.append(k)
        return k

    await cache.get_or_load("a", lambda: load("a"))
    await cache.get_or_load("a", lambda: load("a"))
    clock[0] += 61
    await cache.get_or_load("a", lambda: load("a"))
    assert loads == ["a", "a"]  # reloaded after the TTL
    await cache.get_or_load("b", lambda: load("b"))
    await cache.get_or_load("c", lambda: load("c"))
    assert len(cache) == 2  # oldest dropped
    assert catalog_cache.ttl == 60
