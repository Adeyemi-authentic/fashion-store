import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, created_at, updated_at, uuid_pk

PRODUCT_STATUSES = ("draft", "active", "archived")
# Who wrote the live description. A re-seed only overwrites "template" ones.
DESCRIPTION_SOURCES = ("template", "admin", "ai_approved")
DRAFT_STATUSES = ("needs_review", "failed_checks", "approved", "rejected", "superseded")
EMBEDDING_DIM = 1024  # Voyage voyage-4 default output size


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(f"status IN {PRODUCT_STATUSES}", name="status_valid"),
        CheckConstraint("base_price_kobo > 0", name="base_price_positive"),
        CheckConstraint(
            f"description_source IN {DESCRIPTION_SOURCES}", name="description_source_valid"
        ),
        # Listing pages filter by status + category and keyset-paginate by these sort keys.
        Index("ix_products_listing_newest", "status", "created_at", "id"),
        Index("ix_products_listing_price", "status", "base_price_kobo", "id"),
        Index("ix_products_listing_name", "status", "name", "id"),
        Index("ix_products_category", "category"),
        Index("ix_products_search_tsv", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    description_source: Mapped[str] = mapped_column(
        Text, default="template", server_default="template"
    )
    category: Mapped[str] = mapped_column(Text)
    # Lowest variant price ("from ₦X"). Seed and admin keep it equal to min(variant price).
    base_price_kobo: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, default="draft", server_default="draft")
    # Structured facts (fabric, pattern, gender, occasion, fit). The Phase 6 description
    # pipeline may only make claims that appear here.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    # Hash of the model name + the text the embedding was made from. A different hash means
    # the embedding is stale (the product text or the embedding model changed).
    embedding_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(name, '') || ' ' || coalesce(category, '') "
            "|| ' ' || coalesce(description, ''))",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    variants: Mapped[list["Variant"]] = relationship(
        back_populates="product", order_by="Variant.sku", cascade="all, delete-orphan"
    )
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product", order_by="ProductImage.position", cascade="all, delete-orphan"
    )


class Variant(Base):
    __tablename__ = "variants"
    __table_args__ = (
        UniqueConstraint("product_id", "size", "colour", name="uq_variants_product_size_colour"),
        CheckConstraint("price_kobo > 0", name="price_positive"),
        CheckConstraint("stock_on_hand >= 0", name="on_hand_non_negative"),
        # The overselling backstop (ADR-003): if application code is ever wrong, the database
        # refuses the write instead of letting reserved stock exceed what exists.
        CheckConstraint(
            "stock_reserved >= 0 AND stock_reserved <= stock_on_hand",
            name="reserved_within_on_hand",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[str] = mapped_column(Text)
    colour: Mapped[str] = mapped_column(Text)
    price_kobo: Mapped[int] = mapped_column(BigInteger)
    # Only orders.inventory writes these two columns (ARCHITECTURE.md, module ownership).
    stock_on_hand: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stock_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    product: Mapped[Product] = relationship(back_populates="variants")


class ProductImage(Base):
    __tablename__ = "product_images"
    __table_args__ = (
        UniqueConstraint("product_id", "position", name="uq_product_images_product_position"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    alt: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="images")


class DescriptionDraft(Base):
    """An LLM-drafted description and its automatic check results, waiting for an admin.
    At most one open (needs_review or failed_checks) draft per product."""

    __tablename__ = "description_drafts"
    __table_args__ = (
        CheckConstraint(f"status IN {DRAFT_STATUSES}", name="status_valid"),
        Index(
            "uq_description_drafts_one_open",
            "product_id",
            unique=True,
            postgresql_where=text("status IN ('needs_review', 'failed_checks')"),
        ),
        Index("ix_description_drafts_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    # [{"name": ..., "passed": bool, "detail": ...}], one per check, from the last attempt.
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    attempts: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at()
