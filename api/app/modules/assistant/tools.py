"""The assistant's tools: what the model may ask the shop to do, and how each request runs.

Safety comes from what is missing here as much as from what is present:
- No tool places an order, starts a payment or changes a price. The worst a confused or
  manipulated model can do is search, read, and add an item to the shopper's own cart (which
  the shopper can see and undo).
- Who the shopper is comes from the request (their session cookie and cart cookie), never
  from the model. `get_order_status` has no customer parameter: it can only read the
  signed-in customer's orders, so another customer's order reference simply isn't found.
- Every argument is checked again here; the JSON schema (strict) is the first line, not the
  only one.
- Results are JSON data. Money is always written as "₦45,000" strings so the price check
  (guards.py) can find every amount the model was given.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant import policies
from app.modules.cart import service as cart
from app.modules.cart.schemas import CartOut
from app.modules.cart.service import CartOwner
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ProductDetail, VariantOut
from app.modules.catalog.service import SearchFilters, SearchVocabulary
from app.modules.orders import service as orders
from app.modules.search import service as search

MAX_SEARCH_RESULTS = 6
MAX_ADD_QTY = 5  # per call; the cart's own per-line limit still applies
DESCRIPTION_CHARS = 700


def naira(kobo: int) -> str:
    return f"₦{kobo // 100:,}"


class ToolError(Exception):
    """A problem the model should see and can fix (wrong slug, sold out): sent back as an
    error tool result, not raised to the shopper."""


@dataclass
class ToolContext:
    session: AsyncSession
    customer_id: uuid.UUID | None
    cart_owner: CartOwner
    # Set when add_to_cart made a guest's first cart: the router sends it as the cart cookie.
    new_cart_token: str | None = None
    cart_changed: bool = False


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _tool(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
    }


def definitions(vocab: SearchVocabulary) -> list[dict[str, Any]]:
    """The tool list. The enums come from the live catalog, so the model can only name
    categories and occasions that exist. Kept in a fixed order: tools are the start of the
    prompt, and any change there invalidates the prompt cache."""
    price = _nullable({"type": "integer"})
    return [
        _tool(
            "search_products",
            "Search the catalog. Returns up to 6 products with their starting price, stock "
            "and page link. Use the shopper's own words in `query`; set a filter only when "
            "the shopper clearly asked for it.",
            {
                "query": {"type": "string", "description": "What the shopper is looking for"},
                "category": _nullable({"type": "string", "enum": list(vocab.categories)}),
                "gender": _nullable({"type": "string", "enum": ["women", "men"]}),
                "occasion": _nullable({"type": "string", "enum": list(vocab.occasions)}),
                "min_price_naira": price,
                "max_price_naira": price,
            },
        ),
        _tool(
            "get_product",
            "Full details of one product: description, attributes, and every size/colour "
            "variant with its price, stock and variant_id.",
            {"slug": {"type": "string", "description": "The product slug from search results"}},
        ),
        _tool(
            "check_stock",
            "Live stock for a product, optionally only for one size and/or colour. Use it "
            "right before telling the shopper something is available.",
            {
                "slug": {"type": "string"},
                "size": _nullable({"type": "string"}),
                "colour": _nullable({"type": "string"}),
            },
        ),
        _tool(
            "add_to_cart",
            "Add a variant to the shopper's cart. Only when the shopper has asked for it and "
            "has chosen the size and colour. Returns the updated cart.",
            {
                "variant_id": {"type": "string", "description": "variant_id from get_product"},
                "qty": {"type": "integer", "description": f"1 to {MAX_ADD_QTY}"},
            },
        ),
        _tool(
            "get_order_status",
            "The signed-in shopper's own orders and their status (most recent first). Pass "
            "an order reference to get one order, or null for the latest few.",
            {"reference": _nullable({"type": "string"})},
        ),
        _tool(
            "get_policy",
            "Search the shop's shipping, returns and size-guide pages. Returns the most "
            "relevant sections with links. Use it for any question about delivery, returns, "
            "refunds, exchanges or sizing.",
            {"question": {"type": "string"}},
        ),
    ]


def _product_url(slug: str) -> str:
    return f"/p/{slug}"


async def search_products(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()[:200]
    if not query:
        raise ToolError("query is empty")
    vocab = await search.vocabulary(ctx.session)

    def pick(value: Any, allowed: tuple[str, ...]) -> str | None:
        return value if isinstance(value, str) and value in allowed else None

    def kobo(value: Any) -> int | None:
        return value * 100 if isinstance(value, int) and 0 < value < 100_000_000 else None

    filters = SearchFilters(
        category=pick(args.get("category"), vocab.categories),
        gender=pick(args.get("gender"), ("women", "men")),
        occasion=pick(args.get("occasion"), vocab.occasions),
        min_price_kobo=kobo(args.get("min_price_naira")),
        max_price_kobo=kobo(args.get("max_price_naira")),
    )
    result = await search.search(ctx.session, query, MAX_SEARCH_RESULTS, filters=filters)
    items = []
    for card in result.items:
        item = {
            "slug": card.slug,
            "name": card.name,
            "category": card.category,
            "price_from": naira(card.price_kobo),
            "in_stock": card.in_stock,
            "url": _product_url(card.slug),
        }
        items.append(item)
    out: dict[str, Any] = {"results": items}
    if result.relaxed:
        out["note"] = "Nothing matched every filter, so some filters were dropped."
    return out


async def _product(ctx: ToolContext, slug: Any) -> ProductDetail:
    if not isinstance(slug, str) or not slug:
        raise ToolError("slug is required")
    product = await catalog.get_product(ctx.session, slug)
    if product is None:
        raise ToolError(f"no product with slug {slug!r}; use a slug from search_products")
    return product


def _variant(v: VariantOut) -> dict[str, Any]:
    return {
        "variant_id": str(v.id),
        "size": v.size,
        "colour": v.colour,
        "price": naira(v.price_kobo),
        "available": v.available,
    }


async def get_product(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    p = await _product(ctx, args.get("slug"))
    attrs = {k: v for k, v in p.attributes.items() if isinstance(v, str | int | list)}
    return {
        "slug": p.slug,
        "name": p.name,
        "category": p.category,
        "price_from": naira(p.price_kobo),
        "description": p.description[:DESCRIPTION_CHARS],
        "attributes": attrs,
        "variants": [_variant(v) for v in p.variants],
        "url": _product_url(p.slug),
    }


async def check_stock(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    p = await _product(ctx, args.get("slug"))
    size, colour = args.get("size"), args.get("colour")
    variants = [
        v
        for v in p.variants
        if (not size or v.size.lower() == str(size).lower())
        and (not colour or v.colour.lower() == str(colour).lower())
    ]
    out: dict[str, Any] = {
        "slug": p.slug,
        "name": p.name,
        "variants": [_variant(v) for v in variants],
    }
    if not variants:
        out["note"] = "No variant has that size/colour. Sizes: {}. Colours: {}.".format(
            ", ".join(sorted({v.size for v in p.variants})),
            ", ".join(sorted({v.colour for v in p.variants})),
        )
    return out


def _cart_summary(c: CartOut) -> dict[str, Any]:
    return {
        "items": [
            {
                "name": line.product_name,
                "size": line.size,
                "colour": line.colour,
                "qty": line.qty,
                "unit_price": naira(line.unit_price_kobo),
                "line_total": naira(line.line_total_kobo),
            }
            for line in c.items
        ],
        "item_count": c.item_count,
        "subtotal": naira(c.subtotal_kobo),
        "checkout_url": "/checkout",
    }


async def add_to_cart(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        variant_id = uuid.UUID(str(args.get("variant_id")))
    except ValueError as exc:
        raise ToolError("variant_id must be a variant_id from get_product") from exc
    qty = args.get("qty")
    if not isinstance(qty, int) or not 1 <= qty <= MAX_ADD_QTY:
        raise ToolError(f"qty must be between 1 and {MAX_ADD_QTY}")
    try:
        result, token = await cart.add_item(ctx.session, ctx.cart_owner, variant_id, qty)
    except cart.VariantNotFound as exc:
        raise ToolError("no such variant; use a variant_id from get_product") from exc
    except cart.NotEnoughStock as exc:
        raise ToolError(
            f"not enough stock: {exc.available} available, {exc.in_cart} already in the cart"
        ) from exc
    if token:  # a guest's first cart: later calls in this turn must use it too
        ctx.new_cart_token = token
        ctx.cart_owner = CartOwner(token=token)
    ctx.cart_changed = True
    return {"added": True, "cart": _cart_summary(result)}


async def get_order_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.customer_id is None:
        return {
            "signed_in": False,
            "message": "The shopper is not signed in. Order status is only available to "
            "signed-in customers (Account page), or through the link in their order email.",
        }
    receipts = await orders.customer_orders(ctx.session, ctx.customer_id, limit=20)
    ref = args.get("reference")
    if isinstance(ref, str) and ref.strip():
        receipts = [r for r in receipts if r.reference.lower() == ref.strip().lower()]
        if not receipts:
            # Same answer whether the reference is someone else's or doesn't exist.
            return {"found": False, "message": "No order with that reference on this account."}
    return {
        "orders": [
            {
                "reference": r.reference,
                "status": r.status,
                "placed_at": r.created_at.date().isoformat(),
                "total": naira(r.total_kobo),
                "items": [
                    {"name": i.name, "qty": i.qty, "unit_price": naira(i.unit_price_kobo)}
                    for i in r.items
                ],
            }
            for r in receipts[:5]
        ],
        "statuses": {
            "pending_payment": "waiting for payment",
            "under_review": "payment received but being checked by the shop",
            "paid": "paid, being prepared",
            "fulfilled": "sent out for delivery",
            "expired": "not paid in time; nothing was charged",
            "cancelled": "cancelled",
            "refund_pending": "refund on its way",
            "refunded": "refunded",
        },
    }


async def get_policy(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()[:300]
    if not question:
        raise ToolError("question is empty")
    found = await policies.search(question)
    return {
        "sections": [
            {"page": c.page_title, "section": c.heading, "text": c.text, "url": c.url}
            for c in found
        ]
    }


HANDLERS = {
    "search_products": search_products,
    "get_product": get_product,
    "check_stock": check_stock,
    "add_to_cart": add_to_cart,
    "get_order_status": get_order_status,
    "get_policy": get_policy,
}


async def run(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON text, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        return json.dumps(await handler(ctx, args), ensure_ascii=False), False
    except ToolError as exc:
        return json.dumps({"error": str(exc)}), True
