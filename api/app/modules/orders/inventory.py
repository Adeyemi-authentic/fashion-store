"""The only place stock columns are read for availability or written (ADR-003).

Two records of every hold, always changed together in one transaction:
- `variants.stock_reserved`: a counter, cheap to check in the conditional UPDATE
- `reservations` rows: who holds what until when (`released_at IS NULL` = still held)

**Expiry is checked on read.** A hold past its `expires_at` stops counting the moment it
expires, even if nothing has released it yet: `available_qty()` adds expired-but-unreleased
holds back. Releasing them (checkout does it for the variants it touches, the expiry job does
the rest) is tidying up, so stock is right even when the worker is asleep.

**Lock order, everywhere:** order row -> reservation rows -> variant rows (variants in id
order). Checkout has no order yet and takes variants first, but it only touches other
orders' reservations with SKIP LOCKED, so it never waits on a lock in the wrong direction.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Variant
from app.modules.orders.availability import available_qty
from app.modules.orders.models import Reservation


class VariantNotFound(LookupError):
    pass


@dataclass
class StockBelowReserved(Exception):
    """The change would leave fewer units on hand than are held for unpaid orders."""

    on_hand: int
    reserved: int


@dataclass
class OutOfStock(Exception):
    variant_ids: list[uuid.UUID]


@dataclass(frozen=True)
class StockLevel:
    on_hand: int
    reserved: int


Items = list[tuple[uuid.UUID, int]]  # (variant_id, qty)


def _merge(items: Items) -> list[tuple[uuid.UUID, int]]:
    """Same variant twice -> one entry; sorted by id, the order every lock is taken in."""
    totals: dict[uuid.UUID, int] = defaultdict(int)
    for variant_id, qty in items:
        totals[variant_id] += qty
    return sorted(totals.items())


async def _lock_variants(session: AsyncSession, variant_ids: list[uuid.UUID]) -> None:
    await session.execute(
        select(Variant.id).where(Variant.id.in_(variant_ids)).order_by(Variant.id).with_for_update()
    )


async def _decrement_reserved(session: AsyncSession, released: dict[uuid.UUID, int]) -> None:
    for variant_id in sorted(released):
        await session.execute(
            update(Variant)
            .where(Variant.id == variant_id)
            .values(stock_reserved=Variant.stock_reserved - released[variant_id])
        )


async def release_expired_holds(session: AsyncSession, variant_ids: list[uuid.UUID]) -> None:
    """Release expired holds on these variants (any order's). The caller has already locked
    the variant rows. Reservations someone else has locked (e.g. a webhook converting them
    right now) are skipped, never waited for."""
    expired = (
        select(Reservation.id)
        .where(
            Reservation.variant_id.in_(variant_ids),
            Reservation.released_at.is_(None),
            Reservation.expires_at <= func.now(),
        )
        .with_for_update(skip_locked=True)
    )
    rows = await session.execute(
        update(Reservation)
        .where(Reservation.id.in_(expired))
        .values(released_at=func.now())
        .returning(Reservation.variant_id, Reservation.qty)
    )
    released: dict[uuid.UUID, int] = defaultdict(int)
    for variant_id, qty in rows:
        released[variant_id] += qty
    await _decrement_reserved(session, released)


async def reserve(
    session: AsyncSession, order_id: uuid.UUID, items: Items, expires_at: datetime
) -> None:
    """Hold stock for an order: all of it or none. Doesn't commit.

    Raises OutOfStock listing every variant that didn't have enough; the caller rolls back.
    """
    merged = _merge(items)
    ids = [v for v, _ in merged]
    await _lock_variants(session, ids)
    await release_expired_holds(session, ids)
    short: list[uuid.UUID] = []
    for variant_id, qty in merged:
        # The check and the write are one statement on a locked row: nobody can take the
        # last unit between "is there enough?" and "take it".
        result = await session.execute(
            update(Variant)
            .where(Variant.id == variant_id, Variant.stock_on_hand - Variant.stock_reserved >= qty)
            .values(stock_reserved=Variant.stock_reserved + qty)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            short.append(variant_id)
    if short:
        raise OutOfStock(short)
    session.add_all(
        Reservation(order_id=order_id, variant_id=v, qty=q, expires_at=expires_at)
        for v, q in merged
    )
    await session.flush()


async def _lock_order_holds(session: AsyncSession, order_id: uuid.UUID) -> list[Reservation]:
    return list(
        (
            await session.execute(
                select(Reservation)
                .where(Reservation.order_id == order_id, Reservation.released_at.is_(None))
                .order_by(Reservation.variant_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def convert_holds_to_sale(session: AsyncSession, order_id: uuid.UUID, items: Items) -> bool:
    """Payment arrived in time: the held units leave the shelf. Caller holds the order lock.

    Returns False (changing nothing) unless every item is covered by an unexpired hold of
    this order; the caller then treats it as a late payment.
    """
    holds = await _lock_order_holds(session, order_id)
    now = await session.scalar(select(func.now()))
    if not holds or any(r.expires_at <= now for r in holds):  # type: ignore[operator]
        return False
    held: dict[uuid.UUID, int] = defaultdict(int)
    for r in holds:
        held[r.variant_id] += r.qty
    if sorted(held.items()) != _merge(items):
        return False
    for variant_id, qty in sorted(held.items()):
        await session.execute(
            update(Variant)
            .where(Variant.id == variant_id)
            .values(
                stock_on_hand=Variant.stock_on_hand - qty,
                stock_reserved=Variant.stock_reserved - qty,
            )
        )
    await session.execute(
        update(Reservation)
        .where(Reservation.id.in_([r.id for r in holds]))
        .values(released_at=func.now())
    )
    return True


async def release_order_holds(session: AsyncSession, order_id: uuid.UUID) -> None:
    """Give an order's held units back (cancel, expiry, or before a late re-reserve)."""
    holds = await _lock_order_holds(session, order_id)
    if not holds:
        return
    released: dict[uuid.UUID, int] = defaultdict(int)
    for r in holds:
        released[r.variant_id] += r.qty
    await session.execute(
        update(Reservation)
        .where(Reservation.id.in_([r.id for r in holds]))
        .values(released_at=func.now())
    )
    await _decrement_reserved(session, released)


async def sell_without_hold(session: AsyncSession, items: Items) -> bool:
    """Late payment: the hold is gone, so take the units straight off the shelf if they are
    still free. All or nothing (runs in a savepoint). Caller holds the order lock."""
    merged = _merge(items)
    ids = [v for v, _ in merged]
    async with session.begin_nested() as savepoint:
        await _lock_variants(session, ids)
        await release_expired_holds(session, ids)
        for variant_id, qty in merged:
            result = await session.execute(
                update(Variant)
                .where(
                    Variant.id == variant_id,
                    Variant.stock_on_hand - Variant.stock_reserved >= qty,
                )
                .values(stock_on_hand=Variant.stock_on_hand - qty)
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                await savepoint.rollback()
                return False
    return True


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
                Variant.id == variant_id,
                Variant.stock_on_hand + delta >= Variant.stock_reserved,
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


async def reserved_mismatches(session: AsyncSession) -> list[tuple[uuid.UUID, int, int]]:
    """Variants whose stock_reserved counter differs from the sum of unreleased holds.
    Must always be empty (ADR-003); tests and, later, a health check use it."""
    held = (
        select(Reservation.variant_id, func.sum(Reservation.qty).label("held"))
        .where(Reservation.released_at.is_(None))
        .group_by(Reservation.variant_id)
        .subquery()
    )
    rows = await session.execute(
        select(Variant.id, Variant.stock_reserved, func.coalesce(held.c.held, 0))
        .outerjoin(held, held.c.variant_id == Variant.id)
        .where(Variant.stock_reserved != func.coalesce(held.c.held, 0))
    )
    return [(v, int(r), int(h)) for v, r, h in rows]


__all__ = [
    "OutOfStock",
    "StockBelowReserved",
    "StockLevel",
    "VariantNotFound",
    "adjust_stock",
    "available_qty",
    "convert_holds_to_sale",
    "release_expired_holds",
    "release_order_holds",
    "reserve",
    "reserved_mismatches",
    "sell_without_hold",
]
