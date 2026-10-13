import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.accounts import service as accounts
from app.modules.accounts.deps import Admin, require_admin
from app.modules.admin.schemas import CreatedOut, OrderOut, OrderPage, StockAdjustIn, StockOut
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import (
    AdminProductOut,
    AdminProductPage,
    ProductCreateIn,
    ProductUpdateIn,
    VariantIn,
    VariantPriceIn,
)
from app.modules.orders import service as orders
from app.shared.pagination import InvalidCursor

log = logging.getLogger(__name__)

# The admin check sits on the router, so every route added here is protected, including
# ones written later by someone who forgets to add it. A test walks all /admin routes to
# prove a customer gets 403 and a visitor 401 on each.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

Session = Annotated[AsyncSession, Depends(get_session)]


def _bad_cursor(exc: InvalidCursor) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/products", response_model=AdminProductPage)
async def list_products(
    session: Session,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["draft", "active", "archived"] | None = None,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> AdminProductPage:
    try:
        return await catalog.admin_list_products(session, q, status, category, limit, cursor)
    except InvalidCursor as exc:
        raise _bad_cursor(exc) from exc


@router.get("/products/{product_id}", response_model=AdminProductOut)
async def get_product(product_id: UUID, session: Session) -> AdminProductOut:
    product = await catalog.admin_get_product(session, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return product


@router.post("/products", response_model=CreatedOut, status_code=201)
async def create_product(body: ProductCreateIn, session: Session, admin: Admin) -> CreatedOut:
    try:
        product_id, stock = await catalog.create_product(session, body)
    except (catalog.CannotPublish, catalog.DuplicateVariant) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Starting stock goes through orders.inventory, in the same transaction as the product.
    for variant_id, qty in stock:
        if qty:
            await orders.adjust_stock(session, variant_id, qty)
    await session.commit()
    product = await catalog.admin_get_product(session, product_id)
    assert product is not None
    log.info("admin %s created product %s", admin.email, product.slug)
    return CreatedOut(id=product_id, slug=product.slug)


@router.patch("/products/{product_id}", response_model=AdminProductOut)
async def update_product(
    product_id: UUID, body: ProductUpdateIn, session: Session, admin: Admin
) -> AdminProductOut:
    try:
        slug = await catalog.update_product(session, product_id, body)
    except catalog.ProductNotFound as exc:
        raise HTTPException(status_code=404, detail="product not found") from exc
    except catalog.CannotPublish as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    log.info("admin %s updated product %s", admin.email, slug)
    return await get_product(product_id, session)


@router.delete("/products/{product_id}", response_model=AdminProductOut)
async def archive_product(product_id: UUID, session: Session, admin: Admin) -> AdminProductOut:
    """Soft delete: past orders point at this product's variants, so the rows stay."""
    return await update_product(product_id, ProductUpdateIn(status="archived"), session, admin)


@router.post("/products/{product_id}/variants", response_model=AdminProductOut, status_code=201)
async def add_variant(
    product_id: UUID, body: VariantIn, session: Session, admin: Admin
) -> AdminProductOut:
    try:
        variant_id, slug = await catalog.add_variant(session, product_id, body)
    except catalog.ProductNotFound as exc:
        raise HTTPException(status_code=404, detail="product not found") from exc
    except catalog.DuplicateVariant as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.stock_on_hand:
        await orders.adjust_stock(session, variant_id, body.stock_on_hand)
    await session.commit()
    log.info("admin %s added variant to %s", admin.email, slug)
    return await get_product(product_id, session)


@router.patch("/variants/{variant_id}", response_model=AdminProductOut)
async def set_price(
    variant_id: UUID, body: VariantPriceIn, session: Session, admin: Admin
) -> AdminProductOut:
    try:
        slug = await catalog.set_variant_price(session, variant_id, body.price_kobo)
    except catalog.ProductNotFound as exc:
        raise HTTPException(status_code=404, detail="variant not found") from exc
    await session.commit()
    log.info("admin %s set price of %s on %s", admin.email, variant_id, slug)
    product_id = await catalog.product_id_for_variant(session, variant_id)
    return await get_product(product_id, session)


@router.post("/variants/{variant_id}/stock", response_model=StockOut)
async def adjust_stock(
    variant_id: UUID, body: StockAdjustIn, session: Session, admin: Admin
) -> StockOut:
    try:
        level = await orders.adjust_stock(session, variant_id, body.delta)
    except orders.VariantNotFound as exc:
        raise HTTPException(status_code=404, detail="variant not found") from exc
    except orders.StockBelowReserved as exc:
        if exc.reserved:
            message = (
                f"Can't go below {exc.reserved}: that many are held for unpaid orders "
                f"(on hand now: {exc.on_hand})"
            )
        else:
            message = f"Can't remove more than the {exc.on_hand} on hand"
        raise HTTPException(status_code=409, detail=message) from exc
    await session.commit()
    log.info(
        "admin %s stock %+d on %s (%s) -> %d",
        admin.email,
        body.delta,
        variant_id,
        body.reason,
        level.on_hand,
    )
    return StockOut(
        variant_id=variant_id,
        stock_on_hand=level.on_hand,
        stock_reserved=level.reserved,
        available=max(0, level.on_hand - level.reserved),
    )


@router.get("/orders", response_model=OrderPage)
async def list_orders(
    session: Session,
    status: Annotated[
        str | None, Query(pattern="^(" + "|".join(orders.ORDER_STATUSES) + ")$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> OrderPage:
    try:
        rows, next_cursor = await orders.list_orders(session, status, limit, cursor)
    except InvalidCursor as exc:
        raise _bad_cursor(exc) from exc
    emails = await accounts.emails_for(session, [r.customer_id for r in rows if r.customer_id])
    return OrderPage(
        items=[
            OrderOut(
                id=r.id,
                status=r.status,
                total_kobo=r.total_kobo,
                currency=r.currency,
                email=emails.get(r.customer_id) if r.customer_id else r.guest_email,
                is_guest=r.customer_id is None,
                item_count=r.item_count,
                needs_review=r.needs_review,
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_cursor=next_cursor,
    )
