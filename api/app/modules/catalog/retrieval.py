"""Catalog reads and writes for search and recommendations: product embeddings.

Re-exported by catalog.service, the module's public interface (ADR-001). The search module
does the ranking logic; the SQL that touches catalog tables stays here.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product


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
