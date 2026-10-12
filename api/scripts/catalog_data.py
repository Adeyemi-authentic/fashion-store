"""Deterministic FlyFit catalog: about 150 products across 7 categories.

Everything is generated from a fixed random seed, so the same slugs, SKUs and prices come out
on every run. That is what lets `seed_catalog.py` upsert safely instead of duplicating rows.
Prices are realistic Lagos retail in naira (2026), rounded to ₦500, stored as kobo.
Descriptions are deliberately plain templates; the Phase 6 pipeline rewrites them from
`attributes`.
"""

import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

SEED = 20261012
CATALOG_EPOCH = datetime(2026, 10, 1, tzinfo=UTC)

WOMEN_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
MEN_SIZES = ["S", "M", "L", "XL", "XXL"]
UNISEX_SIZES = ["S", "M", "L", "XL"]
ONE_SIZE = ["One Size"]


@dataclass(frozen=True)
class ProductType:
    name: str
    gender: str  # women | men | unisex
    price_naira: tuple[int, int]
    sizes: list[str]
    fit: str


@dataclass(frozen=True)
class Category:
    slug: str
    count: int
    fabric: str
    occasions: list[str]
    colours: list[str]
    types: list[ProductType]
    patterns: list[str] = field(default_factory=list)


# Pattern / collection names used in product names. Plain ASCII so slugs stay readable.
PRINT_NAMES = [
    "Lagoon",
    "Sunburst",
    "Eko Nights",
    "Harmattan",
    "Indigo Tide",
    "Market Day",
    "Palm Frond",
    "Kola",
    "Oyin",
    "Imole",
    "Ireti",
    "Owambe",
    "Bronze",
    "Lekki Sunset",
    "Yaba",
    "Ikoyi",
    "Calabar",
    "Ayo",
    "Ife",
    "Banana Island",
    "Tarkwa",
    "Ojuelegba",
    "Obalende",
    "Akoka",
    "Shitta",
    "Agege",
    "Badagry",
    "Epe",
    "Festac",
    "Ilaje",
]

