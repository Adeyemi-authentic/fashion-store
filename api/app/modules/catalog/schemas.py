from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProductSort(StrEnum):
    newest = "newest"
    price_asc = "price_asc"
    price_desc = "price_desc"
    name = "name"


class ImageOut(BaseModel):
    url: str
    alt: str


class ProductCard(BaseModel):
    """One product in a listing. Money is always integer kobo (ADR-004)."""

    model_config = ConfigDict(strict=True)

    id: UUID
    slug: str
    name: str
    category: str
    price_kobo: int  # lowest variant price ("from ₦X")
    in_stock: bool
    image: ImageOut | None


class ProductPage(BaseModel):
    items: list[ProductCard]
    next_cursor: str | None


class VariantOut(BaseModel):
    id: UUID
    sku: str
    size: str
    colour: str
    price_kobo: int
    available: int


class ProductDetail(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str
    category: str
    price_kobo: int
    attributes: dict[str, Any]
    in_stock: bool
    variants: list[VariantOut]
    images: list[ImageOut]


class CategoryOut(BaseModel):
    slug: str
    product_count: int
