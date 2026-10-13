from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts.deps import OptionalCustomer
from app.modules.cart import service
from app.modules.cart.schemas import AddItemIn, CartOut, SetQtyIn
from app.shared.cookies import CART_COOKIE, set_cookie

router = APIRouter(prefix="/cart", tags=["cart"])

Session = Annotated[AsyncSession, Depends(get_session)]


async def cart_owner(
    customer: OptionalCustomer,
    token: Annotated[str | None, Cookie(alias=CART_COOKIE)] = None,
) -> service.CartOwner:
    # Signed in: always the customer's own cart. A leftover guest cookie is ignored (it was
    # merged at login).
    if customer is not None:
        return service.CartOwner(customer_id=customer.id)
    return service.CartOwner(token=token)


Owner = Annotated[service.CartOwner, Depends(cart_owner)]


def _stock_error(exc: service.NotEnoughStock) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "not_enough_stock",
            "available": exc.available,
            "in_cart": exc.in_cart,
            "message": "Sold out" if exc.available == 0 else f"Only {exc.available} left",
        },
    )


@router.get("", response_model=CartOut)
async def get_cart(session: Session, owner: Owner) -> CartOut:
    return await service.view(session, owner)


@router.post("/items", response_model=CartOut)
async def add_item(body: AddItemIn, response: Response, session: Session, owner: Owner) -> CartOut:
    try:
        cart, token = await service.add_item(session, owner, body.variant_id, body.qty)
    except service.VariantNotFound as exc:
        raise HTTPException(status_code=404, detail="variant not found") from exc
    except service.NotEnoughStock as exc:
        raise _stock_error(exc) from exc
    if token:
        set_cookie(response, CART_COOKIE, token)
    return cart


@router.patch("/items/{variant_id}", response_model=CartOut)
async def set_qty(
    variant_id: UUID, body: SetQtyIn, response: Response, session: Session, owner: Owner
) -> CartOut:
    try:
        cart = await service.set_qty(session, owner, variant_id, body.qty)
    except service.VariantNotFound as exc:
        raise HTTPException(status_code=404, detail="item not in cart") from exc
    except service.NotEnoughStock as exc:
        raise _stock_error(exc) from exc
    if owner.customer_id is None and owner.token:
        set_cookie(response, CART_COOKIE, owner.token)  # renew the 30 days
    return cart


@router.delete("/items/{variant_id}", response_model=CartOut)
async def remove_item(
    variant_id: UUID, response: Response, session: Session, owner: Owner
) -> CartOut:
    return await set_qty(variant_id, SetQtyIn(qty=0), response, session, owner)
