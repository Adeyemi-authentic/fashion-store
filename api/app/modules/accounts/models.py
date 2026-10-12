import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, created_at, uuid_pk


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        # Emails are stored lowercased so the UNIQUE constraint is case-insensitive.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("role IN ('customer', 'admin')", name="role_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)  # argon2
    role: Mapped[str] = mapped_column(Text, default="customer", server_default="customer")
    created_at: Mapped[datetime] = created_at()


class Session(Base):
    """Server-side login sessions. The cookie holds a random token; only its hash is stored."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
