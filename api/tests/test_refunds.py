import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.modules.catalog.models import Variant
from app.modules.orders.models import Order, Reservation
from app.platform import worker
from app.platform.models import OutboxJob
from tests.conftest import FakeEmail

SECRET = "sk_test_refunds"


@pytest.fixture(autouse=True)
def signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "paystack_secret_key", SECRET)


async def webhook(client: AsyncClient, event: str, data: dict):  # type: ignore[no-untyped-def]
    raw = json.dumps({"event": event, "data": data}).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return await client.post(
        "/payments/webhook", content=raw, headers={"x-paystack-signature": sig}
    )


@dataclass(frozen=True)
class Placed:
    id: uuid.UUID
    paystack_reference: str
    total_kobo: int


async def late_payment_with_no_stock(
    client: AsyncClient, make_client, session: AsyncSession, paystack, v: Variant
) -> Placed:  # type: ignore[no-untyped-def]
    """Customer's hold expires, a rival buys everything, then the customer's payment lands."""
    await client.post("/cart/items", json={"variant_id": str(v.id), "qty": 1})
    out = (
        await client.post(
            "/checkout",
            json={"email": "late@example.com"},
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
    ).json()
    await session.execute(
        update(Reservation)
        .where(Reservation.order_id == uuid.UUID(out["order_id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    rival = make_client()
    await rival.post("/cart/items", json={"variant_id": str(v.id), "qty": 5})
    assert (
        await rival.post(
            "/checkout",
            json={"email": "r@example.com"},
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
    ).status_code == 201
    paystack.pay(out["reference"])
    assert (await client.get(f"/orders/by-reference/{out['reference']}")).json()[
        "status"
    ] == "refund_pending"
    order = await session.get(Order, uuid.UUID(out["order_id"]))
    assert order is not None
    # Plain values: the ORM object expires on later commits and can't lazy-load in async.
    return Placed(order.id, order.paystack_reference, order.total_kobo)


async def run_all() -> list[str]:
    outcomes: list[str] = []
    while batch := await worker.run_once(get_sessionmaker()):
        outcomes += batch
    return outcomes


async def test_late_payment_is_refunded_once_even_if_the_job_runs_twice(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    order = await late_payment_with_no_stock(client, make_client, session, paystack, stocked[0])
    charge = paystack.charges[order.paystack_reference]

    # The refund job runs, then (as if the worker died before marking it done) runs again.
    job = await session.scalar(select(OutboxJob).where(OutboxJob.type == "refund_payment"))
    assert job is not None
    claimed = worker.Job(job.id, job.type, job.payload, job.attempts)
    async with get_sessionmaker()() as s:
        await worker.HANDLERS["refund_payment"](s, claimed)
    assert await run_all() == ["done", "done", "done"]  # refund (again), apology, admin alert
    assert paystack.refunds == [
        (charge.transaction_id, order.total_kobo)
    ]  # one refund, full amount
    assert len(outbox_email.to("late@example.com")) == 1  # the apology
    assert len(outbox_email.to("admin@example.com")) == 1

    # Paystack confirms the refund: the order is closed. A redelivery changes nothing.
    data = {
        "transaction_reference": order.paystack_reference,
        "refund_reference": "rf_1",
        "status": "processed",
    }
    assert (await webhook(client, "refund.processed", data)).json()["result"] == "refunded"
    assert (await webhook(client, "refund.processed", data)).json()["result"] == "duplicate"
    session.expire_all()
    assert (await session.get(Order, order.id)).status == "refunded"  # type: ignore[union-attr]


async def test_a_failed_refund_is_flagged_for_a_human(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    order = await late_payment_with_no_stock(client, make_client, session, paystack, stocked[0])
    data = {
        "transaction_reference": order.paystack_reference,
        "refund_reference": "rf_2",
        "status": "failed",
    }
    assert (await webhook(client, "refund.failed", data)).json()["result"] == "needs_review"
    session.expire_all()
    fresh = await session.get(Order, order.id)
    assert fresh is not None and fresh.status == "refund_pending" and fresh.needs_review


async def test_refund_events_without_data_id_do_not_collide(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    """Keyed only on data.id, every refund event would be 'refund.processed:None' and all but
    the first would be dropped as duplicates."""
    a, b = stocked
    first = await late_payment_with_no_stock(client, make_client, session, paystack, a)
    second = await late_payment_with_no_stock(make_client(), make_client, session, paystack, b)
    for order, ref in ((first, "rf_a"), (second, "rf_b")):
        data = {
            "transaction_reference": order.paystack_reference,
            "refund_reference": ref,
            "status": "processed",
        }
        assert (await webhook(client, "refund.processed", data)).json()["result"] == "refunded"


async def test_refund_job_refuses_an_order_that_is_not_refund_pending(
    client: AsyncClient,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import paid_order

    out = await paid_order(client, paystack, stocked[0])
    from app.platform.outbox import enqueue

    await enqueue(
        session,
        "refund_payment",
        {"order_id": out["order_id"], "transaction_id": 1, "amount_kobo": 1},
    )
    await session.commit()
    outcomes = await run_all()
    assert (
        "dead" in outcomes and paystack.refunds == []
    )  # a paid order is never refunded by mistake
