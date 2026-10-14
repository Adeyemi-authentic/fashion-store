"""Catalog reads. Public interface of the catalog module (ADR-001)."""

import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.catalog.models import Product, ProductImage, Variant
from app.modules.catalog.retrieval import (  # noqa: F401  (public interface)
    EmbeddingInput,
    SearchFilters,
    SearchVocabulary,
    embedding_text,
    keyword_candidates,
    save_embedding,
    search_vocabulary,
    stale_embeddings,
    vector_candidates,
)
from app.modules.catalog.retrieval import (
    similar_products as _similar_products,
)
from app.modules.catalog.schemas import (
    AdminProductOut,
    AdminProductPage,
    AdminProductRow,
    AdminVariantOut,
    CategoryOut,
    FacetsOut,
    ImageOut,
    ProductCard,
    ProductCreateIn,
    ProductDetail,
    ProductPage,
    ProductSort,
    ProductUpdateIn,
    StockOut,
    VariantIn,
    VariantOut,
)
from app.modules.orders.availability import available_qty
from app.platform.outbox import enqueue
from app.shared.pagination import InvalidCursor, decode_cursor, encode_cursor

MAX_PAGE_SIZE = 60
# Clothing sizes in shop order; anything else (e.g. "One Size") sorts after these.
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]


@dataclass(frozen=True)
class ProductFilters:
    category: str | None = None
    min_price_kobo: int | None = None
    max_price_kobo: int | None = None
    size: str | None = None
    colour: str | None = None
    in_stock: bool | None = None


def _sort_column(sort: ProductSort):  # type: ignore[no-untyped-def]
    return {
        ProductSort.newest: Product.created_at,
        ProductSort.price_asc: Product.base_price_kobo,
        ProductSort.price_desc: Product.base_price_kobo,
        ProductSort.name: Product.name,
    }[sort]


def _is_descending(sort: ProductSort) -> bool:
    return sort in (ProductSort.newest, ProductSort.price_desc)


def _cursor_value(product: Product, sort: ProductSort) -> str | int:
    value = getattr(product, _sort_column(sort).key)
    return value.isoformat() if isinstance(value, datetime) else value


def _parse_cursor(cursor: str, sort: ProductSort) -> tuple[datetime | int | str, uuid.UUID]:
    raw_value, raw_id = decode_cursor(cursor, sort.value)
    expected = {ProductSort.newest: str, ProductSort.name: str}.get(sort, int)
    try:
        if not isinstance(raw_value, expected) or isinstance(raw_value, bool):
            raise ValueError("wrong type")
        value = datetime.fromisoformat(str(raw_value)) if sort is ProductSort.newest else raw_value
        return value, uuid.UUID(raw_id)
    except (TypeError, ValueError) as exc:
        raise InvalidCursor("cursor is not valid") from exc


def _in_stock_clause():  # type: ignore[no-untyped-def]
    return exists().where(Variant.product_id == Product.id, available_qty() > 0)


def _apply_filters(stmt: Select, f: ProductFilters) -> Select:  # type: ignore[type-arg]
    if f.category:
        stmt = stmt.where(Product.category == f.category)
    if f.min_price_kobo is not None:
        stmt = stmt.where(Product.base_price_kobo >= f.min_price_kobo)
    if f.max_price_kobo is not None:
        stmt = stmt.where(Product.base_price_kobo <= f.max_price_kobo)
    if f.size or f.colour:
        conds = [Variant.product_id == Product.id]
        if f.size:
            conds.append(func.lower(Variant.size) == f.size.lower())
        if f.colour:
            conds.append(func.lower(Variant.colour) == f.colour.lower())
        stmt = stmt.where(exists().where(*conds))
    if f.in_stock is True:
        stmt = stmt.where(_in_stock_clause())
    elif f.in_stock is False:
        stmt = stmt.where(~_in_stock_clause())
    return stmt


