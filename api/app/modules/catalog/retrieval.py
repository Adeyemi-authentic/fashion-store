"""Catalog reads and writes for search and recommendations: embeddings and candidates.

Re-exported by catalog.service, the module's public interface (ADR-001). The search module
does the ranking logic; the SQL that touches catalog tables stays here.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, Text, cast, exists, func, select, update
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product, Variant


def embedding_text(p: Product) -> str:
    """What a product's embedding is made from: the words a shopper might search with.
    Price is left out on purpose; price limits are filters, not similarity."""
    a = p.attributes or {}
    lines = [p.name, f"Category: {p.category}"]
    for key, label in (("type", "Type"), ("gender", "For"), ("fabric", "Fabric"), ("fit", "Fit")):
        if a.get(key):
            lines.append(f"{label}: {a[key]}")
    if a.get("occasion"):
        lines.append("Occasions: " + ", ".join(a["occasion"]))
    if a.get("colours"):
        lines.append("Colours: " + ", ".join(a["colours"]))
    if p.description:
        lines.append(p.description)
    return "\n".join(lines)


def embedding_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()[:32]


@dataclass(frozen=True)
class EmbeddingInput:
    product_id: uuid.UUID
    text: str
    hash: str


async def stale_embeddings(
    session: AsyncSession, model: str, product_ids: list[uuid.UUID] | None = None
) -> list[EmbeddingInput]:
    """Products whose stored embedding is missing or was made from other text or another
    model. Every status, so a draft is ready the moment it's published."""
    stmt = select(Product).order_by(Product.id)
    if product_ids is not None:
        stmt = stmt.where(Product.id.in_(product_ids))
    out = []
    for p in (await session.execute(stmt)).scalars():
        text = embedding_text(p)
        h = embedding_hash(text, model)
        if p.embedding is None or p.embedding_hash != h:
            out.append(EmbeddingInput(p.id, text, h))
    return out


async def save_embedding(
    session: AsyncSession,
    product_id: uuid.UUID,
    vector: list[float],
    expected_hash: str,
    model: str,
) -> bool:
    """Store an embedding only if the product still has the text it was made from. If an
    admin edited the product while the embedding was being made, this one is already stale;
    the edit queued its own refresh, so skipping is correct. Returns whether it was saved."""
    product = await session.get(Product, product_id, with_for_update=True)
    if product is None or embedding_hash(embedding_text(product), model) != expected_hash:
        return False
    await session.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(embedding=vector, embedding_hash=expected_hash)
    )
    return True


# ---------------------------------------------------------------------------------------------
# Search candidates. Each returns active product ids, best first. The search module merges
# the lists; filters are applied here, inside both queries, so neither list wastes slots on
# products the filters would remove later.


@dataclass(frozen=True)
class SearchFilters:
    category: str | None = None
    min_price_kobo: int | None = None
    max_price_kobo: int | None = None
    gender: str | None = None  # "women" or "men"; unisex products match both
    occasion: str | None = None
    colours: tuple[str, ...] = ()  # any of these (variant colours, exact names)

    def is_empty(self) -> bool:
        return self == SearchFilters()


def _filtered(stmt: Select, f: SearchFilters) -> Select:  # type: ignore[type-arg]
    stmt = stmt.where(Product.status == "active")
    if f.category:
        stmt = stmt.where(Product.category == f.category)
    if f.min_price_kobo is not None:
        stmt = stmt.where(Product.base_price_kobo >= f.min_price_kobo)
    if f.max_price_kobo is not None:
        stmt = stmt.where(Product.base_price_kobo <= f.max_price_kobo)
    if f.gender:
        stmt = stmt.where(Product.attributes["gender"].astext.in_([f.gender, "unisex"]))
    if f.occasion:
        stmt = stmt.where(Product.attributes["occasion"].contains([f.occasion]))
    if f.colours:
        stmt = stmt.where(
            exists().where(Variant.product_id == Product.id, Variant.colour.in_(f.colours))
        )
    return stmt


def _tsquery(text: str, mode: str):  # type: ignore[no-untyped-def]
    """`and`: every word must match (websearch syntax, like most shop search boxes).
    `or`: any word may match, and ranking rewards matching more of them."""
    if mode == "and":
        return func.websearch_to_tsquery("english", text)
    return cast(func.replace(cast(func.plainto_tsquery("english", text), Text), "&", "|"), TSQUERY)


async def keyword_candidates(
    session: AsyncSession, text: str, f: SearchFilters, limit: int, mode: str = "and"
) -> list[uuid.UUID]:
    query = _tsquery(text, mode)
    stmt = (
        select(Product.id)
        .where(Product.search_tsv.op("@@")(query))
        # Ties break by slug, not id: ids are random, slugs are the same in every database,
        # so the eval gives the same answer on every run.
        .order_by(func.ts_rank_cd(Product.search_tsv, query).desc(), Product.slug)
        .limit(limit)
    )
    return list((await session.execute(_filtered(stmt, f))).scalars())


async def vector_candidates(
    session: AsyncSession, vector: list[float], f: SearchFilters, limit: int
) -> list[uuid.UUID]:
    """Exact nearest neighbours by cosine distance. No ANN index: at ~150 products a full
    scan takes about a millisecond and is exact (an HNSW index pays off in the tens of
    thousands, and filtering after an approximate scan can return too few rows)."""
    stmt = (
        select(Product.id)
        .where(Product.embedding.is_not(None))
        .order_by(Product.embedding.cosine_distance(vector), Product.slug)
        .limit(limit)
    )
    return list((await session.execute(_filtered(stmt, f))).scalars())


@dataclass(frozen=True)
class SearchVocabulary:
    """The values a search filter can take: only ones some visible product has."""

    categories: tuple[str, ...]
    occasions: tuple[str, ...]
    colours: tuple[str, ...]
    # (category, its product types): lets the LLM tell that a scarf is in adire, not
    # accessories. Product types differ across categories in ways no name reveals.
    types: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def fingerprint(self) -> str:
        """Stable across processes (Python's hash() of strings is not)."""
        return hashlib.sha256(repr(self).encode()).hexdigest()[:12]


async def search_vocabulary(session: AsyncSession) -> SearchVocabulary:
    active = Product.status == "active"
    categories = (
        await session.execute(select(Product.category).where(active).distinct())
    ).scalars()
    occasions = (
        await session.execute(
            select(func.jsonb_array_elements_text(Product.attributes["occasion"]))
            .where(active)
            .distinct()
        )
    ).scalars()
    colours = (
        await session.execute(
            select(Variant.colour)
            .join(Product, Product.id == Variant.product_id)
            .where(active)
            .distinct()
        )
    ).scalars()
    type_rows = (
        await session.execute(
            select(Product.category, Product.attributes["type"].astext).where(active).distinct()
        )
    ).all()
    by_category: dict[str, set[str]] = {}
    for category, product_type in type_rows:
        if product_type:
            by_category.setdefault(category, set()).add(product_type)
    return SearchVocabulary(
        tuple(sorted(categories)),
        tuple(sorted(occasions)),
        tuple(sorted(colours)),
        tuple((c, tuple(sorted(t))) for c, t in sorted(by_category.items())),
    )
