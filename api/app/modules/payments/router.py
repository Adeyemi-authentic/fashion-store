"""Paystack webhook. Called by Paystack directly (not through the storefront proxy, ADR-005).

Every response is 200 except a bad signature (401): Paystack retries anything that isn't a
2xx, so rejecting an event we can't use would only make it come back again and again.
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.modules.orders import service as orders
from app.modules.payments.models import PaymentEvent
from app.modules.payments.paystack import Charge, signature_is_valid

log = logging.getLogger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


def _ok(result: str) -> JSONResponse:
    return JSONResponse({"received": True, "result": result})


@router.post("/webhook")
async def paystack_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    signature: Annotated[str | None, Header(alias="x-paystack-signature")] = None,
) -> JSONResponse:
    raw = await request.body()  # the signature covers these exact bytes, so don't re-encode
    if not signature_is_valid(raw, signature, get_settings().paystack_secret_key):
        log.warning("paystack webhook with a bad signature")
        return JSONResponse({"detail": "invalid signature"}, status_code=401)
    try:
        payload: dict[str, Any] = json.loads(raw)
        event = str(payload["event"])
        data: dict[str, Any] = payload.get("data") or {}
    except (ValueError, KeyError, TypeError):
        log.error("signed paystack webhook we can't parse")
        return _ok("unparseable")

    # Paystack has no event id; "<event>:<data.id>" identifies a delivery's content.
    event_id = f"{event}:{data.get('id')}"
    # Storing the event, applying it and marking it processed are one transaction: if
    # anything fails, nothing is stored and Paystack's retry gets a clean second attempt.
    first_time = await session.scalar(
        insert(PaymentEvent)
        .values(paystack_event_id=event_id, type=event, payload=payload)
        .on_conflict_do_nothing()
        .returning(PaymentEvent.paystack_event_id)
    )
    if not first_time:
        await session.rollback()
        return _ok("duplicate")

    if event != "charge.success" or data.get("status") != "success":
        result = "ignored"
    else:
        result = await orders.apply_successful_charge(session, Charge.from_data(data))
    reference = data.get("reference")
    order_id = await orders.order_id_for_reference(session, str(reference)) if reference else None
    await session.execute(
        update(PaymentEvent)
        .where(PaymentEvent.paystack_event_id == event_id)
        .values(processed_at=func.now(), order_id=order_id)  # audit trail per order
    )
    await session.commit()
    log.info("paystack %s %s -> %s", event, data.get("reference"), result)
    return _ok(result)
