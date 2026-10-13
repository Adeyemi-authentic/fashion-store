from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.modules.cart import service
from app.modules.cart.schemas import AddItemIn, CartOut, SetQtyIn

router = APIRouter(prefix="/cart", tags=["cart"])

COOKIE_NAME = "cart_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days, renewed on every change

Session = Annotated[AsyncSession, Depends(get_session)]
Token = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


def _set_cookie(response: Response, token: str) -> None:
    # First-party because the browser only sees the storefront origin (ADR-005).
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        path="/",
    )


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
async def get_cart(session: Session, token: Token = None) -> CartOut:
    return await service.view(session, token)


@router.post("/items", response_model=CartOut)
async def add_item(
    body: AddItemIn, response: Response, session: Session, token: Token = None
) -> CartOut:
    try:
        cart, token = await service.add_item(session, token, body.variant_id, body.qty)
    except service.VariantNotFound as exc:
        raise HTTPException(status_code=404, detail="variant not found") from exc
    except service.NotEnoughStock as exc:
        raise _stock_error(exc) from exc
    _set_cookie(response, token)
    return cart


@router.patch("/items/{variant_id}", response_model=CartOut)
async def set_qty(
    variant_id: UUID, body: SetQtyIn, response: Response, session: Session, token: Token = None
) -> CartOut:
    try:
        cart = await service.set_qty(session, token, variant_id, body.qty)
    except service.VariantNotFound as exc:
        raise HTTPException(status_code=404, detail="item not in cart") from exc
    except service.NotEnoughStock as exc:
        raise _stock_error(exc) from exc
    assert token is not None  # set_qty raises VariantNotFound without a cart
    _set_cookie(response, token)
    return cart


@router.delete("/items/{variant_id}", response_model=CartOut)
async def remove_item(
    variant_id: UUID, response: Response, session: Session, token: Token = None
) -> CartOut:
    return await set_qty(variant_id, SetQtyIn(qty=0), response, session, token)
