"""Overselling and duplicate-webhook test, through the real HTTP stack.

    uv run python -m scripts.load.checkout_race              # 200 buyers, 10 units
    uv run python -m scripts.load.checkout_race --buyers 500 --units 25

What runs (all local, nothing touches Neon or Paystack):
- a throwaway Postgres (pgserver), migrated and seeded;
- the real API under uvicorn with 2 worker processes, so buyers really do land on
  different processes and different connection pools;
- a fake Paystack server (scripts/load/fake_paystack.py) that the API's Paystack client
  is pointed at, so checkout runs its normal code path.

Then:
1. Every buyer gets their own cookie jar and puts 1 unit of the same variant in their bag.
   (A bag is not a hold, so all of them can.)
2. All buyers press "pay" at the same moment (an asyncio barrier).
3. Every order that got through is paid: a signed charge.success webhook for each.
4. One of those webhooks is sent 5 more times at once.
5. Meanwhile a poller reads the variant's stock every 10 ms; the lowest free stock it ever
   saw is reported.

Targets (PLAN Phase 8): exactly `units` paid orders, 0 oversold, stock never negative; the
same webhook 5 times records 1 payment.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.eval_search import start_database

OUT = Path(__file__).resolve().parents[3] / "evals" / "load"
API_PORT, PAYSTACK_PORT = 8791, 8790
API = f"http://127.0.0.1:{API_PORT}"
SECRET = "sk_test_load_" + uuid.uuid4().hex  # this run only; the API signs and checks with it


@dataclass
class Buyer:
    client: httpx.AsyncClient
    status: int = 0
    code: str = ""
    seconds: float = 0.0
    order: dict[str, Any] = field(default_factory=dict)


def result(r: httpx.Response) -> str:
    """The webhook's own word for what it did, or the HTTP status if it crashed."""
    try:
        return str(r.json()["result"])
    except (ValueError, KeyError):
        return f"HTTP {r.status_code}"


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()


def webhook_body(reference: str, amount: int, charge_id: int) -> bytes:
    return json.dumps(
        {
            "event": "charge.success",
            "data": {
                "id": charge_id,
                "reference": reference,
                "status": "success",
                "amount": amount,
                "currency": "NGN",
            },
        }
    ).encode()


async def wait_for(url: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    async with httpx.AsyncClient() as c:
        while time.monotonic() < deadline:
            try:
                if (await c.get(url)).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.3)
    raise SystemExit(f"{url} did not come up")


