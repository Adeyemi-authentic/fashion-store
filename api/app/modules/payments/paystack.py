"""Thin Paystack client (test mode). Amounts are integer kobo, as Paystack expects.

Only `initialize`, `verify` and `refund` are used. Tests swap in `FakePaystack` through the
`get_paystack` dependency, so the suite never calls the real API.
"""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import get_settings

BASE_URL = "https://api.paystack.co"
# A checkout request waits on initialize, so it must fail fast rather than hang.
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class PaystackError(Exception):
    pass


@dataclass(frozen=True)
class Charge:
    """What Paystack says about a transaction (from verify or a webhook)."""

    reference: str
    status: str  # "success", "failed", "abandoned", "ongoing", ...
    amount_kobo: int
    currency: str
    transaction_id: int | None

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "Charge":
        return cls(
            reference=str(data.get("reference", "")),
            status=str(data.get("status", "")),
            amount_kobo=int(data.get("amount", 0)),
            currency=str(data.get("currency", "")),
            transaction_id=data.get("id"),
        )


class PaymentProvider(Protocol):
    async def initialize(
        self,
        *,
        email: str,
        amount_kobo: int,
        reference: str,
        callback_url: str,
        metadata: dict[str, Any],
    ) -> str: ...

    async def verify(self, reference: str) -> Charge: ...

    async def refund(self, *, transaction_id: int, amount_kobo: int) -> None: ...


class PaystackClient:
    def __init__(self, secret_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {secret_key}"},
            transport=transport,
        )

    async def _call(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            resp = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise PaystackError(f"Paystack unreachable: {exc.__class__.__name__}") from exc
        body = (
            resp.json()
            if resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if resp.status_code >= 400 or not body.get("status"):
            raise PaystackError(
                f"Paystack {path} -> {resp.status_code}: {body.get('message', 'no message')}"
            )
        return body.get("data") or {}

    async def initialize(
        self,
        *,
        email: str,
        amount_kobo: int,
        reference: str,
        callback_url: str,
        metadata: dict[str, Any],
    ) -> str:
        data = await self._call(
            "POST",
            "/transaction/initialize",
            {
                "email": email,
                "amount": amount_kobo,
                "currency": "NGN",
                "reference": reference,
                "callback_url": callback_url,
                "metadata": metadata,
            },
        )
        return str(data["authorization_url"])

    async def verify(self, reference: str) -> Charge:
        return Charge.from_data(await self._call("GET", f"/transaction/verify/{reference}"))

    async def refund(self, *, transaction_id: int, amount_kobo: int) -> None:
        await self._call("POST", "/refund", {"transaction": transaction_id, "amount": amount_kobo})


def signature_is_valid(raw_body: bytes, signature: str | None, secret_key: str) -> bool:
    """Paystack signs the raw request body with HMAC-SHA512 using the secret key."""
    if not signature or not secret_key:
        return False
    expected = hmac.new(secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)  # constant time: no timing hints


_client: PaystackClient | None = None


def get_paystack() -> PaymentProvider:
    """FastAPI dependency. One shared client (connection pooling); tests override this."""
    global _client
    if _client is None:
        _client = PaystackClient(get_settings().paystack_secret_key)
    return _client
