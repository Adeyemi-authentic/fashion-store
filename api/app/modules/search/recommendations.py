"""Recommendations on the product page.

- **Similar items:** nearest neighbours of the product's embedding, visible and in stock.
- **Frequently bought together:** products sharing paid orders with this one, at least
  twice. Empty until there are enough orders; the page then hides the row rather than
  filling it with guesses (the cold-start problem, stated plainly).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ProductCard
from app.modules.orders import service as orders


@dataclass(frozen=True)
class Recommendations:
    similar: list[ProductCard]
    bought_together: list[ProductCard]


async def recommend(session: AsyncSession, slug: str, limit: int = 4) -> Recommendations | None:
    product_id = await catalog.product_id_for_slug(session, slug)
    if product_id is None:
        return None
    similar = await catalog.cards_by_id(
        session, await catalog.similar_products(session, product_id, limit)
    )
    pairs = await orders.bought_together(session, product_id, limit=limit * 2)
    # Out of stock or hidden products are dropped here; cards_by_id keeps only visible ones.
    together = [
        c for c in await catalog.cards_by_id(session, [pid for pid, _ in pairs]) if c.in_stock
    ]
    return Recommendations(similar=similar, bought_together=together[:limit])
