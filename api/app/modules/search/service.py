"""Hybrid product search: keyword (Postgres full-text) + vector (pgvector), merged with
reciprocal rank fusion. Public interface of the search module (ADR-001).

Why RRF: the two lists score on different scales (ts_rank_cd vs cosine distance), so their
scores can't be added. RRF uses only each product's rank in each list: 1 / (k + rank),
summed. A product near the top of both lists wins; one that only one list found still
counts. k = 60 is the value from the original paper; it stops the very top rank from
dominating.
"""

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ProductCard
from app.modules.catalog.service import SearchFilters, SearchVocabulary
from app.modules.search.understand import understand
from app.platform.cache import TTLCache, catalog_cache
from app.platform.embeddings import get_embedder

Mode = Literal["keyword", "vector", "hybrid"]


@dataclass(frozen=True)
class SearchConfig:
    """The knobs the eval compares. Defaults are what the storefront uses."""

    mode: Mode = "hybrid"
    keyword_mode: str = "and"  # see catalog.retrieval._tsquery
    candidates: int = 50  # taken from each list before fusing
    rrf_k: int = 60
    use_llm: bool = True  # query understanding (filters from the query's words)


DEFAULT = SearchConfig()


# Shoppers repeat queries; an embedding call costs a round trip to Voyage (~300 ms from Lagos).
query_vectors = TTLCache("query_vectors", ttl_seconds=3600, max_entries=2000)


def normalise(q: str) -> str:
    return " ".join(q.lower().split())


async def embed_query(q: str) -> list[float]:
    embedder = get_embedder()

    async def load() -> list[float]:
        return (await embedder.embed([q], "query"))[0]

    return await query_vectors.get_or_load(f"{embedder.model}:{normalise(q)}", load)


def rrf(lists: list[list[uuid.UUID]], k: int) -> list[uuid.UUID]:
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranked in lists:
        for rank, pid in enumerate(ranked, start=1):
            scores[pid] += 1.0 / (k + rank)
    # Ties (same ranks in mirrored lists) keep first-seen order: Python's sort is stable
    # and `scores` remembers insertion order, so the keyword list's order wins.
    return sorted(scores, key=lambda pid: -scores[pid])


async def rank(
    session: AsyncSession,
    q: str,
    filters: SearchFilters | None = None,
    config: SearchConfig = DEFAULT,
    vector: list[float] | None = None,
) -> list[uuid.UUID]:
    """Product ids, best first, before sold-out products are moved down. `q` is the text
    for keyword search; `vector` (the query's embedding) is made from `q` if not given."""
    f = filters or SearchFilters()
    lists: list[list[uuid.UUID]] = []
    if config.mode in ("keyword", "hybrid"):
        lists.append(
            await catalog.keyword_candidates(session, q, f, config.candidates, config.keyword_mode)
        )
    if config.mode in ("vector", "hybrid"):
        vector = vector if vector is not None else await embed_query(q)
        lists.append(await catalog.vector_candidates(session, vector, f, config.candidates))
    return rrf(lists, config.rrf_k)


@dataclass(frozen=True)
class SearchResult:
    items: list[ProductCard]
    filters: SearchFilters  # the filters actually applied (after any relaxing)
    relaxed: bool  # the query's filters matched nothing, so some were dropped


async def vocabulary(session: AsyncSession) -> SearchVocabulary:
    # In the catalog cache, so an admin edit (which clears it) shows up at once.
    return await catalog_cache.get_or_load(
        "search_vocabulary", lambda: catalog.search_vocabulary(session)
    )


def relaxations(f: SearchFilters) -> list[SearchFilters]:
    """What to try, in order, when filters match nothing: keep only the prices (a number
    the shopper typed is the most deliberate part of a query), then nothing."""
    prices_only = SearchFilters(min_price_kobo=f.min_price_kobo, max_price_kobo=f.max_price_kobo)
    return list(dict.fromkeys([f, prices_only, SearchFilters()]))  # drops repeats, keeps order


async def search(
    session: AsyncSession,
    q: str,
    limit: int = 24,
    config: SearchConfig = DEFAULT,
) -> SearchResult:
    """What the storefront shows. The eval calls this too, so it measures the same thing."""
    text, filters, vector = q, SearchFilters(), None
    if config.use_llm:
        vocab = await vocabulary(session)
        needs_vector = config.mode != "keyword"
        # The LLM call and the query embedding are both network round trips: run them
        # together. The embedding uses the raw query, so it doesn't wait for the LLM.
        understood, vector = await asyncio.gather(
            understand(q, vocab), embed_query(q) if needs_vector else _none()
        )
        if understood is not None:
            text, filters = understood.text, understood.filters

    ids: list[uuid.UUID] = []
    tried = filters
    for tried in relaxations(filters):
        ids = await rank(session, text, tried, config, vector)
        if ids:
            break
    # Sold-out products keep their place: they are still the right answer, and the card
    # says "sold out". (Moving them last was tried: with vector search always returning
    # candidates, a sold-out slip dress fell below unrelated in-stock blazers.)
    cards = await catalog.cards_by_id(session, ids[:limit])
    return SearchResult(items=cards, filters=tried, relaxed=tried != filters)


async def _none() -> None:
    return None
