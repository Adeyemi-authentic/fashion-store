import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base


class PaymentEvent(Base):
    """Every Paystack webhook we accept, stored once.

    Paystack payloads have no top-level event id, so the dedupe key is built as
    "<event>:<data.id>" (e.g. "charge.success:4099260516"). A repeat delivery hits the
    primary key and is acknowledged without being processed again (Phase 4).
    """

    __tablename__ = "payment_events"

    paystack_event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
