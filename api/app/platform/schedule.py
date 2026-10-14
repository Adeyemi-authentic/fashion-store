"""Scheduled jobs. Each is an outbox job that, when it finishes, queues its own next run in
the same transaction, so a crash can't lose the schedule or double it. `singleton_key` keeps
it to one live copy even if two workers both start and both call `ensure_scheduled()`."""

from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.models import OutboxJob, rate_limits
from app.platform.outbox import enqueue
from app.platform.worker import Job, handler

SCHEDULE: dict[str, timedelta] = {
    "expire_holds": timedelta(minutes=1),
    "cleanup": timedelta(hours=1),
}


async def ensure_scheduled(session: AsyncSession) -> None:
    """Make sure each scheduled job has a live copy (worker start, and every few minutes, in
    case one went dead). Commits."""
    for job_type in SCHEDULE:
        await enqueue(session, job_type, {}, singleton_key=f"schedule:{job_type}")
    await session.commit()


async def _next_run(session: AsyncSession, job: Job) -> None:
    # This job still holds the singleton key (status 'running'), which would make the insert
    # below a no-op. Give the key up first; it's all one transaction with the done mark.
    await session.execute(
        update(OutboxJob).where(OutboxJob.id == job.id).values(singleton_key=None)
    )
    await enqueue(
        session,
        job.type,
        {},
        run_after=(await session.execute(select(func.now() + SCHEDULE[job.type]))).scalar_one(),
        singleton_key=f"schedule:{job.type}",
    )


@handler("expire_holds")
async def expire_holds(session: AsyncSession, job: Job) -> None:
    """Tidy-up only: availability already ignores expired holds (ADR-003)."""
    from app.modules.orders.service import expire_overdue_orders

    await expire_overdue_orders(session, limit=500)
    await _next_run(session, job)


@handler("cleanup")
async def cleanup(session: AsyncSession, job: Job) -> None:
    from app.modules.cart.service import delete_idle_guest_carts
    from app.modules.orders.service import delete_old_idempotency_keys

    await session.execute(
        delete(rate_limits).where(rate_limits.c.window_start < func.now() - timedelta(hours=1))
    )
    await delete_old_idempotency_keys(session, older_than=timedelta(hours=24))
    await delete_idle_guest_carts(session, idle_for=timedelta(days=30))
    await session.execute(
        delete(OutboxJob).where(
            OutboxJob.status == "done", OutboxJob.updated_at < func.now() - timedelta(days=7)
        )
    )
    await _next_run(session, job)