CATEGORIES: list[Category] = [
    Category(
        slug="ankara",
        count=28,
        fabric="100% cotton ankara wax print",
        occasions=["casual", "work", "party"],
        colours=["Orange/Teal", "Yellow/Blue", "Red/Green", "Purple/Gold", "Pink/Black"],
        types=[
            ProductType("Wrap Dress", "women", (28_000, 65_000), WOMEN_SIZES, "relaxed"),
            ProductType("Peplum Top", "women", (18_000, 35_000), WOMEN_SIZES, "fitted"),
            ProductType("Maxi Skirt", "women", (20_000, 40_000), WOMEN_SIZES, "flowing"),
            ProductType("Two-Piece Set", "women", (35_000, 75_000), WOMEN_SIZES, "tailored"),
            ProductType("Bomber Jacket", "unisex", (30_000, 60_000), UNISEX_SIZES, "boxy"),
            ProductType("Short-Sleeve Shirt", "men", (18_000, 38_000), MEN_SIZES, "regular"),
            ProductType("Kimono", "unisex", (22_000, 45_000), UNISEX_SIZES, "oversized"),
        ],
    ),
    Category(
        slug="adire",
        count=24,
        fabric="hand-dyed adire cotton (indigo resist-dye)",
        occasions=["casual", "work"],
        colours=["Deep Indigo", "Sky Indigo", "Indigo/White", "Rust", "Charcoal"],
        types=[
            ProductType("Kaftan Dress", "women", (35_000, 85_000), WOMEN_SIZES, "relaxed"),
            ProductType("Two-Piece Set", "unisex", (40_000, 90_000), UNISEX_SIZES, "relaxed"),
            ProductType("Wide-Leg Trousers", "unisex", (22_000, 45_000), UNISEX_SIZES, "wide"),
            ProductType("Button-Up Shirt", "men", (25_000, 48_000), MEN_SIZES, "regular"),
            ProductType("Boubou", "women", (45_000, 95_000), WOMEN_SIZES, "flowing"),
            ProductType("Scarf", "unisex", (9_000, 20_000), ONE_SIZE, "one size"),
        ],
    ),
    Category(
        slug="aso-oke",
        count=18,
        fabric="hand-woven aso-oke",
        occasions=["wedding", "owambe", "traditional"],
        colours=["Wine/Gold", "Emerald/Silver", "Champagne", "Royal Blue/Gold", "Coral"],
        types=[
            ProductType("Iro and Buba Set", "women", (120_000, 350_000), WOMEN_SIZES, "tailored"),
            ProductType("Gele and Ipele Set", "women", (60_000, 180_000), ONE_SIZE, "one size"),
            ProductType("Buba and Sokoto", "men", (110_000, 300_000), MEN_SIZES, "tailored"),
            ProductType("Bridal Set", "women", (250_000, 600_000), WOMEN_SIZES, "tailored"),
            ProductType("Waistcoat", "men", (45_000, 110_000), MEN_SIZES, "fitted"),
        ],
    ),
    Category(
        slug="agbada-kaftans",
        count=20,
        fabric="cotton-blend with hand embroidery",
        occasions=["wedding", "owambe", "eid", "traditional"],
        colours=["White/Gold", "Navy", "Sage", "Burgundy", "Cream", "Black"],
        types=[
            ProductType("Three-Piece Agbada", "men", (180_000, 650_000), MEN_SIZES, "flowing"),
            ProductType("Senator Kaftan", "men", (45_000, 120_000), MEN_SIZES, "tailored"),
            ProductType("Embroidered Kaftan", "men", (55_000, 150_000), MEN_SIZES, "regular"),
            ProductType("Dashiki Kaftan", "unisex", (30_000, 70_000), UNISEX_SIZES, "relaxed"),
            ProductType("Grand Boubou", "men", (150_000, 450_000), MEN_SIZES, "flowing"),
        ],
    ),
    Category(
        slug="ready-to-wear",
        count=28,
        fabric="linen and cotton blends",
        occasions=["casual", "work"],
        colours=["Sand", "Olive", "Black", "Off-White", "Terracotta", "Navy"],
        types=[
            ProductType("Linen Shirt", "men", (22_000, 45_000), MEN_SIZES, "relaxed"),
            ProductType("Tailored Trousers", "unisex", (25_000, 55_000), UNISEX_SIZES, "tailored"),
            ProductType("Slip Dress", "women", (28_000, 60_000), WOMEN_SIZES, "bias-cut"),
            ProductType("Oversized Tee", "unisex", (12_000, 25_000), UNISEX_SIZES, "oversized"),
            ProductType("Cargo Trousers", "unisex", (22_000, 42_000), UNISEX_SIZES, "relaxed"),
            ProductType("Co-ord Set", "women", (40_000, 80_000), WOMEN_SIZES, "relaxed"),
            ProductType("Blazer", "unisex", (55_000, 120_000), UNISEX_SIZES, "tailored"),
        ],
    ),
    Category(
        slug="accessories",
        count=20,
        fabric="mixed materials",
        occasions=["party", "wedding", "owambe"],
        colours=["Coral", "Gold", "Tan", "Black", "Multicolour"],
        types=[
            ProductType("Beaded Necklace", "unisex", (12_000, 60_000), ONE_SIZE, "one size"),
            ProductType("Coral Bead Set", "women", (45_000, 180_000), ONE_SIZE, "one size"),
            ProductType("Leather Tote", "women", (35_000, 90_000), ONE_SIZE, "one size"),
            ProductType("Woven Clutch", "women", (18_000, 45_000), ONE_SIZE, "one size"),
            ProductType("Brass Cuff", "unisex", (9_000, 28_000), ONE_SIZE, "one size"),
            ProductType("Hand Fan", "unisex", (8_000, 25_000), ONE_SIZE, "one size"),
        ],
    ),
    Category(
        slug="headwear",
        count=12,
        fabric="aso-oke and damask",
        occasions=["wedding", "owambe", "traditional"],
        colours=["Gold", "Silver", "Wine", "Emerald", "Fuchsia"],
        types=[
            ProductType("Gele", "women", (15_000, 60_000), ONE_SIZE, "one size"),
            ProductType("Fila (Abeti-Aja)", "men", (8_000, 25_000), ONE_SIZE, "one size"),
            ProductType("Turban", "women", (9_000, 22_000), ONE_SIZE, "one size"),
        ],
    ),
]


