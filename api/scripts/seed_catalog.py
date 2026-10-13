"""Seed the FlyFit catalog. Safe to run any number of times.

    uv run python -m scripts.seed_catalog

Products upsert on `slug`, variants on `sku`, images on (product_id, position). Re-running
updates names, prices and descriptions but never touches stock or `created_at`: stock belongs
to orders.inventory once the store is live (ADR-003), and a re-seed must not undo real sales.

A product is `active` only if its image exists in web/public; otherwise it is seeded as
`draft` (hidden) with no image row. Generating the missing image and re-seeding makes it live.
Products an admin has `archived` stay archived.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import dispose_engine, get_sessionmaker
from app.modules.catalog.models import Product, ProductImage, Variant
from scripts.catalog_data import build_catalog, has_image


@dataclass(frozen=True)
class SeedResult:
    total: int
    active: int


async def seed(session: AsyncSession) -> SeedResult:
    """Three multi-row upserts (products, variants, images) in one transaction.

    Batched because every statement is a network round trip: row-by-row took 6+ minutes
    from Lagos to a us-east-2 Neon database; batched it is a few seconds.
    """
    catalog = build_catalog()
    with_image = {p.slug for p in catalog if has_image(p)}

    pstmt = insert(Product).values(
        [
            {
                "slug": p.slug,
                "name": p.name,
                "category": p.category,
                "description": p.description,
                "base_price_kobo": p.base_price_kobo,
                "attributes": p.attributes,
                "status": "active" if p.slug in with_image else "draft",
                "created_at": p.created_at,
            }
            for p in catalog
        ]
    )
    pstmt = pstmt.on_conflict_do_update(
        index_elements=[Product.slug],
        set_={
            "name": pstmt.excluded.name,
            "category": pstmt.excluded.category,
            "description": pstmt.excluded.description,
            "base_price_kobo": pstmt.excluded.base_price_kobo,
            "attributes": pstmt.excluded.attributes,
            "status": case((Product.status == "archived", "archived"), else_=pstmt.excluded.status),
        },
    ).returning(Product.slug, Product.id)
    ids = dict((await session.execute(pstmt)).all())

    vstmt = insert(Variant).values(
        [
            {
                "product_id": ids[p.slug],
                "sku": v.sku,
                "size": v.size,
                "colour": v.colour,
                "price_kobo": v.price_kobo,
                "stock_on_hand": v.stock_on_hand,
            }
            for p in catalog
            for v in p.variants
        ]
    )
    await session.execute(
        vstmt.on_conflict_do_update(
            index_elements=[Variant.sku],
            set_={
                "size": vstmt.excluded.size,
                "colour": vstmt.excluded.colour,
                "price_kobo": vstmt.excluded.price_kobo,
            },
        )
    )

    images = [
        {"product_id": ids[p.slug], "url": p.image_url, "alt": p.image_alt, "position": 0}
        for p in catalog
        if p.slug in with_image
    ]
    if images:
        istmt = insert(ProductImage).values(images)
        await session.execute(
            istmt.on_conflict_do_update(
                constraint="uq_product_images_product_position",
                set_={"url": istmt.excluded.url, "alt": istmt.excluded.alt},
            )
        )
    await session.commit()
    return SeedResult(total=len(catalog), active=len(with_image))


async def main() -> None:
    async with get_sessionmaker()() as session:
        result = await seed(session)
    await dispose_engine()
    print(
        f"seeded {result.total} products: {result.active} active, "
        f"{result.total - result.active} draft (no image yet)"
    )


if __name__ == "__main__":
    asyncio.run(main())
