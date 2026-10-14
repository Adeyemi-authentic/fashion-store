"""Server-side carts for guests (cookie token) and signed-in customers.

Public interface of the cart module (ADR-001). Product facts come from catalog.service.
A signed-in customer always uses their own cart; a guest uses the cart behind `cart_token`.
At login the guest cart is merged into the customer's cart (`merge_guest_cart`).
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cart.models import Cart, CartItem
from app.modules.cart.schemas import MAX_LINE_QTY, CartLineOut, CartOut, LineProblem
from app.modules.catalog.service import VariantSummary, get_variant_summaries


class VariantNotFound(LookupError):
    pass


@dataclass(frozen=True)
class CartOwner:
    """Whose cart a request is about: a signed-in customer, else the guest cookie token."""

    customer_id: uuid.UUID | None = None
    token: str | None = None

    @property
    def ref(self) -> str | None:
        """A stable, non-secret name for this owner (scopes idempotency keys per buyer)."""
        if self.customer_id is not None:
            return f"c:{self.customer_id}"
        if self.token:
            return f"g:{_hash(self.token)[:32]}"
        return None


@dataclass
class NotEnoughStock(Exception):
    available: int
    in_cart: int


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _hash(token: str) -> str:
    # Only a hash is stored, so a leaked database (or log of it) can't be replayed as cookies.
    return hashlib.sha256(token.encode()).hexdigest()


async def _find_cart(session: AsyncSession, owner: CartOwner, lock: bool) -> Cart | None:
    if owner.customer_id is not None:
        stmt = select(Cart).where(Cart.customer_id == owner.customer_id)
    elif owner.token:
        stmt = select(Cart).where(Cart.session_token == _hash(owner.token))
    else:
        return None
    if lock:
        # One cart's changes run one at a time. Without this, two quick "add" clicks can both
        # read qty=1 and both write qty=2, or both pass the stock check for the last unit.
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def _customer_cart(session: AsyncSession, customer_id: uuid.UUID) -> Cart:
    """The customer's cart, created if missing, locked. ON CONFLICT makes two first requests
    at once both end up with the same single cart instead of one failing on the UNIQUE."""
    await session.execute(
        insert(Cart)
        .values(customer_id=customer_id)
        .on_conflict_do_nothing(index_elements=[Cart.customer_id])
    )
    cart = await _find_cart(session, CartOwner(customer_id=customer_id), lock=True)
    assert cart is not None
    return cart


async def _get_or_create_cart(session: AsyncSession, owner: CartOwner) -> tuple[Cart, str | None]:
    """Returns the cart and, for guests, the token the cookie should hold."""
    if owner.customer_id is not None:
        return await _customer_cart(session, owner.customer_id), None
    cart = await _find_cart(session, owner, lock=True)
    if cart is not None:
        return cart, owner.token
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
    session: AsyncSession, owner: CartOwner, variant_id: uuid.UUID, qty: int
) -> tuple[CartOut, str | None]:
    """Add qty of a variant. Returns the cart and, for guests, the cookie token (new if a cart
    was just created)."""
    summary = await _summary(session, variant_id)
    cart, token = await _get_or_create_cart(session, owner)
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
    return await view(session, owner if token is None else CartOwner(token=token)), token


async def set_qty(
    session: AsyncSession, owner: CartOwner, variant_id: uuid.UUID, qty: int
) -> CartOut:
    cart = await _find_cart(session, owner, lock=True)
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
    return await view(session, owner)


async def view(session: AsyncSession, owner: CartOwner) -> CartOut:
    cart = await _find_cart(session, owner, lock=False)
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


async def merge_guest_cart(
    session: AsyncSession, guest_token: str | None, customer_id: uuid.UUID
) -> None:
    """Move a guest's cart into the customer's cart at login. Runs inside the login
    transaction and doesn't commit, so login and merge succeed or fail together.

    Same variant in both: quantities add up, capped at MAX_LINE_QTY. Stock isn't checked
    here; lines over stock show up flagged in the cart, like any other stock change.
    """
    if not guest_token:
        return
    # Lock order: customer cart first, then guest cart. Every merge takes them in this order,
    # so two logins at once can't each hold one lock and wait for the other.
    target = await _customer_cart(session, customer_id)
    guest = await _find_cart(session, CartOwner(token=guest_token), lock=True)
    if guest is None or guest.id == target.id:
        return
    guest_lines = select(literal(target.id), CartItem.variant_id, CartItem.qty).where(
        CartItem.cart_id == guest.id
    )
    stmt = insert(CartItem).from_select(["cart_id", "variant_id", "qty"], guest_lines)
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_cart_items_cart_variant",
            set_={"qty": func.least(CartItem.qty + stmt.excluded.qty, MAX_LINE_QTY)},
        )
    )
    await session.execute(delete(Cart).where(Cart.id == guest.id))  # its items cascade
    await _touch(session, target)


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


@dataclass(frozen=True)
class CheckoutLine:
    variant_id: uuid.UUID
    qty: int
    unit_price_kobo: int
    name: str
    sku: str
    problem: LineProblem | None


async def checkout_lines(
    session: AsyncSession, owner: CartOwner
) -> tuple[uuid.UUID | None, list[CheckoutLine]]:
    """The cart's id and its lines with live prices, for checkout to copy into an order."""
    cart = await _find_cart(session, owner, lock=False)
    if cart is None:
        return None, []
    view_ = await view(session, owner)
    return cart.id, [
        CheckoutLine(
            variant_id=line.variant_id,
            qty=line.qty,
            unit_price_kobo=line.unit_price_kobo,
            name=f"{line.product_name} ({line.colour}, {line.size})",
            sku=line.sku,
            problem=line.problem,
        )
        for line in view_.items
    ]


async def remove_purchased(
    session: AsyncSession, cart_id: uuid.UUID, items: list[tuple[uuid.UUID, int]]
) -> None:
    """After payment: take what was bought out of the cart. A line the customer has raised
    since checkout keeps the extra units. Doesn't commit."""
    for variant_id, qty in items:
        line = and_(CartItem.cart_id == cart_id, CartItem.variant_id == variant_id)
        # Delete fully-bought lines first: lowering one to 0 would break CHECK (qty > 0).
        await session.execute(delete(CartItem).where(line, CartItem.qty <= qty))
        await session.execute(update(CartItem).where(line).values(qty=CartItem.qty - qty))


async def delete_idle_guest_carts(session: AsyncSession, idle_for: timedelta) -> int:
    """Guest carts nobody has touched for a while. Account carts are kept. Doesn't commit."""
    result = await session.execute(
        delete(Cart).where(Cart.customer_id.is_(None), Cart.updated_at < func.now() - idle_for)
    )
    return result.rowcount  # type: ignore[attr-defined,no-any-return]