async def list_products(
    session: AsyncSession,
    filters: ProductFilters,
    sort: ProductSort = ProductSort.newest,
    limit: int = 24,
    cursor: str | None = None,
) -> ProductPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    col = _sort_column(sort)
    desc = _is_descending(sort)

    stmt = (
        select(Product, _in_stock_clause().label("in_stock"))
        .where(Product.status == "active")
        .options(selectinload(Product.images))
    )
    stmt = _apply_filters(stmt, filters)

    if cursor:
        value, last_id = _parse_cursor(cursor, sort)
        # Keyset condition written out instead of a row-value comparison so it works with
        # mixed directions later; both columns sort the same way here.
        if desc:
            stmt = stmt.where(or_(col < value, and_(col == value, Product.id < last_id)))
        else:
            stmt = stmt.where(or_(col > value, and_(col == value, Product.id > last_id)))

    order = (col.desc(), Product.id.desc()) if desc else (col.asc(), Product.id.asc())
    rows = (await session.execute(stmt.order_by(*order).limit(limit + 1))).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_card(product, in_stock) for product, in_stock in rows]
    next_cursor = None
    if has_more:
        last = rows[-1][0]
        next_cursor = encode_cursor(sort.value, _cursor_value(last, sort), str(last.id))
    return ProductPage(items=items, next_cursor=next_cursor)


def _first_image(images: list[ProductImage]) -> ImageOut | None:
    return ImageOut(url=images[0].url, alt=images[0].alt) if images else None


def _card(product: Product, in_stock: bool) -> ProductCard:
    return ProductCard(
        id=product.id,
        slug=product.slug,
        name=product.name,
        category=product.category,
        price_kobo=product.base_price_kobo,
        in_stock=bool(in_stock),
        image=_first_image(product.images),
    )


async def similar_products(
    session: AsyncSession, product_id: uuid.UUID, limit: int = 8
) -> list[uuid.UUID]:
    return await _similar_products(session, product_id, limit, _in_stock_clause())


async def product_id_for_slug(session: AsyncSession, slug: str) -> uuid.UUID | None:
    return await session.scalar(
        select(Product.id).where(Product.slug == slug, Product.status == "active")
    )


async def cards_by_id(session: AsyncSession, ids: list[uuid.UUID]) -> list[ProductCard]:
    """Cards for these active products, in the order given (search and recommendations
    decide the order; this only fills in the details)."""
    if not ids:
        return []
    rows = (
        await session.execute(
            select(Product, _in_stock_clause().label("in_stock"))
            .where(Product.id.in_(ids), Product.status == "active")
            .options(selectinload(Product.images))
        )
    ).all()
    by_id = {p.id: _card(p, in_stock) for p, in_stock in rows}
    return [by_id[i] for i in ids if i in by_id]


async def get_product(session: AsyncSession, slug: str) -> ProductDetail | None:
    product = (
        await session.execute(
            select(Product)
            .where(Product.slug == slug, Product.status == "active")
            .options(selectinload(Product.images))
        )
    ).scalar_one_or_none()
    if product is None:
        return None

    variant_rows = (
        await session.execute(
            select(Variant, available_qty().label("available"))
            .where(Variant.product_id == product.id)
            .order_by(Variant.colour, Variant.price_kobo, Variant.sku)
        )
    ).all()
    variants = [
        VariantOut(
            id=v.id,
            sku=v.sku,
            size=v.size,
            colour=v.colour,
            price_kobo=v.price_kobo,
            available=max(0, available),
        )
        for v, available in variant_rows
    ]
    return ProductDetail(
        id=product.id,
        slug=product.slug,
        name=product.name,
        description=product.description,
        category=product.category,
        price_kobo=product.base_price_kobo,
        attributes=product.attributes,
        in_stock=any(v.available > 0 for v in variants),
        variants=variants,
        images=[ImageOut(url=i.url, alt=i.alt) for i in product.images],
    )


async def get_stock(session: AsyncSession, slug: str) -> list[StockOut] | None:
    product_id = await session.scalar(
        select(Product.id).where(Product.slug == slug, Product.status == "active")
    )
    if product_id is None:
        return None
    rows = await session.execute(
        select(Variant.id, available_qty()).where(Variant.product_id == product_id)
    )
    return [StockOut(variant_id=v, available=max(0, int(a))) for v, a in rows]


