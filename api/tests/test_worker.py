import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.platform import worker
from app.platform.models import OutboxJob
from app.platform.outbox import enqueue

ran: Counter[int] = Counter()  # job id -> times the handler ran to the end


@pytest.fixture(autouse=True)
async def clean_jobs(session: AsyncSession) -> AsyncIterator[None]:
    await session.execute(delete(OutboxJob))
    await session.commit()
    ran.clear()
    behaviour.clear()
    yield
    await session.execute(delete(OutboxJob))
    await session.commit()


behaviour: dict[str, object] = {}


@worker.handler("test_job")
async def _test_job(session: AsyncSession, job: worker.Job) -> None:
    mode = job.payload.get("mode", "ok")
    if mode == "slow":
        await asyncio.sleep(float(job.payload.get("seconds", 0.2)))
    if mode == "fail":
        raise RuntimeError("provider said no")
    if mode == "permanent":
        raise worker.PermanentJobError("order does not exist")
    if mode == "write_then_fail":
        await enqueue(session, "admin_alert", {"reason": "written by a handler that then fails"})
        raise RuntimeError("after writing")
    ran[job.id] += 1


async def add(session: AsyncSession, n: int = 1, **payload: object) -> list[int]:
    for _ in range(n):
        await enqueue(session, "test_job", dict(payload))
    await session.commit()
    return list(
        (
            await session.execute(
                select(OutboxJob.id).where(OutboxJob.type == "test_job").order_by(OutboxJob.id)
            )
        ).scalars()
    )


async def job_row(session: AsyncSession, job_id: int) -> OutboxJob:
    session.expire_all()
    row = await session.get(OutboxJob, job_id)
    assert row is not None
    return row


async def test_a_job_runs_and_is_done(session: AsyncSession) -> None:
    (job_id,) = await add(session)
    assert await worker.run_once(get_sessionmaker()) == ["done"]
    row = await job_row(session, job_id)
    assert (row.status, row.attempts, row.locked_until) == ("done", 1, None)
    assert ran[job_id] == 1
    assert await worker.run_once(get_sessionmaker()) == []  # nothing left


async def test_failures_back_off_then_go_dead_and_alert(session: AsyncSession) -> None:
    (job_id,) = await add(session, mode="fail")
    for attempt in range(1, worker.MAX_ATTEMPTS + 1):
        await session.execute(
            update(OutboxJob).where(OutboxJob.id == job_id).values(run_after=datetime.now(UTC))
        )
        await session.commit()
        outcomes = await worker.run_once(get_sessionmaker(), limit=1)
        row = await job_row(session, job_id)
        assert row.attempts == attempt
        if attempt < worker.MAX_ATTEMPTS:
            assert outcomes == ["retry"] and row.status == "queued"
            assert row.run_after > datetime.now(UTC) - timedelta(
                seconds=1
            )  # pushed into the future
        else:
            assert outcomes == ["dead"] and row.status == "dead"
    assert "provider said no" in (row.last_error or "")
    alert = await session.scalar(select(OutboxJob.payload).where(OutboxJob.type == "admin_alert"))
    assert alert is not None and f"job {job_id}" in alert["reason"]


async def test_permanent_errors_skip_the_retries(session: AsyncSession) -> None:
    (job_id,) = await add(session, mode="permanent")
    assert await worker.run_once(get_sessionmaker()) == ["dead"]
    assert (await job_row(session, job_id)).attempts == 1


async def test_unknown_job_type_is_dead_not_stuck(session: AsyncSession) -> None:
    session.add(OutboxJob(type="no_such_type", payload={}))
    await session.commit()
    assert await worker.run_once(get_sessionmaker()) == ["dead"]


async def test_a_failing_handler_writes_nothing(session: AsyncSession) -> None:
    """The handler enqueued a job and then failed: that write is rolled back with it."""
    await add(session, mode="write_then_fail")
    assert await worker.run_once(get_sessionmaker()) == ["retry"]
    alerts = (
        (await session.execute(select(OutboxJob.payload).where(OutboxJob.type == "admin_alert")))
        .scalars()
        .all()
    )
    assert not any("written by a handler" in a["reason"] for a in alerts)


