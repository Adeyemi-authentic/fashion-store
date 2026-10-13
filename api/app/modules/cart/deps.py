"""Which cart a request is about. Used by the cart and checkout routes."""

from typing import Annotated

from fastapi import Cookie, Depends

from app.modules.accounts.deps import OptionalCustomer
from app.modules.cart.service import CartOwner
from app.shared.cookies import CART_COOKIE


async def cart_owner(
    customer: OptionalCustomer,
    token: Annotated[str | None, Cookie(alias=CART_COOKIE)] = None,
) -> CartOwner:
    # Signed in: always the customer's own cart. A leftover guest cookie is ignored (it was
    # merged at login).
    if customer is not None:
        return CartOwner(customer_id=customer.id)
    return CartOwner(token=token)


Owner = Annotated[CartOwner, Depends(cart_owner)]
