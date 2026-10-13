import asyncio
import uuid

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Variant
from app.modules.orders import inventory
from app.modules.orders.models import IdempotencyKey, Order, OrderItem, Reservation
from app.modules.payments.fake import FakePaystack
from tests.conftest import sign_up

GUEST = {"email": "Buyer@Example.com"}


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"test-{uuid.uuid4()}"}


async def fill_bag(client: AsyncClient, *items: tuple[Variant, int]) -> None:
    for v, qty in items:
        resp = await client.post("/cart/items", json={"variant_id": str(v.id), "qty": qty})
        assert resp.status_code == 200, resp.text


async def reserved(session: AsyncSession, v: Variant) -> int:
    return await session.scalar(select(Variant.stock_reserved).where(Variant.id == v.id))  # type: ignore[return-value]


async def test_checkout_holds_stock_and_starts_payment(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, b = stocked
    await fill_bag(client, (a, 2), (b, 1))
    resp = await client.post("/checkout", json=GUEST, headers=key())
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["authorization_url"].startswith("https://checkout.paystack.test/")
    assert out["total_kobo"] == 2 * a.price_kobo + b.price_kobo

    order = await session.get(Order, uuid.UUID(out["order_id"]))
    assert order is not None and order.status == "pending_payment"
    assert order.guest_email == "buyer@example.com" and order.total_kobo == out["total_kobo"]
    assert await reserved(session, a) == 2 and await reserved(session, b) == 1
    sent = paystack.initialized[out["reference"]]
    assert sent["amount"] == out["total_kobo"] and sent["email"].lower() == "buyer@example.com"
    # The bag is kept until payment succeeds, so an abandoned payment loses nothing.
    assert (await client.get("/cart")).json()["item_count"] == 3


async def test_prices_are_copied_at_checkout(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    await fill_bag(client, (a, 1))
    out = (await client.post("/checkout", json=GUEST, headers=key())).json()
    await session.execute(
        update(Variant).where(Variant.id == a.id).values(price_kobo=a.price_kobo * 3)
    )
    await session.commit()
    try:
        item = await session.scalar(
            select(OrderItem).where(OrderItem.order_id == uuid.UUID(out["order_id"]))
        )
        assert item is not None and item.unit_price_kobo == a.price_kobo
    finally:
        await session.execute(
            update(Variant).where(Variant.id == a.id).values(price_kobo=a.price_kobo)
        )
        await session.commit()


async def test_same_key_replays_the_same_order(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    await fill_bag(client, (a, 1))
    headers = key()
    first = await client.post("/checkout", json=GUEST, headers=headers)
    again = await client.post("/checkout", json=GUEST, headers=headers)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json()
    assert len(paystack.initialized) == 1
    assert await reserved(session, a) == 1  # held once, not twice


async def test_two_requests_with_one_key_at_once_make_one_order(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    """A double-tap: both requests arrive together. The second waits on the first one's key
    insert, then sees it in progress (409, retry) or finished (same response)."""
    a, _ = stocked
    await fill_bag(client, (a, 1))
    headers = key()
    results = await asyncio.gather(
        client.post("/checkout", json=GUEST, headers=headers),
        client.post("/checkout", json=GUEST, headers=headers),
    )
    codes = sorted(r.status_code for r in results)
    assert codes in ([201, 201], [201, 409])
    if codes == [201, 409]:
        busy = next(r for r in results if r.status_code == 409)
        assert busy.headers["retry-after"] == "1"
        retry = await client.post("/checkout", json=GUEST, headers=headers)
        assert retry.json() == next(r for r in results if r.status_code == 201).json()
    orders = await session.scalar(
        select(func.count()).select_from(OrderItem).where(OrderItem.variant_id == a.id)
    )
    assert orders == 1 and len(paystack.initialized) == 1 and await reserved(session, a) == 1


async def test_same_key_different_body_is_422(
    client: AsyncClient, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    await fill_bag(client, (a, 1))
    headers = key()
    assert (await client.post("/checkout", json=GUEST, headers=headers)).status_code == 201
    other = await client.post(
        "/checkout", json={"email": "someone.else@example.com"}, headers=headers
    )
    assert other.status_code == 422
    assert len(paystack.initialized) == 1


async def test_keys_are_scoped_per_buyer(
    client: AsyncClient, make_client, stocked: list[Variant], paystack: FakePaystack
) -> None:  # type: ignore[no-untyped-def]
    """Another shopper sending the same key value gets their own order, never ours."""
    a, b = stocked
    stranger = make_client()
    await fill_bag(client, (a, 1))
    await fill_bag(stranger, (b, 1))
    headers = key()
    mine = (await client.post("/checkout", json=GUEST, headers=headers)).json()
    theirs = await stranger.post("/checkout", json=GUEST, headers=headers)
    assert theirs.status_code == 201
    assert theirs.json()["order_id"] != mine["order_id"]


async def test_out_of_stock_holds_nothing(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack: FakePaystack,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """The bag looked fine when checked, but a rival took the last unit before our hold:
    the conditional UPDATE catches it and nothing at all is held (not even the other item)."""
    from app.modules.orders import checkout as checkout_module

    a, b = stocked
    await fill_bag(client, (a, 2), (b, 5))
    rival = make_client()
    await fill_bag(rival, (b, 1))
    real_lines = checkout_module.checkout_lines

    async def lines_then_rival_buys(session_, owner):  # type: ignore[no-untyped-def]
        result = await real_lines(session_, owner)
        monkeypatch.setattr(checkout_module, "checkout_lines", real_lines)  # rival: normal path
        assert (await rival.post("/checkout", json=GUEST, headers=key())).status_code == 201
        return result

    monkeypatch.setattr(checkout_module, "checkout_lines", lines_then_rival_buys)
    resp = await client.post("/checkout", json=GUEST, headers=key())
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "out_of_stock"
    assert resp.json()["detail"]["skus"] == [b.sku]
    assert await reserved(session, a) == 0  # a's 2 were not kept either
    assert await reserved(session, b) == 1  # only the rival's


async def test_bag_problems_are_409_before_anything_is_held(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    await fill_bag(client, (a, 3))
    await session.execute(update(Variant).where(Variant.id == a.id).values(stock_on_hand=2))
    await session.commit()
    resp = await client.post("/checkout", json=GUEST, headers=key())
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "bag_needs_attention"
    assert paystack.initialized == {}


async def test_validation(
    client: AsyncClient, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    assert (
        await client.post("/checkout", json=GUEST, headers=key())
    ).status_code == 422  # empty bag
    await fill_bag(client, (a, 1))
    assert (await client.post("/checkout", json=GUEST)).status_code == 422  # no key
    assert (
        await client.post("/checkout", json=GUEST, headers={"Idempotency-Key": "x"})
    ).status_code == 422
    assert (
        await client.post("/checkout", json={}, headers=key())
    ).status_code == 422  # guest, no email


async def test_signed_in_buyer_uses_account_email(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    email = await sign_up(client)
    await fill_bag(client, (a, 1))
    out = (await client.post("/checkout", json={}, headers=key())).json()
    order = await session.get(Order, uuid.UUID(out["order_id"]))
    assert order is not None and order.customer_id is not None and order.guest_email is None
    assert paystack.initialized[out["reference"]]["email"] == email


async def test_paystack_down_undoes_everything_and_the_key_can_be_retried(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    await fill_bag(client, (a, 2))
    headers = key()
    paystack.fail_initialize = True
    resp = await client.post("/checkout", json=GUEST, headers=headers)
    assert resp.status_code == 502
    assert await reserved(session, a) == 0
    cancelled = await session.scalar(
        select(Order.status).join(OrderItem).where(OrderItem.variant_id == a.id)
    )
    assert cancelled == "cancelled"
    assert (
        await session.scalar(
            select(func.count())
            .select_from(IdempotencyKey)
            .where(IdempotencyKey.key.like(f"%{headers['Idempotency-Key']}"))
        )
        == 0
    )

    paystack.fail_initialize = False
    retry = await client.post("/checkout", json=GUEST, headers=headers)
    assert retry.status_code == 201
    assert await reserved(session, a) == 2


async def test_many_shoppers_racing_never_oversell(
    make_client, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:  # type: ignore[no-untyped-def]
    """12 shoppers each want 1 of 5 units. Exactly 5 get an order; the counter matches the
    reservation rows afterwards."""
    a, _ = stocked
    shoppers = [make_client() for _ in range(12)]
    for s in shoppers:
        await fill_bag(s, (a, 1))
    results = await asyncio.gather(
        *(s.post("/checkout", json=GUEST, headers=key()) for s in shoppers)
    )
    codes = [r.status_code for r in results]
    assert codes.count(201) == 5 and codes.count(409) == 7
    assert await reserved(session, a) == 5
    held = await session.scalar(
        select(func.sum(Reservation.qty)).where(
            Reservation.variant_id == a.id, Reservation.released_at.is_(None)
        )
    )
    assert held == 5
    assert await inventory.reserved_mismatches(session) == []
