from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.catalog import service
from app.modules.catalog.schemas import CategoryOut, ProductDetail, ProductPage, ProductSort
from app.shared.pagination import InvalidCursor

router = APIRouter(tags=["catalog"])

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/products", response_model=ProductPage)
async def list_products(
    session: Session,
    category: str | None = None,
    min_price_kobo: Annotated[int | None, Query(ge=0)] = None,
    max_price_kobo: Annotated[int | None, Query(ge=0)] = None,
    size: str | None = None,
    colour: str | None = None,
    in_stock: bool | None = None,
    sort: ProductSort = ProductSort.newest,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE_SIZE)] = 24,
    cursor: str | None = None,
) -> ProductPage:
    filters = service.ProductFilters(
        category=category,
        min_price_kobo=min_price_kobo,
        max_price_kobo=max_price_kobo,
        size=size,
        colour=colour,
        in_stock=in_stock,
    )
    try:
        return await service.list_products(session, filters, sort, limit, cursor)
    except InvalidCursor as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/products/{slug}", response_model=ProductDetail)
async def get_product(slug: str, session: Session) -> ProductDetail:
    product = await service.get_product(session, slug)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return product


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: Session) -> list[CategoryOut]:
    return await service.list_categories(session)
