"""Outbox job handlers for orders: emails to the customer (ADR-002)."""

import html
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.accounts.service import emails_for
from app.modules.orders.models import Order, OrderItem
from app.modules.payments.paystack import get_paystack
from app.platform.email import Email, get_sender
from app.platform.worker import Job, PermanentJobError, handler


def _naira(kobo: int) -> str:
    return f"₦{kobo / 100:,.0f}"


async def _order(session: AsyncSession, job: Job) -> tuple[Order, list[OrderItem], str]:
    try:
        order_id = uuid.UUID(str(job.payload["order_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError("payload has no valid order_id") from exc
    order = await session.get(Order, order_id)
    if order is None:
        raise PermanentJobError(f"order {order_id} not found")
    items = list(
        (await session.execute(select(OrderItem).where(OrderItem.order_id == order.id))).scalars()
    )
    if order.customer_id:
        to = (await emails_for(session, [order.customer_id])).get(order.customer_id)
    else:
        to = order.guest_email
    if not to:
        raise PermanentJobError(f"order {order_id} has no email address")
    return order, items, to


def _lines(items: list[OrderItem]) -> tuple[str, str]:
    text = "\n".join(
        f"- {i.name_snapshot} x {i.qty}: {_naira(i.unit_price_kobo * i.qty)}" for i in items
    )
    rows = "".join(
        f"<tr><td>{html.escape(i.name_snapshot)} × {i.qty}</td>"
        f"<td align='right'>{_naira(i.unit_price_kobo * i.qty)}</td></tr>"
        for i in items
    )
    return text, f"<table width='100%'>{rows}</table>"


def _footer(brand: str) -> str:
    return f"{brand} is a fictional brand (concept build, Paystack test mode)."


@handler("order_confirmation_email")
async def order_confirmation(session: AsyncSession, job: Job) -> None:
    s = get_settings()
    order, items, to = await _order(session, job)
    total = _naira(order.total_kobo)
    link = f"{s.public_site_url}/checkout/callback?reference={order.paystack_reference}"
    text_lines, html_lines = _lines(items)
    text = (
        f"Thank you! We've received your payment.\n\n{text_lines}\n\n"
        f"Total: {total}\nOrder: {link}\n\n{_footer(s.brand_name)}"
    )
    body = (
        f"<p>Thank you! We've received your payment.</p>{html_lines}"
        f"<p><b>Total: {total}</b></p>"
        f"<p><a href='{html.escape(link)}'>View your order</a></p>"
        f"<p style='color:#888'>{html.escape(_footer(s.brand_name))}</p>"
    )
    subject = f"{s.brand_name}: order confirmed ({total})"
    await get_sender().send(Email(to, subject, text, body), idempotency_key=f"job-{job.id}")


@handler("late_payment_apology_email")
async def late_payment_apology(session: AsyncSession, job: Job) -> None:
    s = get_settings()
    order, items, to = await _order(session, job)
    total = _naira(order.total_kobo)
    text_lines, html_lines = _lines(items)
    sorry = (
        "Sorry! Your payment reached us after your items were held for someone else, "
        "and they've now sold out."
    )
    text = f"{sorry} We're refunding {total} in full to the card you used.\n\n{text_lines}"
    body = (
        f"<p>{sorry} We're refunding <b>{total}</b> in full to the card you used.</p>{html_lines}"
    )
    subject = f"{s.brand_name}: we're refunding your payment"
    await get_sender().send(Email(to, subject, text, body), idempotency_key=f"job-{job.id}")


@handler("refund_payment")
async def refund_payment(session: AsyncSession, job: Job) -> None:
    """Refund a late payment. Check first, then act: if a retry finds a refund already
    made for this transaction (the previous attempt got that far before dying), it stops
    there, so the customer is never refunded twice."""
    order, _, _ = await _order(session, job)
    if order.status == "refunded":
        return
    if order.status != "refund_pending":
        raise PermanentJobError(f"order {order.id} is {order.status}, not refund_pending")
    transaction_id = job.payload.get("transaction_id")
    amount = int(job.payload.get("amount_kobo") or order.total_kobo)
    if not isinstance(transaction_id, int):
        raise PermanentJobError("payload has no Paystack transaction_id")
    provider = get_paystack()
    existing = [s for s in await provider.list_refunds(transaction_id) if s != "failed"]
    if existing:
        return  # already requested; Paystack's refund.processed webhook completes the order
    await provider.refund(transaction_id=transaction_id, amount_kobo=amount)
