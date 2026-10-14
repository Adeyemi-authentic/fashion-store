import json

import httpx
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.modules.catalog.models import Variant
from app.modules.orders.models import Order
from app.platform import worker
from app.platform.email import Email, FileSender, ResendSender
from app.platform.models import OutboxJob
from app.platform.outbox import enqueue
from tests.conftest import FakeEmail, paid_order, sign_up


async def run_all() -> list[str]:
    outcomes: list[str] = []
    while batch := await worker.run_once(get_sessionmaker()):
        outcomes += batch
    return outcomes


async def test_paid_order_sends_one_confirmation_with_the_job_id_as_key(
    client: AsyncClient,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked
    out = await paid_order(client, paystack, a, qty=2, email="guest@example.com")
    assert set(await run_all()) == {"done"}  # confirmation (+ a low-stock alert: 3 left)
    (mail,) = outbox_email.to("guest@example.com")
    assert "order confirmed" in mail.subject and "₦" in mail.subject
    assert "x 2" in mail.text and out["reference"] in mail.text
    job_id = await session.scalar(
        select(OutboxJob.id).where(OutboxJob.type == "order_confirmation_email")
    )
    assert f"job-{job_id}" in outbox_email.calls


async def test_a_job_that_runs_twice_still_sends_one_email(
    client: AsyncClient,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    """The worker sent the email, then died before marking the job done. The job is retried
    (at least once) and sends again, with the same key, so the customer gets one email."""
    a, _ = stocked
    await paid_order(client, paystack, a, email="twice@example.com")
    async with get_sessionmaker()() as s:
        (job,) = await worker.claim(s)
    async with get_sessionmaker()() as s:
        await worker.HANDLERS[job.type](s, job)  # sends... and the worker "dies" here
    await session.execute(
        update(OutboxJob).where(OutboxJob.id == job.id).values(locked_until=OutboxJob.created_at)
    )
    await session.commit()
    assert await run_all() == ["done"]  # retried after the lease
    assert len(outbox_email.calls) == 2  # it did run twice...
    assert len(outbox_email.to("twice@example.com")) == 1  # ...but one email arrived


async def test_signed_in_orders_go_to_the_account_email(
    client: AsyncClient, stocked: list[Variant], paystack, outbox_email: FakeEmail
) -> None:  # type: ignore[no-untyped-def]
    account = await sign_up(client)
    a, _ = stocked
    await paid_order(client, paystack, a)
    await run_all()
    assert len(outbox_email.to(account)) == 1


async def test_late_refund_sends_apology_and_admin_alert(
    client: AsyncClient,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    outbox_email: FakeEmail,
) -> None:  # type: ignore[no-untyped-def]
    a, _ = stocked
    out = await paid_order(client, paystack, a, email="late@example.com")
    order = await session.scalar(select(Order).where(Order.paystack_reference == out["reference"]))
    assert order is not None
    await enqueue(session, "late_payment_apology_email", {"order_id": str(order.id)})
    await enqueue(
        session,
        "admin_alert",
        {"order_id": str(order.id), "reason": "late payment and the stock is gone"},
    )
    await session.commit()
    await run_all()
    (apology,) = outbox_email.to("late@example.com")[1:]  # [0] is the confirmation
    assert "refunding" in apology.subject
    (alert,) = outbox_email.to("admin@example.com")
    assert "late payment" in alert.subject


async def test_job_for_a_missing_order_is_dead_at_once(
    session: AsyncSession, outbox_email: FakeEmail
) -> None:
    await enqueue(
        session, "order_confirmation_email", {"order_id": "00000000-0000-0000-0000-000000000000"}
    )
    await session.commit()
    outcomes = await run_all()
    assert outcomes[0] == "dead"  # no pointless retries
    assert "not found" in (
        await session.scalar(
            select(OutboxJob.last_error).where(OutboxJob.type == "order_confirmation_email")
        )
        or ""
    )
    await session.execute(OutboxJob.__table__.delete())
    await session.commit()


async def test_file_sender_treats_a_repeat_key_as_sent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sender = FileSender(str(tmp_path))
    await sender.send(Email("a@example.com", "first", "t", "<p>t</p>"), "job-7")
    await sender.send(Email("a@example.com", "second", "t", "<p>t</p>"), "job-7")
    files = list(tmp_path.iterdir())
    assert [f.name for f in files] == ["job-7.json"]
    assert json.loads(files[0].read_text())["subject"] == "first"


async def test_resend_gets_the_idempotency_key_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["idempotency-key"]
        seen["to"] = json.loads(request.content)["to"][0]
        return httpx.Response(200, json={"id": "email_1"})

    sender = ResendSender("re_test", "Shop <s@example.com>", transport=httpx.MockTransport(handler))
    await sender.send(Email("b@example.com", "s", "t", "h"), "job-42")
    assert seen == {"key": "job-42", "to": "b@example.com"}
