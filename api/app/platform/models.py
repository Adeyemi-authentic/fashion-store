from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, created_at, updated_at


class OutboxJob(Base):
    """The job queue (ADR-002). Rows are written in the same transaction as their cause."""

    __tablename__ = "outbox_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'done', 'dead')", name="status_valid"),
        Index("ix_outbox_jobs_ready", "run_after", postgresql_where=text("status = 'queued'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(Text, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


# Fixed-window rate-limit counters (ADR-006). UNLOGGED: faster writes, emptied after a
# crash, which is acceptable for rate limits. A plain Table because it has no ORM behaviour.
rate_limits = Table(
    "rate_limits",
    Base.metadata,
    Column("key", Text, nullable=False),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False, server_default="0"),
    PrimaryKeyConstraint("key", "window_start", name="pk_rate_limits"),
    prefixes=["UNLOGGED"],
)
