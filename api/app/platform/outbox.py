"""Write jobs to the outbox (ADR-002). Always called inside the transaction that caused the
job, so the job exists if and only if that change committed. Phase 5 adds the worker."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.models import OutboxJob

JOB_TYPES = frozenset(
    {
        "order_confirmation_email",
        "refund_payment",
        "late_payment_apology_email",
        "admin_alert",
        "expire_holds",
    }
)


async def enqueue(session: AsyncSession, job_type: str, payload: dict[str, Any]) -> None:
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type {job_type}")
    session.add(OutboxJob(type=job_type, payload=payload))
    await session.flush()
