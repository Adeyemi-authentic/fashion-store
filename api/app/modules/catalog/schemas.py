from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


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


class StockOut(BaseModel):
    variant_id: UUID
    available: int


class CategoryOut(BaseModel):
    slug: str
    product_count: int


class FacetsOut(BaseModel):
    """Filter options for a listing page, taken from what is actually on sale."""

    sizes: list[str]
    colours: list[str]
    min_price_kobo: int | None
    max_price_kobo: int | None


# ---------------------------------------------------------------------------------------------
# Admin. Stock numbers are shown here but only changed through orders.inventory (ADR-003).

ProductStatus = Literal["draft", "active", "archived"]
CategorySlug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=40)]
ImageUrl = Annotated[str, StringConstraints(pattern=r"^/images/[\w./-]+$", max_length=200)]
Kobo = Annotated[int, Field(gt=0, le=1_000_000_000)]  # up to ₦10m per item


class AdminVariantOut(BaseModel):
    id: UUID
    sku: str
    size: str
    colour: str
    price_kobo: int
    stock_on_hand: int
    stock_reserved: int
    available: int


class AdminProductOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str
    category: str
    status: ProductStatus
    price_kobo: int
    attributes: dict[str, Any]
    image: ImageOut | None
    variants: list[AdminVariantOut]
    created_at: datetime
    updated_at: datetime


class AdminProductRow(BaseModel):
    id: UUID
    slug: str
    name: str
    category: str
    status: ProductStatus
    price_kobo: int
    variant_count: int
    available: int
    image: ImageOut | None


class AdminProductPage(BaseModel):
    items: list[AdminProductRow]
    next_cursor: str | None


class VariantIn(BaseModel):
    size: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    colour: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    price_kobo: Kobo
    stock_on_hand: Annotated[int, Field(ge=0, le=100_000)] = 0


class ProductCreateIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    category: CategorySlug
    description: Annotated[str, StringConstraints(max_length=5000)] = ""
    status: Literal["draft", "active"] = "draft"
    attributes: dict[str, Any] = {}
    image_url: ImageUrl | None = None
    variants: Annotated[list[VariantIn], Field(min_length=1, max_length=60)]


class ProductUpdateIn(BaseModel):
    """Only the fields sent are changed. The slug never changes (links and caches use it)."""

    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
        | None
    ) = None
    category: CategorySlug | None = None
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    status: ProductStatus | None = None
    attributes: dict[str, Any] | None = None
    image_url: ImageUrl | None = None


class VariantPriceIn(BaseModel):
    price_kobo: Kobo
