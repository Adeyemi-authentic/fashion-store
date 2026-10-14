from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field


class StockAdjustIn(BaseModel):
    delta: Annotated[int, Field(ge=-100_000, le=100_000)]  # +received, -damaged/recount
    reason: Annotated[str, Field(max_length=200)] = ""


class StockOut(BaseModel):
    variant_id: UUID
    stock_on_hand: int
    stock_reserved: int
    available: int


class CreatedOut(BaseModel):
    id: UUID
    slug: str


class OrderOut(BaseModel):
    id: UUID
    status: str
    total_kobo: int
    currency: str
    email: str | None  # the customer's account email, or the guest email
    is_guest: bool
    item_count: int
    needs_review: bool
    created_at: datetime


class OrderPage(BaseModel):
    items: list[OrderOut]
    next_cursor: str | None


class DraftCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class DescriptionDraftOut(BaseModel):
    id: UUID
    product_id: UUID
    product_slug: str
    product_name: str
    current_description: str
    status: str
    text: str
    checks: list[DraftCheckOut]
    attempts: int
    model: str
    input_tokens: int
    output_tokens: int
    created_at: datetime


class QueueDraftsIn(BaseModel):
    """Either a list of products, or every visible product still on its template text."""

    product_ids: Annotated[list[UUID], Field(max_length=200)] = []
    all_template: bool = False


class QueuedOut(BaseModel):
    queued: int


class ApproveDraftIn(BaseModel):
    text: Annotated[str | None, Field(max_length=2000)] = None  # an admin's edit, if any
