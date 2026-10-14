"""The outbox worker (ADR-002).

- **Claim** with `FOR UPDATE SKIP LOCKED`: two or more workers never take the same job and
  never wait on each other. A claim sets `status='running'`, a lease (`locked_until`) and
  bumps `attempts`.
- **Lease:** a worker that dies mid-job loses nothing. When the lease runs out, the job is
  claimable again. Handlers are time-boxed below the lease, so a live worker never overruns it.
- **Fencing:** finishing a job only counts if `attempts` still equals the value this worker
  claimed it with. A worker whose lease expired (and whose job someone else re-claimed) can't
  then mark it done or failed.
- **One transaction per job:** the handler's own database writes and "job done" commit
  together, so a crash can't leave the writes without the done mark or the reverse.
- **Retries:** exponential backoff with jitter; after MAX_ATTEMPTS the job is `dead` (the
  dead-letter queue) and an admin is alerted.
- **At least once:** a job can run twice (killed after its side effect, before commit), so
  every side effect carries its own idempotency key (see app/platform/jobs.py).
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.platform.logging import request_id_var
from app.platform.models import OutboxJob

log = logging.getLogger("app.worker")

MAX_ATTEMPTS = 6
LEASE = timedelta(seconds=60)
BASE_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class Job:
    id: int
    type: str
    payload: dict[str, Any]
    attempts: int  # the fencing token for this claim


class PermanentJobError(Exception):
    """Retrying won't help (bad payload, unknown order): go straight to dead."""


Handler = Callable[[AsyncSession, Job], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return register


def backoff(attempts: int) -> timedelta:
    """2 s, 4 s, 8 s, ... with full jitter, so jobs that failed together don't retry in step."""
    return timedelta(seconds=random.uniform(0, BASE_BACKOFF_SECONDS * 2**attempts))


async def claim(session: AsyncSession, limit: int = 5, lease: timedelta = LEASE) -> list[Job]:
    """Take up to `limit` ready jobs: queued and due, or running with an expired lease (their
    worker died). Commits, so the claim is visible to other workers at once."""
    ready = (
        select(OutboxJob.id)
        .where(
            ((OutboxJob.status == "queued") & (OutboxJob.run_after <= func.now()))
            | ((OutboxJob.status == "running") & (OutboxJob.locked_until < func.now()))
        )
        .order_by(OutboxJob.run_after, OutboxJob.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = await session.execute(
        update(OutboxJob)
        .where(OutboxJob.id.in_(ready.scalar_subquery()))
        .values(
            status="running",
            locked_until=func.now() + lease,
            attempts=OutboxJob.attempts + 1,
            updated_at=func.now(),
        )
        .returning(OutboxJob.id, OutboxJob.type, OutboxJob.payload, OutboxJob.attempts)
    )
    jobs = [Job(*row) for row in rows]
    await session.commit()
    return sorted(jobs, key=lambda j: j.id)


def _fenced(job: Job):  # type: ignore[no-untyped-def]
    return (
        (OutboxJob.id == job.id)
        & (OutboxJob.status == "running")
        & (OutboxJob.attempts == job.attempts)
    )


async def _finish(session: AsyncSession, job: Job) -> bool:
    result = await session.execute(
        update(OutboxJob)
        .where(_fenced(job))
        .values(status="done", locked_until=None, last_error=None, updated_at=func.now())
    )
    return result.rowcount == 1  # type: ignore[attr-defined]


async def _fail(session: AsyncSession, job: Job, error: str, permanent: bool) -> str:
    dead = permanent or job.attempts >= MAX_ATTEMPTS
    result = await session.execute(
        update(OutboxJob)
        .where(_fenced(job))
        .values(
            status="dead" if dead else "queued",
            run_after=func.now() + backoff(job.attempts),
            locked_until=None,
            last_error=error[:2000],
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        return "lost_lease"
    if dead and job.type != "admin_alert":  # an alert about a dead alert would loop
        from app.platform.outbox import enqueue

        reason = f"job {job.id} ({job.type}) is dead after {job.attempts} attempts: {error}"
        await enqueue(session, "admin_alert", {"reason": reason[:400]})
    return "dead" if dead else "retry"


async def run_job(
    sessionmaker: async_sessionmaker[AsyncSession], job: Job, lease: timedelta = LEASE
) -> str:
    """Run one claimed job. Returns done / retry / dead / lost_lease."""
    token = request_id_var.set(job.payload.get("request_id") or f"job-{job.id}")
    try:
        fn = HANDLERS.get(job.type)
        async with sessionmaker() as session:
            try:
                if fn is None:
                    raise PermanentJobError(f"no handler for job type {job.type!r}")
                # Time-boxed inside the lease: a stuck handler fails here, before another
                # worker could reclaim the job while this one is still running it.
                await asyncio.wait_for(fn(session, job), timeout=lease.total_seconds() * 0.8)
                if await _finish(session, job):
                    await session.commit()
                    log.info(
                        "job done",
                        extra={"job_id": job.id, "job_type": job.type, "attempt": job.attempts},
                    )
                    return "done"
                await session.rollback()  # our lease expired and someone else has it now
                log.warning("job lease lost", extra={"job_id": job.id, "job_type": job.type})
                return "lost_lease"
            except Exception as exc:  # noqa: BLE001 - any failure is recorded on the job
                await session.rollback()  # undo the handler's partial writes
                permanent = isinstance(exc, PermanentJobError)
                outcome = await _fail(session, job, f"{exc.__class__.__name__}: {exc}", permanent)
                await session.commit()
                log.warning(
                    "job failed",
                    extra={
                        "job_id": job.id,
                        "job_type": job.type,
                        "attempt": job.attempts,
                        "outcome": outcome,
                        "error": str(exc)[:300],
                    },
                )
                return outcome
    finally:
        request_id_var.reset(token)


async def run_once(sessionmaker: async_sessionmaker[AsyncSession], limit: int = 5) -> list[str]:
    async with sessionmaker() as session:
        jobs = await claim(session, limit)
    return [await run_job(sessionmaker, job) for job in jobs]


async def run_forever(
    sessionmaker: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    poll_seconds: float = 1.0,
    lease: timedelta = LEASE,
) -> None:
    """Poll for jobs until `stop` is set. A job in progress finishes first (or its lease runs
    out and another worker takes it)."""
    from app.platform.schedule import ensure_scheduled

    log.info("worker started")
    next_schedule_check = 0.0
    while not stop.is_set():
        try:
            if asyncio.get_running_loop().time() >= next_schedule_check:
                # Re-check every few minutes, in case a scheduled job went dead.
                async with sessionmaker() as session:
                    await ensure_scheduled(session)
                next_schedule_check = asyncio.get_running_loop().time() + 300
            async with sessionmaker() as session:
                jobs = await claim(session, lease=lease)
            outcomes = [await run_job(sessionmaker, job, lease) for job in jobs]
        except Exception:  # noqa: BLE001 - e.g. database unreachable: wait and try again
            log.exception("worker loop error")
            outcomes = []
        if not outcomes:
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
    log.info("worker stopped")


__all__ = [
    "HANDLERS",
    "Job",
    "PermanentJobError",
    "claim",
    "handler",
    "run_forever",
    "run_job",
    "run_once",
]