@dataclass
class SeedVariant:
    sku: str
    size: str
    colour: str
    price_kobo: int
    stock_on_hand: int


@dataclass
class SeedProduct:
    slug: str
    name: str
    category: str
    description: str
    base_price_kobo: int
    attributes: dict[str, Any]
    created_at: datetime
    variants: list[SeedVariant]
    image_url: str
    image_alt: str


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def round_naira(naira: float, step: int = 500) -> int:
    return int(round(naira / step) * step)


def _describe(name: str, cat: Category, ptype: ProductType, occasions: list[str]) -> str:
    return (
        f"{name} in {cat.fabric}. {ptype.fit.capitalize()} fit. "
        f"Made in Lagos. Good for {', '.join(occasions)}."
    )


def build_catalog() -> list[SeedProduct]:
    rng = random.Random(SEED)
    products: list[SeedProduct] = []
    used_slugs: set[str] = set()

    for cat in CATEGORIES:
        for i in range(cat.count):
            ptype = cat.types[i % len(cat.types)]
            print_name = rng.choice(PRINT_NAMES)
            name = f"{print_name} {ptype.name}"
            slug = slugify(f"{cat.slug} {name}")
            n = 2
            while slug in used_slugs:  # same print + type picked twice: make it unique
                slug = slugify(f"{cat.slug} {name} {n}")
                n += 1
            used_slugs.add(slug)

            lo, hi = ptype.price_naira
            base_naira = round_naira(rng.uniform(lo, hi))
            colours = rng.sample(cat.colours, k=rng.randint(1, min(3, len(cat.colours))))
            occasions = sorted(rng.sample(cat.occasions, k=min(2, len(cat.occasions))))

            # About 4% of products are fully sold out, about 10% of other variants are at 0.
            sold_out = rng.random() < 0.04
            variants: list[SeedVariant] = []
            for colour in colours:
                for size in ptype.sizes:
                    # XXL costs about 5% more on apparel; everything else is the base price.
                    price = round_naira(base_naira * 1.05) if size == "XXL" else base_naira
                    stock = 0 if sold_out or rng.random() < 0.10 else rng.randint(1, 25)
                    sku = (
                        f"FF-{len(products) + 1:03d}-{slugify(colour).upper()}-"
                        f"{slugify(size).upper()}"
                    )
                    variants.append(SeedVariant(sku, size, colour, price * 100, stock))

            attributes = {
                "fabric": cat.fabric,
                "pattern": print_name,
                "gender": ptype.gender,
                "occasion": occasions,
                "fit": ptype.fit,
                "type": ptype.name,
                "colours": colours,
                "made_in": "Lagos",
            }
            products.append(
                SeedProduct(
                    slug=slug,
                    name=name,
                    category=cat.slug,
                    description=_describe(name, cat, ptype, occasions),
                    base_price_kobo=min(v.price_kobo for v in variants),
                    attributes=attributes,
                    # Spread over the 180 days before a fixed date so "newest" is meaningful
                    # and identical on every run.
                    created_at=CATALOG_EPOCH - timedelta(minutes=rng.randint(0, 180 * 24 * 60)),
                    variants=variants,
                    image_url=f"/images/products/{slug}.webp",
                    image_alt=f"{name} in {colours[0]} (AI-generated image)",
                )
            )
    return products
