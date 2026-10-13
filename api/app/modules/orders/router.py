import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts.deps import Customer, OptionalCustomer
from app.modules.cart.deps import Owner
from app.modules.orders import receipts
from app.modules.orders.checkout import CheckoutError, start_checkout
from app.modules.orders.payment import apply_successful_charge
from app.modules.payments.paystack import PaymentProvider, PaystackError, get_paystack

log = logging.getLogger(__name__)

router = APIRouter(tags=["orders"])

Session = Annotated[AsyncSession, Depends(get_session)]
Provider = Annotated[PaymentProvider, Depends(get_paystack)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{8,100}$"),
]


class CheckoutIn(BaseModel):
    email: EmailStr | None = None  # required for guests; signed-in customers use their account's


class CheckoutOut(BaseModel):
    order_id: str
    reference: str
    authorization_url: str
    total_kobo: int
    expires_at: str


@router.post("/checkout", response_model=CheckoutOut, status_code=201)
async def checkout(
    body: CheckoutIn,
    session: Session,
    provider: Provider,
    owner: Owner,
    customer: OptionalCustomer,
    idempotency_key: IdempotencyKeyHeader,
) -> CheckoutOut | JSONResponse:
    email = customer.email if customer else body.email
    if not email:
        raise HTTPException(status_code=422, detail="Enter an email so we can send your receipt")
    try:
        result = await start_checkout(
            session,
            provider,
            owner=owner,
            customer_id=customer.id if customer else None,
            email=email,
            idempotency_key=idempotency_key,
            body=body.model_dump(),
        )
    except CheckoutError as exc:
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )
    return CheckoutOut(**result)


class OrderLineOut(BaseModel):
    name: str
    sku: str
    qty: int
    unit_price_kobo: int


class OrderOut(BaseModel):
    reference: str
    status: str
    total_kobo: int
    created_at: datetime
    items: list[OrderLineOut]


UNSETTLED = {"pending_payment", "expired", "cancelled"}


@router.get("/orders/by-reference/{reference}", response_model=OrderOut)
async def order_by_reference(reference: str, session: Session, provider: Provider) -> OrderOut:
    """For the page Paystack sends the customer back to. The redirect itself proves nothing
    (anyone can type ?reference=...), so if the order isn't settled yet we ask Paystack
    directly and apply the answer with the same function the webhook uses. The reference is
    an unguessable token, like a receipt link; no email or address is returned."""
    order = await receipts.find(session, reference)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status in UNSETTLED:
        await session.rollback()  # hold no transaction open across the network call
        try:
            charge = await provider.verify(reference)
        except PaystackError as exc:
            log.warning("verify %s failed: %s", reference, exc)
            charge = None
        if charge is not None and charge.status == "success":
            await apply_successful_charge(session, charge)
            await session.commit()
        order = await receipts.find(session, reference)
        assert order is not None
    return OrderOut.model_validate(order, from_attributes=True)


@router.get("/orders/mine", response_model=list[OrderOut])
async def my_orders(session: Session, customer: Customer) -> list[OrderOut]:
    orders = await receipts.for_customer(session, customer.id)
    return [OrderOut.model_validate(o, from_attributes=True) for o in orders]
