import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.modules.catalog.models import Variant
from app.modules.orders.models import Order, Reservation
from app.modules.orders.service import expire_overdue_orders
from app.modules.orders.state import lock_order, transition
from app.modules.payments.fake import FakePaystack
from app.modules.payments.models import PaymentEvent
from app.platform.models import OutboxJob

SECRET = "sk_test_webhook_secret"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "paystack_secret_key", SECRET)


def signed(payload: dict, secret: str = SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return body, {"x-paystack-signature": sig, "content-type": "application/json"}


def charge_success(reference: str, amount: int, currency: str = "NGN", tx_id: int = 0) -> dict:
    return {
        "event": "charge.success",
        "data": {
            "id": tx_id or int(uuid.uuid4().int % 10**9),
            "reference": reference,
            "status": "success",
            "amount": amount,
            "currency": currency,
        },
    }


async def send(client: AsyncClient, payload: dict, secret: str = SECRET):  # type: ignore[no-untyped-def]
    body, headers = signed(payload, secret)
    return await client.post("/payments/webhook", content=body, headers=headers)


async def checkout(client: AsyncClient, *items: tuple[Variant, int]) -> dict:
    for v, qty in items:
        assert (
            await client.post("/cart/items", json={"variant_id": str(v.id), "qty": qty})
        ).status_code == 200
    resp = await client.post(
        "/checkout",
        json={"email": "buyer@example.com"},
        headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def stock(session: AsyncSession, v: Variant) -> tuple[int, int]:
    row = (
        await session.execute(
            select(Variant.stock_on_hand, Variant.stock_reserved).where(Variant.id == v.id)
        )
    ).one()
    return row[0], row[1]


async def status(session: AsyncSession, order_id: str) -> Order:
    session.expire_all()
    order = await session.get(Order, uuid.UUID(order_id))
    assert order is not None
    return order


async def jobs(session: AsyncSession) -> list[str]:
    return sorted((await session.execute(select(OutboxJob.type))).scalars())


async def expire(session: AsyncSession, order_id: str) -> None:
    await session.execute(
        update(Reservation)
        .where(Reservation.order_id == uuid.UUID(order_id))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()


# --- authenticity --------------------------------------------------------------------------


async def test_forged_signature_is_401_and_changes_nothing(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    out = await checkout(client, (stocked[0], 1))
    forged = await send(
        client, charge_success(out["reference"], out["total_kobo"]), secret="sk_test_attacker"
    )
    assert forged.status_code == 401
    missing = await client.post(
        "/payments/webhook", content=b"{}", headers={"content-type": "application/json"}
    )
    assert missing.status_code == 401
    assert (await status(session, out["order_id"])).status == "pending_payment"
    stored = (
        select(func.count())
        .select_from(PaymentEvent)
        .where(PaymentEvent.payload["data"]["reference"].astext == out["reference"])
    )
    assert await session.scalar(stored) == 0


# --- paid in time --------------------------------------------------------------------------


async def test_charge_success_pays_the_order_once(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, b = stocked
    out = await checkout(client, (a, 2), (b, 1))
    payload = charge_success(out["reference"], out["total_kobo"])

    first = await send(client, payload)
    assert first.status_code == 200 and first.json()["result"] == "paid"
    event = await session.scalar(
        select(PaymentEvent).where(
            PaymentEvent.paystack_event_id == f"charge.success:{payload['data']['id']}"
        )
    )
    assert (
        event is not None
        and str(event.order_id) == out["order_id"]
        and event.processed_at is not None
    )
    assert (await status(session, out["order_id"])).status == "paid"
    assert await stock(session, a) == (3, 0)  # 5 on hand, 2 sold, hold gone
    assert await stock(session, b) == (4, 0)
    assert (await client.get("/cart")).json()["items"] == []  # bought items left the bag
    assert await jobs(session) == ["order_confirmation_email"]

    again = await send(client, payload)  # Paystack redelivers
    assert again.status_code == 200 and again.json()["result"] == "duplicate"
    assert await stock(session, a) == (3, 0)  # not sold twice
    assert await jobs(session) == ["order_confirmation_email"]  # no second email

    # Same charge reported as a different event (e.g. after a manual resend): still once.
    payload["data"]["id"] += 1
    other = await send(client, payload)
    assert other.json()["result"] == "already_handled"
    assert await stock(session, a) == (3, 0)


async def test_the_same_webhook_twice_at_once_is_applied_once(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    out = await checkout(client, (a, 2))
    payload = charge_success(out["reference"], out["total_kobo"])
    results = await asyncio.gather(send(client, payload), send(client, payload))
    assert sorted(r.json()["result"] for r in results) == ["duplicate", "paid"]
    assert await stock(session, a) == (3, 0)
    assert await jobs(session) == ["order_confirmation_email"]


# --- wrong money ---------------------------------------------------------------------------


@pytest.mark.parametrize(("amount_delta", "currency"), [(-100, "NGN"), (0, "USD")])
async def test_wrong_amount_or_currency_is_flagged_not_paid(
    client: AsyncClient,
    session: AsyncSession,
    stocked: list[Variant],
    paystack: FakePaystack,
    amount_delta: int,
    currency: str,
) -> None:
    a, _ = stocked
    out = await checkout(client, (a, 1))
    resp = await send(
        client, charge_success(out["reference"], out["total_kobo"] + amount_delta, currency)
    )
    assert resp.status_code == 200  # a 4xx would make Paystack retry forever
    assert resp.json()["result"] == "needs_review"
    order = await status(session, out["order_id"])
    assert order.status == "pending_payment" and order.needs_review is True
    assert order.review_reason and str(out["total_kobo"]) in order.review_reason
    assert await stock(session, a) == (5, 1)  # still only held, not sold
    assert await jobs(session) == ["admin_alert"]


async def test_other_events_and_unknown_orders_get_200(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    ignored = await send(client, {"event": "transfer.success", "data": {"id": 1}})
    assert ignored.status_code == 200 and ignored.json()["result"] == "ignored"
    failed = charge_success("ff_x", 100)
    failed["data"]["status"] = "failed"
    assert (await send(client, failed)).json()["result"] == "ignored"
    unknown = await send(client, charge_success("ff_no_such_order", 100))
    assert unknown.status_code == 200 and unknown.json()["result"] == "unknown_order"
    garbage = await client.post(
        "/payments/webhook",
        content=b"not json",
        headers={
            "x-paystack-signature": hmac.new(
                SECRET.encode(), b"not json", hashlib.sha512
            ).hexdigest()
        },
    )
    assert garbage.status_code == 200


# --- late payments -------------------------------------------------------------------------


async def test_paid_after_hold_expired_with_worker_asleep_and_stock_left(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    """The hold ran out, nothing marked the order expired, but the stock is still free:
    the customer gets what they paid for."""
    a, _ = stocked
    out = await checkout(client, (a, 2))
    await expire(session, out["order_id"])
    resp = await send(client, charge_success(out["reference"], out["total_kobo"]))
    assert resp.json()["result"] == "paid_late"
    assert (await status(session, out["order_id"])).status == "paid"
    assert await stock(session, a) == (3, 0)


async def test_late_payment_after_expiry_job_with_stock_left(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    out = await checkout(client, (a, 1))
    await expire(session, out["order_id"])
    assert await expire_overdue_orders(session) == 1
    assert (await status(session, out["order_id"])).status == "expired"
    assert await stock(session, a) == (5, 0)  # hold released by the job

    resp = await send(client, charge_success(out["reference"], out["total_kobo"]))
    assert resp.json()["result"] == "paid_late"
    assert (await status(session, out["order_id"])).status == "paid"
    assert await stock(session, a) == (4, 0)


async def test_late_payment_when_stock_is_gone_becomes_refund(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack: FakePaystack,
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked
    out = await checkout(client, (a, 2))
    await expire(session, out["order_id"])
    rival = make_client()
    await checkout(rival, (a, 5))  # takes all 5 (our expired hold no longer counts)

    tx_id = 77_001
    resp = await send(client, charge_success(out["reference"], out["total_kobo"], tx_id=tx_id))
    assert resp.status_code == 200 and resp.json()["result"] == "refund_pending"
    assert (await status(session, out["order_id"])).status == "refund_pending"
    assert await stock(session, a) == (5, 5)  # the rival's hold is untouched
    assert await jobs(session) == ["admin_alert", "late_payment_apology_email", "refund_payment"]
    refund = await session.scalar(
        select(OutboxJob.payload).where(OutboxJob.type == "refund_payment")
    )
    assert refund == {
        "order_id": out["order_id"],
        "reference": out["reference"],
        "transaction_id": tx_id,
        "amount_kobo": out["total_kobo"],
        "request_id": resp.headers["x-request-id"],  # the webhook request that caused it
    }


async def test_payment_for_a_cancelled_order_is_refunded(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack: FakePaystack
) -> None:
    a, _ = stocked
    out = await checkout(client, (a, 1))
    order = await lock_order(session, uuid.UUID(out["order_id"]))
    from app.modules.orders import inventory

    await inventory.release_order_holds(session, order.id)
    await transition(session, order, "cancelled")
    await session.commit()
    resp = await send(client, charge_success(out["reference"], out["total_kobo"]))
    assert resp.json()["result"] == "refund_pending"
    assert await stock(session, a) == (5, 0)


# --- expiry job ----------------------------------------------------------------------------


async def test_expiry_job_only_touches_overdue_pending_orders(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack: FakePaystack,
) -> None:  # type: ignore[no-untyped-def]
    a, b = stocked
    overdue = await checkout(client, (a, 1))
    fresh_buyer, paid_buyer = make_client(), make_client()
    fresh = await checkout(fresh_buyer, (b, 1))
    paid = await checkout(paid_buyer, (a, 1))
    await send(paid_buyer, charge_success(paid["reference"], paid["total_kobo"]))
    await expire(session, overdue["order_id"])

    assert await expire_overdue_orders(session) == 1
    assert (await status(session, overdue["order_id"])).status == "expired"
    assert (await status(session, fresh["order_id"])).status == "pending_payment"
    assert (await status(session, paid["order_id"])).status == "paid"
    assert await expire_overdue_orders(session) == 0  # nothing left to do

    async def run_job() -> int:
        async with get_sessionmaker()() as s:
            return await expire_overdue_orders(s)

    await expire(session, fresh["order_id"])
    assert sorted(await asyncio.gather(run_job(), run_job())) == [
        0,
        1,
    ]  # two copies: no double work
