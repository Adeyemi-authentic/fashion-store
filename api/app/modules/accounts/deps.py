"""FastAPI dependencies other modules use to find out who is calling."""

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts.service import CurrentCustomer, current_customer
from app.shared.cookies import SESSION_COOKIE


async def optional_customer(
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> CurrentCustomer | None:
    return await current_customer(session, token)


async def require_customer(
    customer: Annotated[CurrentCustomer | None, Depends(optional_customer)],
) -> CurrentCustomer:
    if customer is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return customer


async def require_admin(
    customer: Annotated[CurrentCustomer, Depends(require_customer)],
) -> CurrentCustomer:
    # Enforced here on the API. The storefront also hides admin links from customers, but
    # that is only cosmetic: anyone can call the API directly.
    if not customer.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return customer


OptionalCustomer = Annotated[CurrentCustomer | None, Depends(optional_customer)]
Customer = Annotated[CurrentCustomer, Depends(require_customer)]
Admin = Annotated[CurrentCustomer, Depends(require_admin)]
