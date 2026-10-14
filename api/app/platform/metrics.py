"""Prometheus metrics at GET /metrics (PLAN Phase 5): request latency, job queue depth,
failed jobs. Queue numbers are read from Postgres at scrape time, so they're right even
when the worker that would have counted them is asleep."""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.cache import catalog_cache
from app.platform.models import OutboxJob

REGISTRY = CollectorRegistry()

REQUEST_SECONDS = Histogram(
    "http_request_duration_seconds",
    "Request latency by route template",
    ["route", "method", "status_class"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
JOBS_RUN = Counter(
    "worker_jobs_total", "Jobs run by outcome", ["job_type", "outcome"], registry=REGISTRY
)
QUEUE = Gauge("outbox_jobs", "Outbox jobs by status", ["status"], registry=REGISTRY)
OLDEST_QUEUED = Gauge(
    "outbox_oldest_due_seconds", "Age of the oldest due job still waiting", registry=REGISTRY
)
CACHE = Gauge(
    "catalog_cache_lookups", "Catalog cache lookups since start", ["result"], registry=REGISTRY
)


def observe_request(route: str, method: str, status: int, seconds: float) -> None:
    REQUEST_SECONDS.labels(route, method, f"{status // 100}xx").observe(seconds)


async def render(session: AsyncSession) -> tuple[bytes, str]:
    counts = dict(
        (
            await session.execute(select(OutboxJob.status, func.count()).group_by(OutboxJob.status))
        ).all()
    )
    for status in ("queued", "running", "done", "dead"):
        QUEUE.labels(status).set(counts.get(status, 0))
    oldest = await session.scalar(
        select(func.extract("epoch", func.now() - func.min(OutboxJob.run_after))).where(
            OutboxJob.status == "queued", OutboxJob.run_after <= func.now()
        )
    )
    OLDEST_QUEUED.set(float(oldest or 0))
    CACHE.labels("hit").set(catalog_cache.hits)
    CACHE.labels("miss").set(catalog_cache.misses)
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
