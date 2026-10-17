"""Fixed-window rate limits in Postgres (ADR-006): one upsert per check into the UNLOGGED
`rate_limits` table. UNLOGGED skips the crash-recovery log (faster writes); a crash resets
the counters, which is fine for rate limits. Old windows are deleted by the cleanup job.

Fixed windows let a client burst up to 2x the limit across a minute boundary. Accepted: the
goal is stopping scripted abuse (card testing, password guessing), not exact fairness.
"""

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import get_sessionmaker
from app.platform.models import rate_limits

log = logging.getLogger("app.ratelimit")
WINDOW = timedelta(minutes=1)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    count: int
    retry_after: int  # seconds until the window resets


async def hit(key: str, limit: int) -> Verdict:
    """Count one request against `key` in the current minute. Own short transaction, so the
    count sticks even if the request itself later fails (failed attempts must count too)."""
    async with get_sessionmaker()() as session:
        window = func.date_trunc("minute", func.now())
        # Count and seconds left in one statement: every round trip counts (each search
        # and checkout pays for this one before doing anything else).
        count, seconds_left = (
            await session.execute(
                insert(rate_limits)
                .values(key=key, window_start=window, count=1)
                .on_conflict_do_update(
                    index_elements=["key", "window_start"],
                    set_={"count": rate_limits.c.count + 1},
                )
                .returning(rate_limits.c.count, 60 - func.extract("second", func.now()))
            )
        ).one()
        await session.commit()
    assert count is not None
    return Verdict(count <= limit, count, max(1, int(seconds_left or 1)))


def client_ip(request: Request) -> str:
    """The real client IP. Behind our proxy every request comes from the proxy, so the IP is
    taken from X-Forwarded-For, but only if the request proves it came through our proxy.

    Anything else shares one "direct" key. Shoppers always come through the storefront; a
    direct call is a script. Keying direct calls by socket address doesn't work on Render:
    its load balancer connects from a different address almost every time, so each request
    got a fresh limit (found in Phase 9: 40 direct searches in a second, none limited)."""
    secret = get_settings().proxy_shared_secret
    sent = request.headers.get("x-proxy-auth", "")
    forwarded = request.headers.get("x-forwarded-for", "")
    if secret and forwarded and hmac.compare_digest(sent, secret):
        return forwarded.split(",")[0].strip()
    return "direct"


def limit(name: str, per_minute: Callable[[], int]) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: per-IP limit, plus per-account when signed in (so rotating IPs
    doesn't get around it). Raises 429 with Retry-After."""

    async def check(request: Request) -> None:
        cap = per_minute()
        keys = [f"{name}:ip:{client_ip(request)}"]
        if session_cookie := request.cookies.get("session"):
            # The cookie's own hash: no database lookup needed just to rate-limit.
            keys.append(
                f"{name}:session:{hashlib.sha256(session_cookie.encode()).hexdigest()[:32]}"
            )
        for key in keys:
            verdict = await hit(key, cap)
            if not verdict.allowed:
                log.warning("rate limited", extra={"limit": name, "count": verdict.count})
                raise HTTPException(
                    status_code=429,
                    detail="Too many attempts. Please wait a minute and try again.",
                    headers={"Retry-After": str(verdict.retry_after)},
                )

    return check


checkout_limit = limit("checkout", lambda: get_settings().rate_limit_checkout_per_min)
login_limit = limit("login", lambda: get_settings().rate_limit_login_per_min)
search_limit = limit("search", lambda: get_settings().rate_limit_search_per_min)
assistant_limit = limit("assistant", lambda: get_settings().rate_limit_assistant_per_min)
