import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, created_at, updated_at, uuid_pk

# See the state machine in docs/ARCHITECTURE.md §3. Phase 4 adds the transition function.
ORDER_STATUSES = (
    "pending_payment",
    "paid",
    "fulfilled",
    "cancelled",
    "expired",
    "refund_pending",
    "refunded",
)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(f"status IN {ORDER_STATUSES}", name="status_valid"),
        CheckConstraint("customer_id IS NOT NULL OR guest_email IS NOT NULL", name="has_buyer"),
        CheckConstraint("guest_email = lower(guest_email)", name="guest_email_lowercase"),
        CheckConstraint("total_kobo > 0", name="total_positive"),
        Index("ix_orders_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id"), nullable=True, index=True
    )
    guest_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The cart this order was checked out from. Paid items are removed from it then, not at
    # checkout, so an abandoned payment doesn't empty the customer's bag.
    cart_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("carts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, default="pending_payment", server_default="pending_payment"
    )
    total_kobo: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="NGN", server_default="NGN")
    paystack_reference: Mapped[str] = mapped_column(Text, unique=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price_kobo > 0", name="unit_price_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id"))
    qty: Mapped[int] = mapped_column(Integer)
    # Copied at purchase time and never read back from products/variants (ADR-004).
    unit_price_kobo: Mapped[int] = mapped_column(BigInteger)
    name_snapshot: Mapped[str] = mapped_column(Text)
    sku_snapshot: Mapped[str] = mapped_column(Text)


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        # Active holds per variant: used to release expired holds and to check that
        # variants.stock_reserved equals the sum of active holds.
        Index(
            "ix_reservations_active",
            "variant_id",
            "expires_at",
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id"))
    qty: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at()


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (CheckConstraint("status IN ('in_progress', 'done')", name="status_valid"),)

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="in_progress", server_default="in_progress")
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[datetime] = created_at()
