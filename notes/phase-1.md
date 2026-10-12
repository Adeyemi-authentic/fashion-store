# Phase 1: Data model, catalog seed, API skeleton

**Status:** built and tested locally (31 tests pass). Not yet run against Neon, because `DATABASE_URL` / `DATABASE_URL_DIRECT` in `.env` are still placeholders.

## What was built
- **API project** (`api/`): Python 3.12 via `uv`, FastAPI, async SQLAlchemy 2 + psycopg 3, Alembic, pydantic-settings, ruff, import-linter.
- **Full schema** as SQLAlchemy models, one file per module, and one Alembic migration (`0001`) creating all 16 tables from ARCHITECTURE.md §2, including:
  - CHECK constraints: stock can't go negative or over-reserved, order status must be valid, emails stored lowercase
  - a generated `search_tsv` column with a GIN index (ready for Phase 6)
  - a `vector(1024)` column for embeddings
  - an `UNLOGGED` `rate_limits` table
  - a partial index on active reservations
- **Catalog API:**
  - `GET /products` with filters (category, price in kobo, size, colour, in stock), 4 sort orders and cursor pagination
  - `GET /products/{slug}` with per-variant availability
  - `GET /categories` and `GET /health`
- **Seed:** 150 products, 1,212 variants, 7 categories (ankara, adire, aso-oke, agbada & kaftans, ready-to-wear, accessories, headwear).
  - Lagos prices from ₦10,500 to about ₦620,000, in ₦500 steps.
  - 7 products fully sold out and about 10% of variants at zero stock, so out-of-stock screens get tested.
  - Every product has structured `attributes` (fabric, pattern, gender, occasion, fit). Phase 6 builds on these.
- **Image prompts:** `scripts/generate_images.py` writes 150 prompts with one shared art direction. Nothing is generated yet (open decision below).

## Why, and the trade-offs
- **Cursor (keyset) pagination, not page numbers.** `WHERE (sort_value, id) > (last_value, last_id)` uses the index and never skips or repeats an item when products are added between page loads. With OFFSET, page 40 gets slower and items shift between pages. Cost: you can't jump straight to "page 7". That's fine for an infinite-scroll shop. The `id` tie-breaker matters, because many products share a price.
- **A re-seed never touches stock or `created_at`.** Upserts update names and prices only. Once real orders exist, a re-seed must not undo sales.
- **Deterministic seed** (a fixed random seed). The same slugs and SKUs come out every run, which is what makes upserting safe, and test numbers are stable.
- **Async SQLAlchemy with prepared statements off.** Neon's pooled URL runs through PgBouncer in transaction mode, where psycopg's automatic prepared statements can hit the wrong backend connection. Cost: a tiny re-parse per query.
- **The whole schema now, not table by table.** The ERD was already designed, and putting the CHECK constraints in from day one means no phase can quietly write bad stock values.
- **Tests run on a real Postgres** (via `pgserver`, no Docker on this machine), using the real migrations. A test also checks that the models and migrations agree (`alembic check`) and that downgrade → upgrade works. That's the "migrations check" CI will run in Phase 9.

## Decisions made while building
- **No `citext`** (case-insensitive text). The test Postgres doesn't include it, and lowercase + CHECK works on any Postgres. Emails are stored lowercased, with a CHECK constraint.
- **Paystack webhook dedupe key = `"<event>:<data.id>"`.** Paystack payloads have no event id field, so this became the `payment_events` primary key. To confirm against real payloads in Phase 4.
- `products.base_price_kobo` = the lowest variant price ("from ₦X"), so listing filters and sorts need no join. Admin (Phase 3) must keep it in sync.
- `orders.inventory.available_qty()` is the one definition of "available" (on hand − reserved). The catalog uses it through `orders.service`, and `lint-imports` enforces that. Phase 4 changes this one function so expired holds stop counting.
- Renamed one print name: "Makoko" is a Lagos informal settlement, not something to name a dress after.
- Postgres version: tests run on 16, Neon defaults to 17. Nothing here depends on the difference.

## Your to-do
1. **Fill in the Neon URLs** in `.env` (pooled → `DATABASE_URL`, direct → `DATABASE_URL_DIRECT`, scheme `postgresql+psycopg://`). Then I'll run `alembic upgrade head` and the seed against Neon and check the endpoints with real latency.
2. **Pick an image provider.** About $5–10 for 150 images. Options:
   - **OpenAI `gpt-image-1`:** strong at clothing detail.
   - **Google Gemini image:** good, cheap.
   - **Flux via Replicate:** most photographic, per-second billing.

   Whichever you pick, put its key in `IMAGE_GEN_API_KEY`, and I'll add the call and generate the images before Phase 2 needs them.

## Done when
- [x] pytest covers the catalog endpoints (pagination for every sort, filters, bad cursors, 404s, hidden drafts).
- [x] Seeding can be re-run with no duplicates, and doesn't overwrite stock (tested).
- [ ] Migrations + seed run against Neon (blocked on the URLs).

## 3 interview questions this phase lets me answer
1. **"Why cursor pagination over OFFSET, and what's the subtle bug people hit?"** OFFSET scans and throws away rows, and items shift when data changes. Keyset uses the index. The subtle bug is forgetting a unique tie-breaker (`id`): with duplicate prices, items get skipped or repeated at page boundaries.
2. **"How do you keep a seed script from wrecking production data?"** Deterministic keys, upsert on natural keys (slug, SKU), and an explicit list of columns the seed may update. Stock and timestamps are never in that list.
3. **"Where do you enforce data rules: in the app or the database?"** Both, and they do different jobs. The app gives good error messages. CHECK constraints are the last line of defence that holds even when the code has a bug. Example: `stock_reserved <= stock_on_hand`, and there's a test proving the database rejects the bad write.
