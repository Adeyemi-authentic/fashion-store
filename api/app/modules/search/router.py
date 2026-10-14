from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modules.catalog.schemas import ProductCard
from app.modules.search import service
from app.modules.search.recommendations import recommend
from app.platform.cache import catalog_cache
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


class RecommendationsOut(BaseModel):
    similar: list[ProductCard]
    bought_together: list[ProductCard]


@router.get("/products/{slug}/recommendations", response_model=RecommendationsOut)
async def recommendations(
    slug: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> RecommendationsOut:
    """Cached for up to a minute like the rest of the catalog: an item that sells out may
    stay in "similar" for that long, and its own page then shows it as sold out."""
    recs = await catalog_cache.get_or_load(f"recs:{slug}", lambda: recommend(session, slug))
    if recs is None:
        raise HTTPException(status_code=404, detail="product not found")
    return RecommendationsOut(similar=recs.similar, bought_together=recs.bought_together)
