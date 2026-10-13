import pytest
from alembic import command
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Product, ProductImage, Variant
from scripts.catalog_data import build_catalog
from scripts.seed_catalog import seed
from tests.conftest import alembic_config


async def counts(session: AsyncSession) -> tuple[int, int, int]:
    p = (await session.execute(select(func.count()).select_from(Product))).scalar_one()
    v = (await session.execute(select(func.count()).select_from(Variant))).scalar_one()
    i = (await session.execute(select(func.count()).select_from(ProductImage))).scalar_one()
    return p, v, i


def test_catalog_is_deterministic() -> None:
    a, b = build_catalog(), build_catalog()
    assert [p.slug for p in a] == [p.slug for p in b]
    assert [v.sku for p in a for v in p.variants] == [v.sku for p in b for v in p.variants]


def test_catalog_shape() -> None:
    catalog = build_catalog()
    assert 140 <= len(catalog) <= 160
    assert len({p.category for p in catalog}) == 7
    assert len({p.slug for p in catalog}) == len(catalog)
    for p in catalog:
        assert p.base_price_kobo == min(v.price_kobo for v in p.variants)
        for v in p.variants:
            assert v.price_kobo % 50_000 == 0, "prices are whole ₦500 steps, stored in kobo"


async def test_seed_twice_creates_no_duplicates(session: AsyncSession) -> None:
    before = await counts(session)
    assert before[0] == len(build_catalog())
    await seed(session)
    assert await counts(session) == before


async def test_reseed_keeps_stock_and_created_at(session: AsyncSession) -> None:
    variant_id, product_id, original_stock = (
        await session.execute(
            select(Variant.id, Variant.product_id, Variant.stock_on_hand).limit(1)
        )
    ).one()
    original_created = (
        await session.execute(select(Product.created_at).where(Product.id == product_id))
    ).scalar_one()

    await session.execute(update(Variant).where(Variant.id == variant_id).values(stock_on_hand=999))
    await session.commit()
    try:
        await seed(session)
        stock = (
            await session.execute(select(Variant.stock_on_hand).where(Variant.id == variant_id))
        ).scalar_one()
        created = (
            await session.execute(select(Product.created_at).where(Product.id == product_id))
        ).scalar_one()
        assert stock == 999
        assert created == original_created
    finally:
        await session.execute(
            update(Variant).where(Variant.id == variant_id).values(stock_on_hand=original_stock)
        )
        await session.commit()


@pytest.mark.parametrize(
    ("on_hand", "reserved"),
    [(5, 6), (5, -1), (-1, 0)],
    ids=["reserved-above-on-hand", "negative-reserved", "negative-on-hand"],
)
async def test_stock_check_constraints_block_bad_writes(
    session: AsyncSession, on_hand: int, reserved: int
) -> None:
    """The overselling backstop from ADR-003 lives in the database, not just the code."""
    variant_id = (await session.execute(select(Variant.id).limit(1))).scalar_one()
    with pytest.raises(IntegrityError):
        await session.execute(
            update(Variant)
            .where(Variant.id == variant_id)
            .values(stock_on_hand=on_hand, stock_reserved=reserved)
        )
        await session.flush()
    await session.rollback()


async def test_search_tsv_is_generated(session: AsyncSession) -> None:
    hits = (
        await session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.search_tsv.op("@@")(func.plainto_tsquery("english", "agbada")))
        )
    ).scalar_one()
    assert hits > 0


async def test_rate_limits_table_is_unlogged(session: AsyncSession) -> None:
    persistence = (
        await session.execute(
            text("SELECT relpersistence FROM pg_class WHERE relname = 'rate_limits'")
        )
    ).scalar_one()
    assert persistence == "u"


def test_migrations_match_models_and_round_trip(make_database) -> None:  # type: ignore[no-untyped-def]
    """The CI migrations check: models and migrations agree, and down/up both work."""
    cfg = alembic_config(make_database("flyfit_migrations"))
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models have changes no migration covers
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_products_without_images_are_drafts(session: AsyncSession) -> None:
    from scripts.catalog_data import has_image

    catalog = build_catalog()
    statuses = dict((await session.execute(select(Product.slug, Product.status))).all())
    for p in catalog:
        assert statuses[p.slug] == ("active" if has_image(p) else "draft"), p.slug
    images = (
        await session.execute(
            select(Product.slug).join(ProductImage, ProductImage.product_id == Product.id)
        )
    ).scalars()
    assert set(images) == {p.slug for p in catalog if has_image(p)}


async def test_reseed_keeps_archived_products_archived(session: AsyncSession) -> None:
    slug = (
        await session.execute(select(Product.slug).where(Product.status == "active").limit(1))
    ).scalar_one()
    await session.execute(update(Product).where(Product.slug == slug).values(status="archived"))
    await session.commit()
    try:
        await seed(session)
        status = (
            await session.execute(select(Product.status).where(Product.slug == slug))
        ).scalar_one()
        assert status == "archived"
    finally:
        await session.execute(update(Product).where(Product.slug == slug).values(status="active"))
        await session.commit()
