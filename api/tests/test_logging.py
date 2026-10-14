import json
import logging
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Variant
from app.platform.logging import JsonFormatter, mask_email, request_id_var
from app.platform.models import OutboxJob


async def test_every_response_has_a_request_id(client: AsyncClient) -> None:
    made = (await client.get("/health")).headers["x-request-id"]
    assert len(made) == 32
    kept = await client.get("/health", headers={"X-Request-ID": "proxy-abc-12345"})
    assert kept.headers["x-request-id"] == "proxy-abc-12345"  # the proxy's id carries through
    junk = await client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert junk.headers["x-request-id"] != "bad id\nwith newline"


async def test_access_log_line_carries_the_id(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")
    await client.get("/products/no-such-thing", headers={"X-Request-ID": "trace-me-123"})
    record = next(r for r in caplog.records if r.name == "app.access")
    line = json.loads(JsonFormatter().format(record))
    assert line["status"] == 404
    assert line["route"] == "/products/{slug}"  # the template, not the raw path
    assert line["duration_ms"] >= 0


def test_json_lines_mask_emails() -> None:
    token = request_id_var.set("req-123456")
    try:
        record = logging.LogRecord(
            "app.x", logging.INFO, "", 0, "sent to %s", ("ada@example.com",), None
        )
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "sent to a***@example.com"
    assert line["request_id"] == "req-123456"
    assert mask_email("no email here") == "no email here"


async def test_jobs_remember_the_request_that_made_them(
    client: AsyncClient,
    session: AsyncSession,
    stocked: list[Variant],
    paystack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # type: ignore[no-untyped-def]
    """Checkout request -> webhook request -> confirmation job: the job carries the webhook's
    request id, so the worker's logs for it can be joined to that request."""
    import hashlib
    import hmac

    from app.config import get_settings

    a, _ = stocked
    await client.post("/cart/items", json={"variant_id": str(a.id), "qty": 1})
    out = (
        await client.post(
            "/checkout",
            json={"email": "log@example.com"},
            headers={"Idempotency-Key": f"k-{uuid.uuid4()}"},
        )
    ).json()
    secret = "sk_test_logs"
    monkeypatch.setattr(get_settings(), "paystack_secret_key", secret)
    body = json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": 9,
                "reference": out["reference"],
                "status": "success",
                "amount": out["total_kobo"],
                "currency": "NGN",
            },
        }
    ).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    await client.post(
        "/payments/webhook",
        content=body,
        headers={"x-paystack-signature": sig, "X-Request-ID": "webhook-req-42"},
    )
    payload = await session.scalar(
        select(OutboxJob.payload).where(OutboxJob.type == "order_confirmation_email")
    )
    assert payload is not None and payload["request_id"] == "webhook-req-42"
