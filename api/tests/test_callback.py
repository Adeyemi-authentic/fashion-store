import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.catalog.models import Variant
from app.modules.payments.fake import FakePaystack
from app.platform.models import OutboxJob
from tests.conftest import sign_up

SECRET = "sk_test_callback"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "paystack_secret_key", SECRET)


async def checkout(client: AsyncClient, v: Variant, qty: int = 1) -> dict:
    await client.post("/cart/items", json={"variant_id": str(v.id), "qty": qty})
    resp = await client.post(
        "/checkout",
        json={"email": "cb@example.com"},
        headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def on_hand(session: AsyncSession, v: Variant) -> int:
    session.expire_all()
    return await session.scalar(select(Variant.stock_on_hand).where(Variant.id == v.id))  # type: ignore[return-value]


async def test_callback_does_not_trust_the_redirect(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    """Someone opens the callback URL without paying: Paystack says 'abandoned', nothing moves."""
    out = await checkout(client, stocked[0])
    resp = await client.get(f"/orders/by-reference/{out['reference']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_payment"
    assert await on_hand(session, stocked[0]) == 5


async def test_callback_pays_before_the_webhook_and_the_webhook_is_then_a_no_op(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    out = await checkout(client, a, 2)
    charge = paystack.pay(out["reference"])  # the customer pays on Paystack's page

    resp = await client.get(f"/orders/by-reference/{out['reference']}")
    body = resp.json()
    assert body["status"] == "paid"
    assert body["total_kobo"] == out["total_kobo"]
    assert body["items"] == [
        {"name": body["items"][0]["name"], "sku": a.sku, "qty": 2, "unit_price_kobo": a.price_kobo}
    ]
    assert "email" not in body
    assert await on_hand(session, a) == 3

    payload = {
        "event": "charge.success",
        "data": {
            "id": charge.transaction_id,
            "reference": charge.reference,
            "status": "success",
            "amount": charge.amount_kobo,
            "currency": "NGN",
        },
    }
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    late = await client.post(
        "/payments/webhook", content=raw, headers={"x-paystack-signature": sig}
    )
    assert late.json()["result"] == "already_handled"
    assert await on_hand(session, a) == 3  # sold once
    emails = (
        (await session.execute(select(OutboxJob.type).where(OutboxJob.type != "low_stock_alert")))
        .scalars()
        .all()
    )
    assert list(emails) == ["order_confirmation_email"]


async def test_wrong_amount_from_verify_shows_under_review(
    client: AsyncClient, stocked: list[Variant], paystack: FakePaystack
) -> None:
    out = await checkout(client, stocked[0])
    paystack.pay(out["reference"], amount_kobo=100)
    assert (await client.get(f"/orders/by-reference/{out['reference']}")).json()[
        "status"
    ] == "under_review"


async def test_unknown_reference_is_404(client: AsyncClient, paystack: FakePaystack) -> None:
    assert (await client.get("/orders/by-reference/ff_nope")).status_code == 404


async def test_my_orders_lists_only_mine(
    client: AsyncClient, make_client, stocked: list[Variant], paystack: FakePaystack
) -> None:  # type: ignore[no-untyped-def]
    a, b = stocked
    assert (await client.get("/orders/mine")).status_code == 401
    await sign_up(client)
    mine = await checkout(client, a)
    stranger = make_client()
    await sign_up(stranger)
    await checkout(stranger, b)
    listed = (await client.get("/orders/mine")).json()
    assert [o["reference"] for o in listed] == [mine["reference"]]
