import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_sessionmaker
from app.platform import worker
from app.platform.models import OutboxJob
from app.platform.outbox import enqueue


def value(text: str, metric: str) -> float:
    """First sample whose line starts with `metric` (name plus labels)."""
    line = next(ln for ln in text.splitlines() if ln.startswith(metric))
    return float(line.rsplit(" ", 1)[1])


async def test_request_latency_is_recorded_by_route_template(client: AsyncClient) -> None:
    await client.get("/products/no-such-product")
    text = (await client.get("/metrics")).text
    assert (
        'http_request_duration_seconds_count{method="GET",route="/products/{slug}",status_class="4xx"}'
        in text
    )


async def test_queue_depth_and_failed_jobs(client: AsyncClient, session: AsyncSession) -> None:
    import tests.test_worker  # noqa: F401  (registers the test_job handler)

    await session.execute(delete(OutboxJob))
    for _ in range(3):
        await enqueue(session, "test_job", {})
    await enqueue(session, "test_job", {"mode": "permanent"})
    await session.commit()
    text = (await client.get("/metrics")).text
    assert value(text, 'outbox_jobs{status="queued"}') == 4

    while await worker.run_once(get_sessionmaker()):
        pass
    text = (await client.get("/metrics")).text
    assert value(text, 'outbox_jobs{status="dead"}') == 1
    assert value(text, 'worker_jobs_total{job_type="test_job",outcome="dead"}') >= 1
    await session.execute(delete(OutboxJob))
    await session.commit()


async def test_cache_lookups_are_exposed(client: AsyncClient) -> None:
    await client.get("/categories")
    await client.get("/categories")
    text = (await client.get("/metrics")).text
    assert value(text, 'catalog_cache_lookups{result="hit"}') >= 1


async def test_metrics_need_the_token_when_one_is_set(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "metrics_token", "scrape-me")
    assert (await client.get("/metrics")).status_code == 401
    assert (
        await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    assert (
        await client.get("/metrics", headers={"Authorization": "Bearer scrape-me"})
    ).status_code == 200
