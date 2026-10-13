from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts.deps import OptionalCustomer
from app.modules.cart.deps import Owner
from app.modules.orders.checkout import CheckoutError, start_checkout
from app.modules.payments.paystack import PaymentProvider, get_paystack

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
