import math

from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.modules.catalog import service as catalog
from app.modules.catalog.models import Product
from app.modules.search.embeddings import refresh_embeddings
from app.platform import embeddings, worker
from app.platform.embeddings import HashEmbedder
from app.platform.jobs import register_all
from app.platform.models import OutboxJob
from tests.conftest import sign_up


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


class CountingEmbedder(HashEmbedder):
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, texts, kind):  # type: ignore[no-untyped-def]
        self.texts.extend(texts)
        return await super().embed(texts, kind)


async def test_seeded_catalog_is_fully_embedded(session: AsyncSession) -> None:
    missing = await session.scalar(select(func.count()).where(Product.embedding_hash.is_(None)))
    assert missing == 0
    assert await catalog.stale_embeddings(session, HashEmbedder.model) == []


async def test_refresh_makes_no_api_call_when_nothing_changed(
    session: AsyncSession, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    counter = CountingEmbedder()
    monkeypatch.setattr(embeddings, "_embedder", counter)
    assert await refresh_embeddings(session) == 0
    assert counter.texts == []


async def test_a_changed_model_makes_every_embedding_stale(session: AsyncSession) -> None:
    total = await session.scalar(select(func.count()).select_from(Product))
    assert len(await catalog.stale_embeddings(session, "some-other-model")) == total


async def test_embedding_made_from_old_text_is_not_saved(session: AsyncSession) -> None:
    product = (await session.execute(select(Product).limit(1))).scalar_one()
    [item] = await catalog.stale_embeddings(session, "m2", [product.id])
    old_name = product.name
    product.name = old_name + " (edited while embedding)"
    await session.flush()
    vector = (await HashEmbedder().embed([item.text], "document"))[0]
    assert await catalog.save_embedding(session, product.id, vector, item.hash, "m2") is False
    await session.rollback()


async def test_hash_embedder_puts_shared_words_closer() -> None:
    e = HashEmbedder()
    a, b, c = await e.embed(["ankara wrap dress", "ankara dress", "brass cuff"], "query")
    assert cosine(a, b) > cosine(a, c)


async def run_jobs() -> list[str]:
    register_all()
    outcomes: list[str] = []
    while batch := await worker.run_once(get_sessionmaker()):
        outcomes += batch
    return outcomes


async def test_admin_edit_queues_a_refresh_and_the_job_applies_it(
    make_client, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    admin: AsyncClient = make_client()
    await sign_up(admin, role="admin")
    await session.execute(delete(OutboxJob))
    await session.commit()
    product = (
        await session.execute(select(Product).where(Product.status == "active").limit(1))
    ).scalar_one()
    product_id, before, old_name = product.id, product.embedding_hash, product.name

    resp = await admin.patch(f"/admin/products/{product.id}", json={"name": "Renamed Wrap"})
    assert resp.status_code == 200
    jobs = (await session.execute(select(OutboxJob.type, OutboxJob.payload))).all()
    assert [(t, p["product_id"]) for t, p in jobs] == [("embed_product", str(product.id))]

    assert await run_jobs() == ["done"]
    session.expire_all()
    after = await session.scalar(select(Product.embedding_hash).where(Product.id == product_id))
    assert after != before

    # A price-only change doesn't touch the text, so no job.
    variant = (await admin.get(f"/admin/products/{product_id}")).json()["variants"][0]
    await admin.patch(f"/admin/variants/{variant['id']}", json={"price_kobo": 1_234_500})
    queued = await session.scalar(select(func.count()).where(OutboxJob.status == "queued"))
    await admin.patch(
        f"/admin/variants/{variant['id']}", json={"price_kobo": variant["price_kobo"]}
    )
    assert queued == 0

    await admin.patch(f"/admin/products/{product_id}", json={"name": old_name})
    await run_jobs()
    await session.execute(delete(OutboxJob))
    await session.commit()
