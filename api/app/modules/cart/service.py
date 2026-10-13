"""Server-side carts for guests (cookie token) and, from Phase 3, signed-in customers.

Public interface of the cart module (ADR-001). Product facts come from catalog.service.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cart.models import Cart, CartItem
from app.modules.cart.schemas import MAX_LINE_QTY, CartLineOut, CartOut, LineProblem
from app.modules.catalog.service import VariantSummary, get_variant_summaries


class VariantNotFound(LookupError):
    pass


@dataclass
class NotEnoughStock(Exception):
    available: int
    in_cart: int


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _hash(token: str) -> str:
    # Only a hash is stored, so a leaked database (or log of it) can't be replayed as cookies.
    return hashlib.sha256(token.encode()).hexdigest()


async def _find_cart(session: AsyncSession, token: str | None, lock: bool) -> Cart | None:
    if not token:
        return None
    stmt = select(Cart).where(Cart.session_token == _hash(token))
    if lock:
        # One cart's changes run one at a time. Without this, two quick "add" clicks can both
        # read qty=1 and both write qty=2, or both pass the stock check for the last unit.
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def _get_or_create_cart(session: AsyncSession, token: str | None) -> tuple[Cart, str]:
    cart = await _find_cart(session, token, lock=True)
    if cart is not None and token is not None:
        return cart, token
    token = new_token()
    cart = Cart(session_token=_hash(token))
    session.add(cart)
    await session.flush()
    return cart, token


async def _current_qty(session: AsyncSession, cart_id: uuid.UUID, variant_id: uuid.UUID) -> int:
    qty = await session.scalar(
        select(CartItem.qty).where(CartItem.cart_id == cart_id, CartItem.variant_id == variant_id)
    )
    return qty or 0


async def _summary(session: AsyncSession, variant_id: uuid.UUID) -> VariantSummary:
    summary = (await get_variant_summaries(session, [variant_id])).get(variant_id)
    if summary is None or not summary.purchasable:
        raise VariantNotFound(variant_id)
    return summary


async def _touch(session: AsyncSession, cart: Cart) -> None:
    await session.execute(update(Cart).where(Cart.id == cart.id).values(updated_at=func.now()))


async def add_item(
    session: AsyncSession, token: str | None, variant_id: uuid.UUID, qty: int
) -> tuple[CartOut, str]:
    """Add qty of a variant. Returns the cart and the token (new if a cart was created)."""
    summary = await _summary(session, variant_id)
    cart, token = await _get_or_create_cart(session, token)
    current = await _current_qty(session, cart.id, variant_id)
    new_qty = min(current + qty, MAX_LINE_QTY)
    if new_qty > summary.available:
        raise NotEnoughStock(available=summary.available, in_cart=current)
    await session.execute(
        insert(CartItem)
        .values(cart_id=cart.id, variant_id=variant_id, qty=new_qty)
        .on_conflict_do_update(constraint="uq_cart_items_cart_variant", set_={"qty": new_qty})
    )
    await _touch(session, cart)
    await session.commit()
    return await view(session, token), token


async def set_qty(
    session: AsyncSession, token: str | None, variant_id: uuid.UUID, qty: int
) -> CartOut:
    cart = await _find_cart(session, token, lock=True)
    if cart is None:
        raise VariantNotFound(variant_id)
    current = await _current_qty(session, cart.id, variant_id)
    if current == 0:
        raise VariantNotFound(variant_id)
    if qty == 0:
        await session.execute(
            delete(CartItem).where(CartItem.cart_id == cart.id, CartItem.variant_id == variant_id)
        )
    else:
        # Lowering a quantity is always allowed, even if stock has since dropped below it,
        # so a customer can fix a "not enough stock" line one step at a time.
        if qty > current:
            summary = await _summary(session, variant_id)
            if qty > summary.available:
                raise NotEnoughStock(available=summary.available, in_cart=current)
        await session.execute(
            update(CartItem)
            .where(CartItem.cart_id == cart.id, CartItem.variant_id == variant_id)
            .values(qty=qty)
        )
    await _touch(session, cart)
    await session.commit()
    return await view(session, token)


async def view(session: AsyncSession, token: str | None) -> CartOut:
    cart = await _find_cart(session, token, lock=False)
    if cart is None:
        return CartOut(items=[], item_count=0, subtotal_kobo=0, can_checkout=False)
    rows = (
        await session.execute(
            select(CartItem.variant_id, CartItem.qty)
            .where(CartItem.cart_id == cart.id)
            .order_by(CartItem.created_at, CartItem.id)
        )
    ).all()
    summaries = await get_variant_summaries(session, [vid for vid, _ in rows])
    lines = [_line(summaries[vid], qty) for vid, qty in rows if vid in summaries]
    return CartOut(
        items=lines,
        item_count=sum(line.qty for line in lines),
        subtotal_kobo=sum(line.line_total_kobo for line in lines),
        can_checkout=bool(lines) and all(line.problem is None for line in lines),
    )


def _line(s: VariantSummary, qty: int) -> CartLineOut:
    problem = None
    if not s.purchasable:
        problem = LineProblem.unavailable
    elif s.available == 0:
        problem = LineProblem.sold_out
    elif qty > s.available:
        problem = LineProblem.not_enough_stock
    return CartLineOut(
        variant_id=s.variant_id,
        sku=s.sku,
        product_slug=s.product_slug,
        product_name=s.product_name,
        size=s.size,
        colour=s.colour,
        image=s.image,
        unit_price_kobo=s.price_kobo,  # live price; the order snapshots it at checkout
        qty=qty,
        line_total_kobo=s.price_kobo * qty,
        available=s.available,
        problem=problem,
    )