async def list_categories(session: AsyncSession) -> list[CategoryOut]:
    rows = await session.execute(
        select(Product.category, func.count())
        .where(Product.status == "active")
        .group_by(Product.category)
        .order_by(Product.category)
    )
    return [CategoryOut(slug=category, product_count=count) for category, count in rows]


async def get_facets(session: AsyncSession, category: str | None = None) -> FacetsOut:
    base = (
        select(Variant.size, Variant.colour)
        .join(Product, Product.id == Variant.product_id)
        .where(Product.status == "active")
    )
    prices = select(func.min(Product.base_price_kobo), func.max(Product.base_price_kobo)).where(
        Product.status == "active"
    )
    if category:
        base = base.where(Product.category == category)
        prices = prices.where(Product.category == category)
    rows = (await session.execute(base.distinct())).all()
    lo, hi = (await session.execute(prices)).one()

    def size_key(size: str) -> tuple[int, str]:
        return (SIZE_ORDER.index(size), "") if size in SIZE_ORDER else (len(SIZE_ORDER), size)

    return FacetsOut(
        sizes=sorted({size for size, _ in rows}, key=size_key),
        colours=sorted({colour for _, colour in rows}),
        min_price_kobo=lo,
        max_price_kobo=hi,
    )


@dataclass(frozen=True)
class VariantSummary:
    """What other modules (cart, later orders) need to show or price a variant."""

    variant_id: uuid.UUID
    sku: str
    product_slug: str
    product_name: str
    size: str
    colour: str
    price_kobo: int
    available: int
    image: ImageOut | None
    purchasable: bool  # False once the product is no longer active


async def get_variant_summaries(
    session: AsyncSession, variant_ids: list[uuid.UUID]
) -> dict[uuid.UUID, VariantSummary]:
    if not variant_ids:
        return {}
    rows = (
        await session.execute(
            select(Variant, Product, available_qty().label("available"))
            .join(Product, Product.id == Variant.product_id)
            .where(Variant.id.in_(variant_ids))
            .options(selectinload(Product.images))
        )
    ).all()
    return {
        v.id: VariantSummary(
            variant_id=v.id,
            sku=v.sku,
            product_slug=p.slug,
            product_name=p.name,
            size=v.size,
            colour=v.colour,
            price_kobo=v.price_kobo,
            available=max(0, available),
            image=_first_image(p.images),
            purchasable=p.status == "active",
        )
        for v, p, available in rows
    }


# ---------------------------------------------------------------------------------------------
# Admin writes. These flush but never commit: the admin router commits once, so a product and
# its starting stock (written by orders.inventory) land together or not at all.


# Fields that feed embedding_text(): changing one makes the product's embedding stale.
EMBEDDED_FIELDS = {"name", "description", "category", "attributes"}


class ProductNotFound(LookupError):
    pass


class CannotPublish(ValueError):
    pass


class DuplicateVariant(ValueError):
    pass


