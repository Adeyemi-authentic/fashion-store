import uuid
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts import service
from app.modules.accounts.deps import Customer
from app.modules.accounts.schemas import LoginIn, MeOut, SignupIn
from app.modules.cart.service import merge_guest_cart
from app.platform.ratelimit import login_limit
from app.shared.cookies import CART_COOKIE, SESSION_COOKIE, clear_cookie, set_cookie

router = APIRouter(prefix="/auth", tags=["accounts"])

Session = Annotated[AsyncSession, Depends(get_session)]
SessionToken = Annotated[str | None, Cookie(alias=SESSION_COOKIE)]
CartToken = Annotated[str | None, Cookie(alias=CART_COOKIE)]


def _me(c: object) -> MeOut:
    return MeOut.model_validate(c, from_attributes=True)


async def _log_in(
    session: Session,
    response: Response,
    customer_id: uuid.UUID,
    old_token: str | None,
    cart_token: str | None,
) -> None:
    """One transaction: drop any old session, start a new one, merge the guest cart."""
    # A fresh token on every login (never reuse one that existed before), so a token planted
    # in someone's browser before they log in is useless afterwards (session fixation).
    await service.end_session(session, old_token)
    token = await service.start_session(session, customer_id)
    await merge_guest_cart(session, cart_token, customer_id)
    await session.commit()
    set_cookie(response, SESSION_COOKIE, token, max_age=service.SESSION_DAYS * 24 * 3600)
    clear_cookie(response, CART_COOKIE)  # the guest cart now belongs to the account


@router.post("/signup", response_model=MeOut, status_code=201, dependencies=[Depends(login_limit)])
async def signup(
    body: SignupIn,
    response: Response,
    session: Session,
    old_token: SessionToken = None,
    cart_token: CartToken = None,
) -> MeOut:
    try:
        customer = await service.create_customer(session, body.email, body.password)
    except service.EmailTaken as exc:
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        ) from exc
    me = _me(customer)
    await _log_in(session, response, customer.id, old_token, cart_token)
    return me


@router.post("/login", response_model=MeOut, dependencies=[Depends(login_limit)])
async def login(
    body: LoginIn,
    response: Response,
    session: Session,
    old_token: SessionToken = None,
    cart_token: CartToken = None,
) -> MeOut:
    customer = await service.authenticate(session, body.email, body.password)
    if customer is None:
        # Same message for "no such email" and "wrong password".
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    me = _me(customer)
    await _log_in(session, response, customer.id, old_token, cart_token)
    return me


@router.post("/logout", status_code=204)
async def logout(response: Response, session: Session, token: SessionToken = None) -> None:
    await service.end_session(session, token)
    await session.commit()
    clear_cookie(response, SESSION_COOKIE)


@router.get("/me", response_model=MeOut)
async def me(customer: Customer) -> MeOut:
    return _me(customer)
