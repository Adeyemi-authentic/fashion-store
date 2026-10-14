import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.modules.cart.models import Cart
from app.modules.catalog.models import Variant
from app.modules.orders.models import IdempotencyKey, Order, Reservation
from app.platform import worker
from app.platform.jobs import register_all
from app.platform.models import OutboxJob, rate_limits
from app.platform.schedule import ensure_scheduled
from tests.conftest import FakeEmail, paid_order

register_all()


async def live(session: AsyncSession, job_type: str) -> list[OutboxJob]:
    session.expire_all()
    return list(
        (
            await session.execute(
                select(OutboxJob).where(
                    OutboxJob.type == job_type, OutboxJob.status.in_(("queued", "running"))
                )
            )
        ).scalars()
    )


async def make_due(session: AsyncSession, job_type: str) -> None:
    await session.execute(
        update(OutboxJob)
        .where(OutboxJob.type == job_type, OutboxJob.status == "queued")
        .values(run_after=func.now())
    )
    await session.commit()


async def test_scheduled_jobs_keep_exactly_one_live_copy(session: AsyncSession) -> None:
    await session.execute(delete(OutboxJob))
    await session.commit()

    # Two workers start at once and both schedule:
    async def start() -> None:
        async with get_sessionmaker()() as s:
            await ensure_scheduled(s)

    await asyncio.gather(start(), start())
    assert len(await live(session, "expire_holds")) == 1
    assert len(await live(session, "cleanup")) == 1

    for _ in range(3):  # each run queues the next one, a minute later
        await make_due(session, "expire_holds")
        before = datetime.now(UTC)
        async with get_sessionmaker()() as s:
            (job,) = [j for j in await worker.claim(s, limit=10) if j.type == "expire_holds"] or [
                None
            ]
        assert job is not None
        assert await worker.run_job(get_sessionmaker(), job) == "done"
        (nxt,) = await live(session, "expire_holds")
        assert nxt.run_after >= before + timedelta(seconds=50)
    await session.execute(delete(OutboxJob))
    await session.commit()


async def test_expire_holds_job_expires_overdue_orders(
    client: AsyncClient, session: AsyncSession, stocked: list[Variant], paystack
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked
    await client.post("/cart/items", json={"variant_id": str(a.id), "qty": 2})
    out = (
        await client.post(
            "/checkout",
            json={"email": "x@example.com"},
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
    ).json()
    await session.execute(
        update(Reservation)
        .where(Reservation.order_id == uuid.UUID(out["order_id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    await ensure_scheduled(session)
    await make_due(session, "expire_holds")
    await worker.run_once(get_sessionmaker(), limit=10)
    session.expire_all()
    assert (await session.get(Order, uuid.UUID(out["order_id"]))).status == "expired"  # type: ignore[union-attr]
    assert await session.scalar(select(Variant.stock_reserved).where(Variant.id == a.id)) == 0


async def test_cleanup_removes_only_old_rows(session: AsyncSession) -> None:
    old, new = datetime.now(UTC) - timedelta(days=40), datetime.now(UTC)
    await session.execute(insert(rate_limits).values(key="ip:old", window_start=old, count=1))
    await session.execute(insert(rate_limits).values(key="ip:new", window_start=new, count=1))
    session.add_all(
        [Cart(session_token=f"old-{uuid.uuid4()}"), Cart(session_token=f"new-{uuid.uuid4()}")]
    )
    session.add_all(
        [
            IdempotencyKey(key=f"g:x:old-{uuid.uuid4()}", request_hash="h"),
            IdempotencyKey(key=f"g:x:new-{uuid.uuid4()}", request_hash="h"),
        ]
    )
    await session.commit()
    await session.execute(
        update(Cart).where(Cart.session_token.like("old-%")).values(updated_at=old)
    )
    await session.execute(
        update(IdempotencyKey).where(IdempotencyKey.key.like("g:x:old-%")).values(created_at=old)
    )
    await session.commit()

    await ensure_scheduled(session)
    await make_due(session, "cleanup")
    await worker.run_once(get_sessionmaker(), limit=10)

    assert (
        await session.scalar(
            select(func.count()).select_from(rate_limits).where(rate_limits.c.key == "ip:old")
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count()).select_from(rate_limits).where(rate_limits.c.key == "ip:new")
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count()).select_from(Cart).where(Cart.session_token.like("old-%"))
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count()).select_from(Cart).where(Cart.session_token.like("new-%"))
        )
        == 1
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(IdempotencyKey)
            .where(IdempotencyKey.key.like("g:x:old-%"))
        )
        == 0
    )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(IdempotencyKey)
            .where(IdempotencyKey.key.like("g:x:new-%"))
        )
        == 1
    )
    await session.execute(delete(rate_limits))
    await session.execute(delete(Cart).where(Cart.session_token.like("new-%")))
    await session.execute(delete(IdempotencyKey).where(IdempotencyKey.key.like("g:x:%")))
    await session.execute(delete(OutboxJob))
    await session.commit()


async def test_low_stock_alert_fires_once_when_crossing_the_line(
    client: AsyncClient,
    make_client,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked  # 5 available
    await paid_order(client, paystack, a, qty=1)  # 4 left: above the line
    await paid_order(make_client(), paystack, a, qty=1)  # 3 left: crosses it
    await paid_order(make_client(), paystack, a, qty=1)  # 2 left: already below
    alerts = (
        (
            await session.execute(
                select(OutboxJob.payload).where(OutboxJob.type == "low_stock_alert")
            )
        )
        .scalars()
        .all()
    )
    assert [p["available"] for p in alerts] == [3]
    while await worker.run_once(get_sessionmaker()):
        pass
    (mail,) = [m for m in outbox_email.to("admin@example.com") if "Low stock" in m.subject]
    assert a.sku in mail.subject and "3 left" in mail.subject
