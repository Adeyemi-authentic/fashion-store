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
    catalog = build_catalog()
    active = 0
    for p in catalog:
        with_image = has_image(p)
        active += with_image
        stmt = insert(Product).values(
            slug=p.slug,
            name=p.name,
            category=p.category,
            description=p.description,
            base_price_kobo=p.base_price_kobo,
            attributes=p.attributes,
            status="active" if with_image else "draft",
            created_at=p.created_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Product.slug],
            set_={
                "name": stmt.excluded.name,
                "category": stmt.excluded.category,
                "description": stmt.excluded.description,
                "base_price_kobo": stmt.excluded.base_price_kobo,
                "attributes": stmt.excluded.attributes,
                "status": case(
                    (Product.status == "archived", "archived"), else_=stmt.excluded.status
                ),
            },
        ).returning(Product.id)
        product_id = (await session.execute(stmt)).scalar_one()

        for v in p.variants:
            vstmt = insert(Variant).values(
                product_id=product_id,
                sku=v.sku,
                size=v.size,
                colour=v.colour,
                price_kobo=v.price_kobo,
                stock_on_hand=v.stock_on_hand,
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

        if not with_image:
            continue
        istmt = insert(ProductImage).values(
            product_id=product_id, url=p.image_url, alt=p.image_alt, position=0
        )
        await session.execute(
            istmt.on_conflict_do_update(
                constraint="uq_product_images_product_position",
                set_={"url": istmt.excluded.url, "alt": istmt.excluded.alt},
            )
        )
    await session.commit()
    return SeedResult(total=len(catalog), active=active)


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
