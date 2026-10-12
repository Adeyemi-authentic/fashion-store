import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, created_at, updated_at, uuid_pk


class Cart(Base):
    __tablename__ = "carts"
    __table_args__ = (
        CheckConstraint("customer_id IS NOT NULL OR session_token IS NOT NULL", name="has_owner"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    session_token: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("cart_id", "variant_id", name="uq_cart_items_cart_variant"),
        CheckConstraint("qty > 0", name="qty_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()
