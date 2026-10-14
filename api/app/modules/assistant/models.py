import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, created_at, uuid_pk


class AssistantSession(Base):
    __tablename__ = "assistant_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = created_at()


class AssistantMessage(Base):
    """One message of a conversation, exactly as sent to (or received from) the model, so a
    conversation can be replayed for evals and debugging.

    role: 'user' (the shopper's text), 'assistant' (the model's content blocks), 'tool'
    (tool results, sent to the model as a user message) or 'system' (a correction the price
    check added). A reply the price check rejected is kept with content.rejected set, and is
    left out when the history is sent to the model again.
    """

    __tablename__ = "assistant_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool', 'system')", name="role_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Message order. created_at can't give it: now() is the same for every row written in
    # one transaction, and a turn writes several.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistant_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = created_at()


class AssistantSpend(Base):
    """Money spent on the assistant per UTC day: the daily spend cap reads this row."""

    __tablename__ = "assistant_spend"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, server_default="0")
