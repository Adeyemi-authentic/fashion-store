from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.catalog.schemas import ProductCard
from app.modules.search import service
from app.platform.ratelimit import search_limit

router = APIRouter(tags=["search"])


class AppliedFilters(BaseModel):
    """What the search understood from the query, so the page can show it ("Under ₦50,000,
    for weddings") and the shopper can see why results were narrowed."""

    category: str | None
    min_price_kobo: int | None
    max_price_kobo: int | None
    gender: str | None
    occasion: str | None
    colours: list[str]


class SearchOut(BaseModel):
    query: str
    items: list[ProductCard]
    filters: AppliedFilters
    relaxed: bool  # the query's filters matched nothing, so some were dropped


@router.get("/search", response_model=SearchOut, dependencies=[Depends(search_limit)])
async def search(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=48)] = 24,
) -> SearchOut:
    """Not in the catalog cache: each card shows live stock. The slow parts (the LLM answer
    and the query embedding) have their own caches."""
    result = await service.search(session, q.strip(), limit)
    f = result.filters
    return SearchOut(
        query=q,
        items=result.items,
        filters=AppliedFilters(
            category=f.category,
            min_price_kobo=f.min_price_kobo,
            max_price_kobo=f.max_price_kobo,
            gender=f.gender,
            occasion=f.occasion,
            colours=list(f.colours),
        ),
        relaxed=result.relaxed,
    )
