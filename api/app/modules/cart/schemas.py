from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.catalog.schemas import ImageOut

MAX_LINE_QTY = 10  # per variant; a shop limit, not a stock rule


class AddItemIn(BaseModel):
    variant_id: UUID
    qty: int = Field(default=1, ge=1, le=MAX_LINE_QTY)


class SetQtyIn(BaseModel):
    qty: int = Field(ge=0, le=MAX_LINE_QTY)  # 0 removes the line


class LineProblem(StrEnum):
    unavailable = "unavailable"  # product taken off sale
    sold_out = "sold_out"
    not_enough_stock = "not_enough_stock"  # qty in cart > available now


class CartLineOut(BaseModel):
    variant_id: UUID
    sku: str
    product_slug: str
    product_name: str
    size: str
    colour: str
    image: ImageOut | None
    unit_price_kobo: int
    qty: int
    line_total_kobo: int
    available: int
    problem: LineProblem | None


class CartOut(BaseModel):
    """A cart is a wish list with prices, not a stock hold. Stock is reserved at checkout
    (Phase 4), so `available` and `problem` can change between page loads."""

    items: list[CartLineOut]
    item_count: int
    subtotal_kobo: int
    can_checkout: bool
