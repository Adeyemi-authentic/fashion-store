import hashlib
import hmac
import json

import httpx
import pytest

from app.modules.payments.paystack import PaystackClient, PaystackError, signature_is_valid

SECRET = "sk_test_unit"


def test_signature_check() -> None:
    body = json.dumps({"event": "charge.success"}).encode()
    good = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()
    assert signature_is_valid(body, good, SECRET)
    assert not signature_is_valid(body + b" ", good, SECRET)  # body changed
    assert not signature_is_valid(body, good, "sk_test_other")  # signed with another key
    assert not signature_is_valid(body, None, SECRET)
    assert not signature_is_valid(body, good, "")  # no key configured: reject, don't accept


def client_with(handler) -> PaystackClient:  # type: ignore[no-untyped-def]
    return PaystackClient(SECRET, transport=httpx.MockTransport(handler))


async def test_initialize_sends_kobo_and_returns_url() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"status": True, "data": {"authorization_url": "https://pay/x"}}
        )

    url = await client_with(handler).initialize(
        email="a@example.com",
        amount_kobo=4_500_000,
        reference="ref1",
        callback_url="http://cb",
        metadata={},
    )
    assert url == "https://pay/x"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["amount"] == 4_500_000 and seen["body"]["currency"] == "NGN"


async def test_errors_become_paystack_error() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"status": False, "message": "Duplicate Transaction Reference"}
        )

    with pytest.raises(PaystackError, match="Duplicate"):
        await client_with(refused).verify("ref1")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(PaystackError, match="unreachable"):
        await client_with(down).verify("ref1")
