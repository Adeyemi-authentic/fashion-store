"""Catalog API latency under a fixed request rate (open loop).

    uv run python -m scripts.load.catalog_latency                     # 50, 100, 200, 400 req/s
    uv run python -m scripts.load.catalog_latency --rates 100 --seconds 30
    uv run python -m scripts.load.catalog_latency --target https://api.example.com --rates 20

Target (PLAN Phase 8): p95 < 200 ms for cached catalog reads, at a rate written down next
to the result. Without Redis (ADR-006): if this misses, Redis is the measured reason to add it.

Open loop: requests are sent on a fixed schedule (one every 1/rate seconds) whatever the
server is doing, and each latency is measured from when the request was *due*, not when it
was sent. A closed loop (send, wait, send) slows down when the server does, so a struggling
server looks fast; this is the "coordinated omission" mistake. The generator also reports
how late it was itself: if it can't keep the schedule, the numbers describe the laptop, not
the API.

Without --target: a throwaway local Postgres, seeded, and the real API under uvicorn with
ONE worker (the free Render plan runs one instance). Same machine as the generator, so treat
the local numbers as the API's own cost, not a production figure; Phase 9 re-runs this
against the deployed API with --target.

The request mix is what a shopper's pages call: listings (by category and sort), product
pages, categories and facets. Each run starts with a warm-up so reads are cached.
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from alembic import command

from scripts.eval_search import start_database
from scripts.load.checkout_race import wait_for

OUT = Path(__file__).resolve().parents[3] / "evals" / "load"
PORT = 8792
# The generator's connection pool. Small on purpose: httpx's pool slows down badly with
# hundreds of connections, and the generator then fell behind its own schedule at ~150 req/s
# while the API could do ~3,800 (checked with ApacheBench). Requests waiting for a free
# connection still count, since latency is measured from when each request was due.
POOL = 32
SORTS = ["newest", "price_asc", "price_desc", "name"]


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


async def build_paths(client: httpx.AsyncClient, rng: random.Random) -> list[str]:
    cats = [c["slug"] for c in (await client.get("/categories")).json()]
    slugs: list[str] = []
    cursor = None
    while True:
        q = "/products?sort=name&limit=60" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(q)).json()
        slugs += [p["slug"] for p in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    paths = []
    for _ in range(2000):
        r = rng.random()
        if r < 0.45:
            paths.append(f"/products/{rng.choice(slugs)}")
        elif r < 0.85:
            paths.append(f"/products?category={rng.choice(cats)}&sort={rng.choice(SORTS)}&limit=24")
        elif r < 0.95:
            paths.append(f"/facets?category={rng.choice(cats)}")
        else:
            paths.append("/categories")
    return paths


PROCS = 4  # generator processes: one Python process can't keep up with a few hundred req/s


async def _share(
    base: str, paths: list[str], rate: int, seconds: float, start: float, k: int, procs: int
) -> tuple[list[float], list[float], list[float], dict[str, int]]:
    """Generator process k of `procs`: sends requests k, k+procs, k+2*procs... of the shared
    schedule (request j is due at start + j/rate), so together they hit the overall rate."""
    limits = httpx.Limits(max_connections=POOL, max_keepalive_connections=POOL)
    latencies: list[float] = []
    service: list[float] = []
    lateness: list[float] = []
    errors: dict[str, int] = {}
    async with httpx.AsyncClient(base_url=base, timeout=60, limits=limits) as client:
        await client.get("/categories")  # open a connection before the clock starts

        async def fire(j: int) -> None:
            due = start + j / rate
            delay = due - time.time()
            if delay > 0:
                await asyncio.sleep(delay)
            sent = time.time()
            lateness.append(max(0.0, sent - due))
            try:
                r = await client.get(paths[j % len(paths)])
                if r.status_code != 200:
                    errors[str(r.status_code)] = errors.get(str(r.status_code), 0) + 1
            except httpx.HTTPError as exc:
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            done = time.time()
            latencies.append(done - due)  # from when it was due: the strict number
            service.append(done - sent)  # from when it was sent

        await asyncio.gather(*(fire(j) for j in range(k, int(rate * seconds), procs)))
    return latencies, service, lateness, errors


def _child(job: tuple[str, list[str], int, float, float, int, int]) -> dict[str, Any]:
    cpu0, t0 = time.process_time(), time.time()
    lat, service, late, errors = asyncio.run(_share(*job))
    busy = (time.process_time() - cpu0) / max(time.time() - t0, 1e-9)
    return {"lat": lat, "service": service, "late": late, "errors": errors, "cpu": busy}


def one_rate(base: str, paths: list[str], rate: int, seconds: float) -> dict[str, Any]:
    # Warm up: every distinct path once, so the measured reads are cache hits (the cache
    # holds them for 60 s, longer than a run).
    async def warm() -> None:
        distinct = sorted(set(paths))
        async with httpx.AsyncClient(base_url=base, timeout=60) as client:
            for i in range(0, len(distinct), 50):
                await asyncio.gather(*(client.get(p) for p in distinct[i : i + 50]))

    asyncio.run(warm())
    start = time.time() + 2.0  # time for every process to start and connect
    jobs = [(base, paths, rate, seconds, start, k, PROCS) for k in range(PROCS)]
    with ProcessPoolExecutor(PROCS) as pool:
        parts = list(pool.map(_child, jobs))
    elapsed = time.time() - start

    ms = [x * 1000 for p in parts for x in p["lat"]]
    late_ms = [x * 1000 for p in parts for x in p["late"]]
    service_ms = [x * 1000 for p in parts for x in p["service"]]
    errors: dict[str, int] = {}
    for p in parts:
        for key, n in p["errors"].items():
            errors[key] = errors.get(key, 0) + n
    return {
        "rate_rps": rate,
        "seconds": seconds,
        "requests": len(ms),
        "achieved_rps": round(len(ms) / elapsed, 1),
        "p50_ms": round(pct(ms, 0.50), 1),
        "p95_ms": round(pct(ms, 0.95), 1),
        "p99_ms": round(pct(ms, 0.99), 1),
        "max_ms": round(max(ms), 1),
        "mean_ms": round(statistics.fmean(ms), 1),
        # From when each request was actually sent. Differs from p95_ms by the generator's
        # own lateness: on macOS mostly idle processes wake a few ms late (timer coalescing),
        # which inflates the strict number at low rates. The target is judged on p95_ms.
        "p95_from_send_ms": round(pct(service_ms, 0.95), 1),
        "errors": errors,
        "generator_late_p95_ms": round(pct(late_ms, 0.95), 1),
        # Above ~0.8 a generator process is itself the bottleneck: the numbers then
        # describe the generator, not the API.
        "generator_cpu_max": round(max(p["cpu"] for p in parts), 2),
        "meets_target": pct(ms, 0.95) < 200 and not errors,
    }


def measure(base: str, rates: list[int], seconds: float) -> list[dict[str, Any]]:
    async def get_paths() -> list[str]:
        async with httpx.AsyncClient(base_url=base, timeout=30) as c:
            return await build_paths(c, random.Random(8))

    paths = asyncio.run(get_paths())
    out = []
    for rate in rates:
        r = one_rate(base, paths, rate, seconds)
        print(
            f"{rate:>4} req/s: p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms "
            f"(from send {r['p95_from_send_ms']}), p99 {r['p99_ms']} ms, "
            f"errors {r['errors'] or 0}, generator late p95 {r['generator_late_p95_ms']} ms, "
            f"generator cpu {r['generator_cpu_max']}"
        )
        out.append(r)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rates", nargs="*", type=int, default=[50, 100, 200, 400, 800, 1600])
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--target", help="an already running API (default: start one locally)")
    parser.add_argument("--workers", type=int, default=1, help="uvicorn workers (local only)")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.target:
        results = measure(args.target, args.rates, args.seconds)
        where = args.target
    else:
        logs = Path(tempfile.mkdtemp(prefix="flyfit-load-"))
        server, url = start_database()
        proc = None
        try:
            from tests.conftest import alembic_config

            os.environ.update(DATABASE_URL=url, DATABASE_URL_DIRECT=url)
            command.upgrade(alembic_config(url), "head")
            seed = subprocess.run(
                [sys.executable, "-m", "scripts.seed_catalog"],
                env=dict(os.environ),
                capture_output=True,
            )
            if seed.returncode != 0:
                raise SystemExit(seed.stderr.decode()[-2000:])
            env = {
                **os.environ,
                "ANTHROPIC_API_KEY": "",
                "VOYAGE_API_KEY": "",
                "SENTRY_DSN": "",
                "LOG_LEVEL": "WARNING",
                "PAYSTACK_SECRET_KEY": "",
            }
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--port",
                    str(PORT),
                    "--workers",
                    str(args.workers),
                    "--log-level",
                    "warning",
                    # Longer than the client keeps idle connections: otherwise the server
                    # closes one just as the client reuses it (a spurious ReadError).
                    "--timeout-keep-alive",
                    "75",
                ],
                env=env,
                stdout=(logs / "api.log").open("wb"),
                stderr=subprocess.STDOUT,
            )
            base = f"http://127.0.0.1:{PORT}"
            asyncio.run(wait_for(f"{base}/health"))
            results = measure(base, args.rates, args.seconds)
            where = (
                f"local: {args.workers} uvicorn worker(s), local Postgres, generator on the "
                "same machine"
            )
        finally:
            if proc:
                proc.terminate()
                proc.wait(timeout=20)
            server.cleanup()

    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "where": where,
        "mix": "45% product page, 40% listing (category + sort), 10% facets, 5% categories",
        "results": results,
    }
    name = "catalog_latency.json" if not args.target else "catalog_latency_remote.json"
    (OUT / name).write_text(json.dumps(report, indent=2))
    print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