async def test_two_workers_never_run_the_same_job(session: AsyncSession) -> None:
    ids = await add(session, 20, mode="slow", seconds=0.05)
    outcomes = await asyncio.gather(
        *(worker.run_once(get_sessionmaker(), limit=20) for _ in range(3))
    )
    assert sum(len(o) for o in outcomes) == 20  # SKIP LOCKED: shared out, nobody waited
    assert sorted(ran) == ids and set(ran.values()) == {1}


async def test_a_dead_workers_job_is_reclaimed_after_its_lease(session: AsyncSession) -> None:
    """Worker A claims the job and dies before finishing. While A's lease is valid nobody
    else takes it; once it runs out, worker B does, and A's late 'done' can't count."""
    (job_id,) = await add(session)
    async with get_sessionmaker()() as s:
        (claimed_by_a,) = await worker.claim(s, lease=timedelta(minutes=5))
    # A is "dead": it never calls run_job. B looks while the lease is still valid:
    assert await worker.run_once(get_sessionmaker()) == []
    # The lease runs out.
    await session.execute(
        update(OutboxJob)
        .where(OutboxJob.id == job_id)
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    assert await worker.run_once(get_sessionmaker()) == ["done"]  # B
    row = await job_row(session, job_id)
    assert (row.status, row.attempts) == ("done", 2)
    assert ran[job_id] == 1

    # A wakes up (it wasn't dead, just paused) and tries to finish with its old claim:
    assert await worker.run_job(get_sessionmaker(), claimed_by_a) == "lost_lease"
    assert ran[job_id] == 2  # its handler ran (at least once!) but its result didn't count
    assert (await job_row(session, job_id)).attempts == 2


async def test_a_stuck_handler_times_out_inside_its_lease(session: AsyncSession) -> None:
    (job_id,) = await add(session, mode="slow", seconds=5)
    async with get_sessionmaker()() as s:
        (job,) = await worker.claim(s, lease=timedelta(seconds=1))
    assert await worker.run_job(get_sessionmaker(), job, lease=timedelta(seconds=1)) == "retry"
    assert "TimeoutError" in ((await job_row(session, job_id)).last_error or "")


async def test_singleton_jobs_have_one_live_copy(session: AsyncSession) -> None:
    for _ in range(3):
        await enqueue(session, "test_job", {}, singleton_key="nightly")
    await session.commit()
    rows = (
        (
            await session.execute(
                select(OutboxJob.status).where(OutboxJob.singleton_key == "nightly")
            )
        )
        .scalars()
        .all()
    )
    assert rows == ["queued"]
    await worker.run_once(get_sessionmaker())
    await enqueue(session, "test_job", {}, singleton_key="nightly")  # the done one doesn't block
    await session.commit()
    rows = (
        (
            await session.execute(
                select(OutboxJob.status)
                .where(OutboxJob.singleton_key == "nightly")
                .order_by(OutboxJob.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == ["done", "queued"]


async def test_run_forever_stops_cleanly(session: AsyncSession) -> None:
    await add(session, 3)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(get_sessionmaker(), stop, poll_seconds=0.05))
    for _ in range(100):
        if len(ran) == 3:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(ran) == 3


async def test_fencing_a_stale_worker_cannot_finish_a_job_someone_else_now_holds(
    session: AsyncSession,
) -> None:
    """A claims, stalls past its lease; B reclaims and is still running it. A wakes and tries
    to finish: refused, because its claim (attempts=1) is no longer the current one (2).
    Without the fence A would mark the job done while B is mid-way through it."""
    (job_id,) = await add(session)
    async with get_sessionmaker()() as s:
        (claim_a,) = await worker.claim(s, lease=timedelta(minutes=5))
    await session.execute(
        update(OutboxJob)
        .where(OutboxJob.id == job_id)
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    async with get_sessionmaker()() as s:
        (claim_b,) = await worker.claim(s, lease=timedelta(minutes=5))
    assert (claim_a.attempts, claim_b.attempts) == (1, 2)

    assert await worker.run_job(get_sessionmaker(), claim_a) == "lost_lease"
    row = await job_row(session, job_id)
    assert (row.status, row.attempts) == ("running", 2)  # still B's
    assert await worker.run_job(get_sessionmaker(), claim_b) == "done"
