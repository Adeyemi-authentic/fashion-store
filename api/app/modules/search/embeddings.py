"""Keeping product embeddings in step with product text."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import service as catalog
from app.platform.embeddings import get_embedder


async def refresh_embeddings(
    session: AsyncSession, product_ids: list[uuid.UUID] | None = None
) -> int:
    """Embed every stale product (or just these). Products already up to date cost no API
    call, so running this twice is cheap. Flushes, doesn't commit. Returns how many saved."""
    embedder = get_embedder()
    stale = await catalog.stale_embeddings(session, embedder.model, product_ids)
    if not stale:
        return 0
    vectors = await embedder.embed([s.text for s in stale], "document")
    saved = 0
    for item, vector in zip(stale, vectors, strict=True):
        saved += await catalog.save_embedding(
            session, item.product_id, vector, item.hash, embedder.model
        )
    await session.flush()
    return saved
