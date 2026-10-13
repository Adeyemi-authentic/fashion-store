"""The only place stock columns are read for availability or written (ADR-003).

Phase 3 adds admin stock adjustments. Phase 4 adds holds, releases and the late-payment path,
and changes `available_qty` to ignore holds whose expires_at has passed (expiry checked on read).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Variant


def available_qty() -> ColumnElement[int]:
    """SQL expression: units of a variant that can be sold right now."""
    return Variant.stock_on_hand - Variant.stock_reserved


class VariantNotFound(LookupError):
    pass


@dataclass
class StockBelowReserved(Exception):
    """The change would leave fewer units on hand than are held for unpaid orders."""

    on_hand: int
    reserved: int


@dataclass(frozen=True)
class StockLevel:
    on_hand: int
    reserved: int


async def adjust_stock(session: AsyncSession, variant_id: uuid.UUID, delta: int) -> StockLevel:
    """Add (goods received) or remove (damaged, recount) units. Doesn't commit.

    A relative change in one conditional UPDATE, not "read the number, then write a new one":
    if a checkout reserves a unit between an admin's read and write, an absolute write would
    silently undo it. Here the database applies the delta to whatever the row holds at that
    moment, and refuses if on-hand would drop below what is reserved.
    """
    row = (
        await session.execute(
            update(Variant)
            .where(
                Variant.id == variant_id, Variant.stock_on_hand + delta >= Variant.stock_reserved
            )
            .values(stock_on_hand=Variant.stock_on_hand + delta, updated_at=func.now())
            .returning(Variant.stock_on_hand, Variant.stock_reserved)
        )
    ).one_or_none()
    if row is not None:
        return StockLevel(*row)
    current = (
        await session.execute(
            select(Variant.stock_on_hand, Variant.stock_reserved).where(Variant.id == variant_id)
        )
    ).one_or_none()
    if current is None:
        raise VariantNotFound(variant_id)
    raise StockBelowReserved(on_hand=current.stock_on_hand, reserved=current.stock_reserved)
