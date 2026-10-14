import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.modules.accounts.models import Customer
from app.modules.catalog.models import Product, Variant
from app.modules.orders.models import Order, OrderItem
from tests.conftest import sign_up

IMAGE = "/images/products/adire-agege-kaftan-dress.webp"

# Every admin endpoint the app exposes, read from its OpenAPI schema (FastAPI's public list
# of routes), so a route added later is covered without editing this test.
ADMIN_ROUTES = sorted(
    (method.upper(), path)
    for path, operations in create_app().openapi()["paths"].items()
    if path.startswith("/admin")
    for method in operations
)


def test_admin_routes_were_found() -> None:
    assert len(ADMIN_ROUTES) >= 10  # guards the parametrised test below against finding none


def _fill(path: str) -> str:
    for name in ("{product_id}", "{variant_id}", "{draft_id}"):
        path = path.replace(name, str(uuid.uuid4()))
    return path


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
async def test_every_admin_route_is_401_for_visitors_and_403_for_customers(
    make_client, method: str, path: str
) -> None:  # type: ignore[no-untyped-def]
    visitor, customer = make_client(), make_client()
    await sign_up(customer)
    # A valid-looking body, so a 403 can't be mistaken for a validation error and vice versa.
    body = {"delta": 1, "price_kobo": 100, "name": "Hack"} if method in ("POST", "PATCH") else None
    assert (await visitor.request(method, _fill(path), json=body)).status_code == 401
    assert (await customer.request(method, _fill(path), json=body)).status_code == 403


@pytest.fixture
async def admin(make_client) -> AsyncClient:  # type: ignore[no-untyped-def]
    client = make_client()
    await sign_up(client, role="admin")
    return client


@pytest.fixture
async def cleanup(session: AsyncSession) -> AsyncIterator[list[str]]:
    """Slugs of products a test creates; deleted afterwards so other tests' counts hold."""
    slugs: list[str] = []
    yield slugs
    if slugs:
        await session.execute(delete(Product).where(Product.slug.in_(slugs)))
        await session.commit()


def new_product(**overrides: object) -> dict:
    return {
        "name": "Test Lagoon Kaftan",
        "category": "adire",
        "description": "Test product.",
        "variants": [
            {"size": "M", "colour": "Deep Indigo", "price_kobo": 4_500_000, "stock_on_hand": 4},
            {"size": "L", "colour": "Deep Indigo", "price_kobo": 4_000_000, "stock_on_hand": 0},
        ],
        **overrides,
    }


async def test_create_draft_then_publish(
    admin: AsyncClient, client: AsyncClient, cleanup: list[str]
) -> None:
    resp = await admin.post("/admin/products", json=new_product())
    assert resp.status_code == 201, resp.text
    created = resp.json()
    cleanup.append(created["slug"])
    assert created["slug"] == "adire-test-lagoon-kaftan"

    product = (await admin.get(f"/admin/products/{created['id']}")).json()
    assert product["status"] == "draft"
    assert product["price_kobo"] == 4_000_000  # lowest variant price
    stock = {v["size"]: v["stock_on_hand"] for v in product["variants"]}
    assert stock == {"M": 4, "L": 0}
    # Drafts are invisible to shoppers.
    assert (await client.get(f"/products/{created['slug']}")).status_code == 404

    # Can't publish without an image.
    no_image = await admin.patch(f"/admin/products/{created['id']}", json={"status": "active"})
    assert no_image.status_code == 422
    assert "image" in no_image.json()["detail"]

    published = await admin.patch(
        f"/admin/products/{created['id']}", json={"status": "active", "image_url": IMAGE}
    )
    assert published.status_code == 200
    shop = await client.get(f"/products/{created['slug']}")
    assert shop.status_code == 200 and shop.json()["in_stock"] is True


async def test_slug_is_unique_and_never_changes(admin: AsyncClient, cleanup: list[str]) -> None:
    first = (await admin.post("/admin/products", json=new_product())).json()
    second = (await admin.post("/admin/products", json=new_product())).json()
    cleanup += [first["slug"], second["slug"]]
    assert second["slug"] == first["slug"] + "-2"
    renamed = await admin.patch(f"/admin/products/{first['id']}", json={"name": "New Name"})
    assert renamed.json()["name"] == "New Name"
    assert renamed.json()["slug"] == first["slug"]


async def test_create_rejects_bad_input(admin: AsyncClient) -> None:
    dupe = new_product(
        variants=[
            {"size": "M", "colour": "Rust", "price_kobo": 100},
            {"size": "m", "colour": "rust", "price_kobo": 200},
        ]
    )
    assert (await admin.post("/admin/products", json=dupe)).status_code == 422
    assert (await admin.post("/admin/products", json=new_product(variants=[]))).status_code == 422
    bad_image = new_product(image_url="https://evil.example/x.png")
    assert (await admin.post("/admin/products", json=bad_image)).status_code == 422
    assert (
        await admin.post("/admin/products", json=new_product(category="Bad Cat"))
    ).status_code == 422


async def test_price_change_updates_from_price(admin: AsyncClient, cleanup: list[str]) -> None:
    created = (await admin.post("/admin/products", json=new_product())).json()
    cleanup.append(created["slug"])
    product = (await admin.get(f"/admin/products/{created['id']}")).json()
    cheap = next(v for v in product["variants"] if v["size"] == "L")
    resp = await admin.patch(f"/admin/variants/{cheap['id']}", json={"price_kobo": 9_000_000})
    assert resp.json()["price_kobo"] == 4_500_000  # M is now the cheapest

    added = await admin.post(
        f"/admin/products/{created['id']}/variants",
        json={"size": "S", "colour": "Rust", "price_kobo": 1_000_000, "stock_on_hand": 2},
    )
    assert added.status_code == 201
    assert added.json()["price_kobo"] == 1_000_000
    same_again = await admin.post(
        f"/admin/products/{created['id']}/variants",
        json={"size": "s", "colour": "RUST", "price_kobo": 1_000_000},
    )
    assert same_again.status_code == 422


