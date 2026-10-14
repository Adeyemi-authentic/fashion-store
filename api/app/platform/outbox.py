"""Write jobs to the outbox (ADR-002). Always called inside the transaction that caused the
job, so the job exists if and only if that change committed. Phase 5 adds the worker."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.logging import request_id_var
from app.platform.models import OutboxJob

JOB_TYPES = frozenset(
    {
        "order_confirmation_email",
        "refund_payment",
        "late_payment_apology_email",
        "admin_alert",
        "expire_holds",
        "low_stock_alert",
        "cleanup",
        "embed_product",
        "test_job",  # used only by the worker tests
    }
)


async def enqueue(
    session: AsyncSession,
    job_type: str,
    payload: dict[str, Any],
    *,
    run_after: datetime | None = None,
    singleton_key: str | None = None,
) -> None:
    """Add a job in the caller's transaction. With `singleton_key`, does nothing if a queued
    or running job with that key already exists (used by scheduled jobs)."""
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type {job_type}")
    # The request that caused the job, so its logs and the job's logs can be joined up.
    payload = {**payload, "request_id": request_id_var.get()}
    values: dict[str, Any] = {"type": job_type, "payload": payload, "singleton_key": singleton_key}
    if run_after is not None:
        values["run_after"] = run_after
    stmt = insert(OutboxJob).values(**values)
    if singleton_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[OutboxJob.singleton_key],
            index_where=text("status IN ('queued', 'running')"),
        )
    await session.execute(stmt)