def _slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _sku_part(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", text.upper())[:12] or "X"


async def _unique_slug(session: AsyncSession, base: str) -> str:
    taken = set(
        (
            await session.execute(
                select(Product.slug).where(
                    or_(Product.slug == base, Product.slug.like(f"{base}-%"))
                )
            )
        ).scalars()
    )
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _new_variant(product_id: uuid.UUID, v: VariantIn) -> Variant:
    sku = f"FF-{secrets.token_hex(3).upper()}-{_sku_part(v.colour)}-{_sku_part(v.size)}"
    return Variant(
        product_id=product_id, sku=sku, size=v.size, colour=v.colour, price_kobo=v.price_kobo
    )


async def _sync_base_price(session: AsyncSession, product_id: uuid.UUID) -> None:
    """base_price_kobo is the 'from' price on listings: always the lowest variant price."""
    await session.execute(
        update(Product)
        .where(Product.id == product_id)
        .values(
            base_price_kobo=select(func.min(Variant.price_kobo))
            .where(Variant.product_id == product_id)
            .scalar_subquery()
        )
    )


async def _check_publishable(session: AsyncSession, product_id: uuid.UUID) -> None:
    has_variant = await session.scalar(select(exists().where(Variant.product_id == product_id)))
    has_image = await session.scalar(select(exists().where(ProductImage.product_id == product_id)))
    if not has_variant:
        raise CannotPublish("Add at least one variant before publishing")
    if not has_image:
        raise CannotPublish("Add an image before publishing")


async def _set_image(session: AsyncSession, product: Product, url: str) -> None:
    await session.execute(delete(ProductImage).where(ProductImage.product_id == product.id))
    session.add(
        ProductImage(
            product_id=product.id, url=url, alt=f"{product.name} (AI-generated image)", position=0
        )
    )


async def create_product(
    session: AsyncSession, data: ProductCreateIn
) -> tuple[uuid.UUID, list[tuple[uuid.UUID, int]]]:
    """Returns the product id and (variant id, starting stock) pairs. The caller sets the
    stock through orders.inventory, which is the only code allowed to write stock columns."""
    keys = {(v.size.lower(), v.colour.lower()) for v in data.variants}
    if len(keys) != len(data.variants):
        raise DuplicateVariant("Two variants have the same size and colour")
    product = Product(
        slug=await _unique_slug(session, _slugify(f"{data.category} {data.name}")),
        name=data.name,
        description=data.description,
        category=data.category,
        status="draft",
        attributes=data.attributes,
        base_price_kobo=min(v.price_kobo for v in data.variants),
    )
    session.add(product)
    await session.flush()
    variants = [_new_variant(product.id, v) for v in data.variants]
    session.add_all(variants)
    if data.image_url:
        await _set_image(session, product, data.image_url)
    await session.flush()
    if data.status == "active":
        await _check_publishable(session, product.id)
        product.status = "active"
    # Same transaction as the product: it can't exist without its embedding job (ADR-002).
    await enqueue(session, "embed_product", {"product_id": str(product.id)})
    return product.id, [
        (v.id, d.stock_on_hand) for v, d in zip(variants, data.variants, strict=True)
    ]


async def update_product(
    session: AsyncSession, product_id: uuid.UUID, data: ProductUpdateIn
) -> str:
    """Returns the slug (so the storefront can refresh that page's cache)."""
    product = await session.get(Product, product_id, with_for_update=True)
    if product is None:
        raise ProductNotFound(product_id)
    changes = data.model_dump(exclude_unset=True, exclude={"image_url", "status"})
    for field, value in changes.items():
        setattr(product, field, value)
    if data.image_url is not None:
        await _set_image(session, product, data.image_url)
        await session.flush()
    if data.status is not None and data.status != product.status:
        if data.status == "active":
            await _check_publishable(session, product.id)
        # archived = soft delete. Orders keep pointing at its variants, so rows never go away.
        product.status = data.status
    product.updated_at = func.now()  # type: ignore[assignment]
    await session.flush()
    if EMBEDDED_FIELDS & changes.keys():
        await enqueue(session, "embed_product", {"product_id": str(product.id)})
    return product.slug


async def add_variant(
    session: AsyncSession, product_id: uuid.UUID, data: VariantIn
) -> tuple[uuid.UUID, str]:
    slug = await session.scalar(select(Product.slug).where(Product.id == product_id))
    if slug is None:
        raise ProductNotFound(product_id)
    clash = await session.scalar(
        select(
            exists().where(
                Variant.product_id == product_id,
                func.lower(Variant.size) == data.size.lower(),
                func.lower(Variant.colour) == data.colour.lower(),
            )
        )
    )
    if clash:
        raise DuplicateVariant("This product already has that size and colour")
    variant = _new_variant(product_id, data)
    session.add(variant)
    await session.flush()
    await _sync_base_price(session, product_id)
    return variant.id, slug


async def set_variant_price(session: AsyncSession, variant_id: uuid.UUID, price_kobo: int) -> str:
    """Changes the price for future carts and orders. Orders already placed keep the price
    they copied at checkout (ADR-004), so this never changes what someone was charged."""
    row = (
        await session.execute(
            update(Variant)
            .where(Variant.id == variant_id)
            .values(price_kobo=price_kobo, updated_at=func.now())
            .returning(Variant.product_id)
        )
    ).one_or_none()
    if row is None:
        raise ProductNotFound(variant_id)
    await _sync_base_price(session, row.product_id)
    return await session.scalar(select(Product.slug).where(Product.id == row.product_id)) or ""


async def product_id_for_variant(session: AsyncSession, variant_id: uuid.UUID) -> uuid.UUID:
    product_id = await session.scalar(select(Variant.product_id).where(Variant.id == variant_id))
    if product_id is None:
        raise ProductNotFound(variant_id)
    return product_id


async def admin_get_product(session: AsyncSession, product_id: uuid.UUID) -> AdminProductOut | None:
    product = (
        await session.execute(
            select(Product).where(Product.id == product_id).options(selectinload(Product.images))
        )
    ).scalar_one_or_none()
    if product is None:
        return None
    rows = (
        await session.execute(
            select(Variant, available_qty().label("available"))
            .where(Variant.product_id == product_id)
            .order_by(Variant.colour, Variant.price_kobo, Variant.sku)
        )
    ).all()
    return AdminProductOut(
        id=product.id,
        slug=product.slug,
        name=product.name,
        description=product.description,
        category=product.category,
        status=product.status,  # type: ignore[arg-type]
        price_kobo=product.base_price_kobo,
        attributes=product.attributes,
        image=_first_image(product.images),
        variants=[
            AdminVariantOut(
                id=v.id,
                sku=v.sku,
                size=v.size,
                colour=v.colour,
                price_kobo=v.price_kobo,
                stock_on_hand=v.stock_on_hand,
                stock_reserved=v.stock_reserved,
                available=max(0, available),
            )
            for v, available in rows
        ],
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


ADMIN_SORT = "admin_name"


async def admin_list_products(
    session: AsyncSession,
    q: str | None = None,
    status: str | None = None,
    category: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> AdminProductPage:
    """Every product whatever its status, by name, keyset-paginated like the storefront."""
    stats = (
        select(
            Variant.product_id,
            func.count().label("variant_count"),
            func.coalesce(func.sum(func.greatest(available_qty(), 0)), 0).label("available"),
        )
        .group_by(Variant.product_id)
        .subquery()
    )
    stmt = (
        select(Product, stats.c.variant_count, stats.c.available)
        .outerjoin(stats, stats.c.product_id == Product.id)
        .options(selectinload(Product.images))
    )
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(Product.status == status)
    if category:
        stmt = stmt.where(Product.category == category)
    if cursor:
        value, raw_id = decode_cursor(cursor, ADMIN_SORT)
        try:
            last_id = uuid.UUID(raw_id)
        except (TypeError, ValueError) as exc:
            raise InvalidCursor("cursor is not valid") from exc
        if not isinstance(value, str):
            raise InvalidCursor("cursor is not valid")
        stmt = stmt.where(
            or_(Product.name > value, and_(Product.name == value, Product.id > last_id))
        )
    rows = (await session.execute(stmt.order_by(Product.name, Product.id).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        AdminProductRow(
            id=p.id,
            slug=p.slug,
            name=p.name,
            category=p.category,
            status=p.status,  # type: ignore[arg-type]
            price_kobo=p.base_price_kobo,
            variant_count=count or 0,
            available=int(avail or 0),
            image=_first_image(p.images),
        )
        for p, count, avail in rows
    ]
    next_cursor = (
        encode_cursor(ADMIN_SORT, rows[-1][0].name, str(rows[-1][0].id)) if has_more else None
    )
    return AdminProductPage(items=items, next_cursor=next_cursor)