async def test_stock_adjustment_and_reserved_floor(
    admin: AsyncClient, session: AsyncSession, cleanup: list[str]
) -> None:
    created = (await admin.post("/admin/products", json=new_product())).json()
    cleanup.append(created["slug"])
    product = (await admin.get(f"/admin/products/{created['id']}")).json()
    variant_id = next(v["id"] for v in product["variants"] if v["size"] == "M")  # 4 on hand

    up = await admin.post(
        f"/admin/variants/{variant_id}/stock", json={"delta": 6, "reason": "delivery"}
    )
    assert up.json() == {
        "variant_id": variant_id,
        "stock_on_hand": 10,
        "stock_reserved": 0,
        "available": 10,
    }

    # Pretend 7 units are held for unpaid orders: on hand can't drop below 7.
    await session.execute(update(Variant).where(Variant.id == variant_id).values(stock_reserved=7))
    await session.commit()
    too_far = await admin.post(f"/admin/variants/{variant_id}/stock", json={"delta": -4})
    assert too_far.status_code == 409
    assert "7" in too_far.json()["detail"]
    ok = await admin.post(f"/admin/variants/{variant_id}/stock", json={"delta": -3})
    assert ok.json()["stock_on_hand"] == 7 and ok.json()["available"] == 0

    missing = await admin.post(f"/admin/variants/{uuid.uuid4()}/stock", json={"delta": 1})
    assert missing.status_code == 404


async def test_archive_hides_product_but_keeps_it(
    admin: AsyncClient, client: AsyncClient, cleanup: list[str]
) -> None:
    created = (
        await admin.post("/admin/products", json=new_product(status="active", image_url=IMAGE))
    ).json()
    cleanup.append(created["slug"])
    assert (await client.get(f"/products/{created['slug']}")).status_code == 200
    archived = await admin.delete(f"/admin/products/{created['id']}")
    assert archived.json()["status"] == "archived"
    assert (await client.get(f"/products/{created['slug']}")).status_code == 404
    listed = (await admin.get("/admin/products", params={"status": "archived"})).json()
    assert created["slug"] in [p["slug"] for p in listed["items"]]


async def test_admin_product_list_search_and_pages(admin: AsyncClient, seeded: int) -> None:
    found = (await admin.get("/admin/products", params={"q": "kaftan"})).json()
    assert found["items"] and all("kaftan" in p["name"].lower() for p in found["items"])

    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 40, **({"cursor": cursor} if cursor else {})}
        page = (await admin.get("/admin/products", params=params)).json()
        seen += [p["id"] for p in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) >= seeded  # drafts included, nothing repeated


async def test_orders_list_filters_by_status(
    admin: AsyncClient, make_client, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    email = await sign_up(make_client())  # a separate customer as the buyer
    customer_id = await session.scalar(select(Customer.id).where(Customer.email == email))
    variant = await session.scalar(select(Variant).limit(1))
    assert variant is not None
    orders = [
        Order(
            customer_id=customer_id,
            status="paid",
            total_kobo=500_000,
            paystack_reference=f"t-{uuid.uuid4()}",
        ),
        Order(
            guest_email="guest@example.com",
            status="pending_payment",
            total_kobo=300_000,
            paystack_reference=f"t-{uuid.uuid4()}",
            needs_review=True,
        ),
    ]
    session.add_all(orders)
    await session.flush()
    session.add(
        OrderItem(
            order_id=orders[0].id,
            variant_id=variant.id,
            qty=2,
            unit_price_kobo=250_000,
            name_snapshot="x",
            sku_snapshot=variant.sku,
        )
    )
    await session.commit()
    try:
        paid = (await admin.get("/admin/orders", params={"status": "paid"})).json()["items"]
        mine = next(o for o in paid if o["id"] == str(orders[0].id))
        assert mine["email"] == email and mine["is_guest"] is False and mine["item_count"] == 2
        assert all(o["status"] == "paid" for o in paid)

        pending = (await admin.get("/admin/orders", params={"status": "pending_payment"})).json()[
            "items"
        ]
        guest = next(o for o in pending if o["id"] == str(orders[1].id))
        assert guest["email"] == "guest@example.com" and guest["needs_review"] is True

        assert (await admin.get("/admin/orders", params={"status": "bogus"})).status_code == 422
    finally:
        await session.execute(delete(Order).where(Order.id.in_([o.id for o in orders])))
        await session.commit()


async def test_cancel_releases_the_hold_and_only_unpaid_orders(
    admin: AsyncClient, client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked
    await client.post("/cart/items", json={"variant_id": str(a.id), "qty": 2})
    out = (
        await client.post(
            "/checkout",
            json={"email": "c@example.com"},
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
    ).json()
    assert (await admin.post(f"/admin/orders/{out['order_id']}/cancel")).status_code == 204
    session.expire_all()
    assert await session.scalar(select(Variant.stock_reserved).where(Variant.id == a.id)) == 0
    again = await admin.post(f"/admin/orders/{out['order_id']}/cancel")
    assert again.status_code == 409 and "cancelled" in again.json()["detail"]
    assert (await admin.post(f"/admin/orders/{uuid.uuid4()}/cancel")).status_code == 404
