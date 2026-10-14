import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.modules.catalog.models import Variant
from app.platform.models import rate_limits
from tests.conftest import sign_up

pytestmark = pytest.mark.real_rate_limits
SECRET = "proxy-secret-for-tests"


@pytest.fixture(autouse=True)
async def fresh_counters(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "proxy_shared_secret", SECRET)
    monkeypatch.setattr(get_settings(), "rate_limit_checkout_per_min", 10)
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_min", 10)
    await session.execute(delete(rate_limits))
    await session.commit()


def via_proxy(ip: str) -> dict[str, str]:
    return {"X-Proxy-Auth": SECRET, "X-Forwarded-For": ip}


async def checkout(client: AsyncClient, headers: dict[str, str]):  # type: ignore[no-untyped-def]
    return await client.post(
        "/checkout",
        json={"email": "rl@example.com"},
        headers={**headers, "Idempotency-Key": f"k-{uuid.uuid4()}"},
    )


async def test_eleventh_checkout_attempt_in_a_minute_from_one_ip_is_429(
    client: AsyncClient, stocked: list[Variant], paystack
) -> None:  # type: ignore[no-untyped-def]
    """PLAN 'done when'. Attempts count whether they succeed or not (an empty bag is a 422
    here): card testers fail most of the time, and their failures must use up the limit."""
    codes = [(await checkout(client, via_proxy("41.58.1.1"))).status_code for _ in range(11)]
    assert codes[:10] == [422] * 10
    assert codes[10] == 429
    blocked = await checkout(client, via_proxy("41.58.1.1"))
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 60
    # Another shopper on a different IP is unaffected.
    assert (await checkout(client, via_proxy("102.89.2.2"))).status_code == 422


async def test_forged_forwarded_for_is_ignored(
    client: AsyncClient, stocked: list[Variant], paystack
) -> None:  # type: ignore[no-untyped-def]
    """Without our proxy's secret, X-Forwarded-For is just a header anyone can set; trusting
    it would let a script dodge the limit by sending a new fake IP each time."""
    codes = [
        (await checkout(client, {"X-Forwarded-For": f"10.0.0.{i}"})).status_code for i in range(11)
    ]
    assert codes[10] == 429  # all counted against the real (socket) address
    wrong_secret = {"X-Proxy-Auth": "guess", "X-Forwarded-For": "10.9.9.9"}
    assert (await checkout(client, wrong_secret)).status_code == 429


async def test_signed_in_limit_follows_the_account_across_ips(
    client: AsyncClient, stocked: list[Variant], paystack
) -> None:  # type: ignore[no-untyped-def]
    await sign_up(client)
    codes = [(await checkout(client, via_proxy(f"197.210.0.{i}"))).status_code for i in range(11)]
    assert codes[10] == 429  # new IP every time, same account


async def test_login_is_limited_per_ip(client: AsyncClient) -> None:
    codes = [
        (
            await client.post(
                "/auth/login",
                json={"email": "who@example.com", "password": "guess"},
                headers=via_proxy("41.1.1.1"),
            )
        ).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10 and codes[10] == 429
