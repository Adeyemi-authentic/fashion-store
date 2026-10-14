"""Embed every product whose embedding is missing or stale. Safe to run any number of times.

    uv run python -m scripts.embed_catalog

Run it after seeding, or after switching EMBEDDING_MODEL. Admin edits don't need it: each
one queues its own `embed_product` job.
"""

import asyncio

from app.db import dispose_engine, get_sessionmaker
from app.modules.search.embeddings import refresh_embeddings
from app.platform.embeddings import get_embedder


async def main() -> None:
    async with get_sessionmaker()() as session:
        saved = await refresh_embeddings(session)
        await session.commit()
    await dispose_engine()
    print(f"embedded {saved} products with {get_embedder().model}")


if __name__ == "__main__":
    asyncio.run(main())