def start(cmd: list[str], env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(cmd, env=env, stdout=log.open("wb"), stderr=subprocess.STDOUT)


async def run(url: str, buyers_n: int, units: int) -> dict[str, Any]:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        variant_id, sku, price = (
            await conn.execute(
                text(
                    "SELECT v.id, v.sku, v.price_kobo FROM variants v JOIN products p "
                    "ON p.id = v.product_id WHERE p.status = 'active' ORDER BY v.sku LIMIT 1"
                )
            )
        ).one()
        await conn.execute(
            text("UPDATE variants SET stock_on_hand = :u, stock_reserved = 0 WHERE id = :id"),
            {"u": units, "id": variant_id},
        )

    # The poller: lowest free stock and lowest on-hand ever seen during the run.
    seen = {"min_free": units, "min_on_hand": units, "samples": 0}
    stop = asyncio.Event()

    async def poll() -> None:
        async with engine.connect() as conn:
            while not stop.is_set():
                on_hand, reserved = (
                    await conn.execute(
                        text("SELECT stock_on_hand, stock_reserved FROM variants WHERE id = :id"),
                        {"id": variant_id},
                    )
                ).one()
                await conn.rollback()
                seen["min_free"] = min(seen["min_free"], on_hand - reserved)
                seen["min_on_hand"] = min(seen["min_on_hand"], on_hand)
                seen["samples"] += 1
                await asyncio.sleep(0.01)

    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    buyers = [
        Buyer(httpx.AsyncClient(base_url=API, timeout=120, limits=limits)) for _ in range(buyers_n)
    ]
    try:
        # 1. Everyone fills their bag (in batches, so this step isn't the stress).
        for i in range(0, buyers_n, 50):
            resps = await asyncio.gather(
                *(
                    b.client.post("/cart/items", json={"variant_id": str(variant_id), "qty": 1})
                    for b in buyers[i : i + 50]
                )
            )
            bad = [r.status_code for r in resps if r.status_code != 200]
            if bad:
                raise SystemExit(f"adding to bag failed: {bad[:5]}")

        # 2. Everyone checks out at the same moment.
        poller = asyncio.create_task(poll())
        go = asyncio.Event()

        async def checkout(b: Buyer, n: int) -> None:
            await go.wait()
            t0 = time.perf_counter()
            r = await b.client.post(
                "/checkout",
                json={"email": f"buyer{n}@example.com"},
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            b.seconds = time.perf_counter() - t0
            b.status = r.status_code
            try:
                body = r.json()
            except ValueError:  # a crash (500) answers in plain text
                b.code = r.text[:40]
                return
            if r.status_code == 201:
                b.order = body
            else:
                detail = body.get("detail")
                b.code = detail.get("code", "") if isinstance(detail, dict) else str(detail)

        tasks = [asyncio.create_task(checkout(b, n)) for n, b in enumerate(buyers)]
        await asyncio.sleep(0.2)  # every task is now waiting at the barrier
        t_burst = time.perf_counter()
        go.set()
        await asyncio.gather(*tasks)
        burst_seconds = time.perf_counter() - t_burst

        # 3. Pay every order that got through, all at once.
        winners = [b for b in buyers if b.status == 201]
        async with httpx.AsyncClient(base_url=API, timeout=120) as hook:

            async def deliver(body: bytes) -> httpx.Response:
                return await hook.post(
                    "/payments/webhook",
                    content=body,
                    headers={
                        "x-paystack-signature": sign(body),
                        "content-type": "application/json",
                    },
                )

            bodies = [
                webhook_body(b.order["reference"], b.order["total_kobo"], 9_000_000 + i)
                for i, b in enumerate(winners)
            ]
            paid_resps = await asyncio.gather(*(deliver(body) for body in bodies))

            # 4. The first payment's webhook, 5 more times at once.
            dup_resps = (
                await asyncio.gather(*(deliver(bodies[0]) for _ in range(5))) if bodies else []
            )
            forged = await hook.post(
                "/payments/webhook",
                content=bodies[0] if bodies else b"{}",
                headers={"x-paystack-signature": "0" * 128},
            )
        await asyncio.sleep(0.3)
        stop.set()
        await poller

        async with engine.connect() as conn:
            on_hand, reserved = (
                await conn.execute(
                    text("SELECT stock_on_hand, stock_reserved FROM variants WHERE id = :id"),
                    {"id": variant_id},
                )
            ).one()
            statuses = {
                status: n
                for status, n in await conn.execute(
                    text("SELECT status, count(*) FROM orders GROUP BY status")
                )
            }
            paid_units = await conn.scalar(
                text(
                    "SELECT coalesce(sum(i.qty), 0) FROM order_items i JOIN orders o "
                    "ON o.id = i.order_id WHERE o.status = 'paid' AND i.variant_id = :id"
                ),
                {"id": variant_id},
            )
            first_ref = winners[0].order["reference"] if winners else ""
            events_for_first = await conn.scalar(
                text(
                    "SELECT count(*) FROM payment_events WHERE payload->'data'->>'reference' = :r"
                ),
                {"r": first_ref},
            )
            mismatches = await conn.scalar(
                text(
                    "SELECT count(*) FROM variants v WHERE v.stock_reserved <> coalesce(("
                    "SELECT sum(r.qty) FROM reservations r WHERE r.variant_id = v.id "
                    "AND r.released_at IS NULL), 0)"
                )
            )
    finally:
        for b in buyers:
            await b.client.aclose()
        await engine.dispose()

    secs = sorted(b.seconds for b in buyers)
    codes: dict[str, int] = {}
    for b in buyers:
        key = str(b.status) + (f" {b.code}" if b.code else "")
        codes[key] = codes.get(key, 0) + 1
    return {
        "buyers": buyers_n,
        "units": units,
        "sku": sku,
        "checkout_responses": codes,
        "orders_created": len(winners),
        "orders_by_status": statuses,
        "paid_orders": statuses.get("paid", 0),
        "paid_units": int(paid_units),
        "oversold_units": max(0, int(paid_units) - units),
        "final_stock_on_hand": on_hand,
        "final_stock_reserved": reserved,
        "min_free_stock_seen": seen["min_free"],
        "min_on_hand_seen": seen["min_on_hand"],
        "poller_samples": seen["samples"],
        "reserved_mismatch_variants": int(mismatches or 0),
        "webhook_first_delivery": sorted({result(r) for r in paid_resps}),
        "duplicate_webhooks_x5": [result(r) for r in dup_resps],
        "payment_events_for_that_order": int(events_for_first or 0),
        "forged_webhook_status": forged.status_code,
        "burst_seconds": round(burst_seconds, 2),
        "checkout_p50_s": round(statistics.median(secs), 3),
        "checkout_p95_s": round(secs[int(len(secs) * 0.95) - 1], 3),
        "checkout_max_s": round(secs[-1], 3),
        "api_workers": 2,
    }


def verdict(r: dict[str, Any]) -> list[tuple[str, bool]]:
    return [
        (
            "no server errors (sold out is a 409, not a crash)",
            not any(k.startswith("5") for k in r["checkout_responses"]),
        ),
        (f"exactly {r['units']} paid orders", r["paid_orders"] == r["units"]),
        ("0 units oversold", r["oversold_units"] == 0 and r["paid_units"] == r["units"]),
        ("stock never negative", r["min_free_stock_seen"] >= 0 and r["min_on_hand_seen"] >= 0),
        ("stock_reserved matches reservations", r["reserved_mismatch_variants"] == 0),
        (
            "same webhook x5 -> 1 payment",
            r["payment_events_for_that_order"] == 1
            and r["duplicate_webhooks_x5"] == ["duplicate"] * 5,
        ),
        ("forged signature -> 401", r["forged_webhook_status"] == 401),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--buyers", type=int, default=200)
    parser.add_argument("--units", type=int, default=10)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix="flyfit-load-"))
    print(f"server logs: {logs}")
    server, url = start_database()
    procs: list[subprocess.Popen[bytes]] = []
    try:
        from tests.conftest import alembic_config

        os.environ.update(DATABASE_URL=url, DATABASE_URL_DIRECT=url)
        command.upgrade(alembic_config(url), "head")
        seed = subprocess.run(
            [sys.executable, "-m", "scripts.seed_catalog"], env={**os.environ}, capture_output=True
        )
        if seed.returncode != 0:
            raise SystemExit(seed.stderr.decode()[-2000:])

        env = {
            **os.environ,
            "DATABASE_URL": url,
            "DATABASE_URL_DIRECT": url,
            "PAYSTACK_SECRET_KEY": SECRET,
            "PAYSTACK_BASE_URL": f"http://127.0.0.1:{PAYSTACK_PORT}",
            "ANTHROPIC_API_KEY": "",
            "VOYAGE_API_KEY": "",
            "RESEND_API_KEY": "",
            "SENTRY_DSN": "",
            "LOG_LEVEL": "WARNING",
            "RATE_LIMIT_CHECKOUT_PER_MIN": "1000000",  # one IP plays every buyer here
            "EMAIL_OUTBOX_DIR": str(logs / "emails"),
            "PUBLIC_SITE_URL": "http://localhost:3000",  # http: cookies without Secure
        }
        procs.append(
            start(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "scripts.load.fake_paystack:app",
                    "--port",
                    str(PAYSTACK_PORT),
                    "--log-level",
                    "warning",
                ],
                env,
                logs / "fake_paystack.log",
            )
        )
        procs.append(
            start(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--port",
                    str(API_PORT),
                    "--workers",
                    "2",
                    "--log-level",
                    "warning",
                    "--timeout-keep-alive",
                    "75",
                ],
                env,
                logs / "api.log",
            )
        )
        asyncio.run(wait_for(f"http://127.0.0.1:{PAYSTACK_PORT}/stats"))
        asyncio.run(wait_for(f"{API}/health"))
        result = asyncio.run(run(url, args.buyers, args.units))
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=20)
        server.cleanup()

    checks = verdict(result)
    result["checks"] = {name: ok for name, ok in checks}
    (OUT / "checkout_race.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not all(ok for _, ok in checks):
        sys.exit(1)


if __name__ == "__main__":
    main()
