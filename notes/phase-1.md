# Phase 1: Data model, catalog seed, API skeleton

**Status:** done. 33 tests pass locally. The schema is migrated and the catalog seeded on Neon (Postgres 18, us-east-2): 131 active and 19 draft products, 1,212 variants, 131 images.

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
- **Images:** 131 AI-generated product photos (OpenAI `gpt-image-2`, medium quality, about $0.04 each) in `web/public/images/products/`, made by `scripts/generate_images.py` with one shared art direction. Flat-lay items (jewellery, bags, fans, scarves) get specific Nigerian craft detail in their prompts, because the first sample came out generic. The run stopped at 131 when the OpenAI balance ran out, and we decided to keep 131.
- **Products without an image are seeded as `draft`** (hidden, no image row): 131 active, 19 draft. All 12 headwear products are drafts, so the storefront shows 6 categories. Generating a missing image and re-seeding makes that product live. An `archived` product is never un-archived by a re-seed.

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

## Neon findings (2026-10-13)
- **Row-by-row seeding took 6.5 minutes** (about 1,500 statements × about 250 ms Lagos→Ohio round trip), and a second run hit a network timeout partway. It rolled back cleanly, because the seed commits once at the end. **Fixed by batching:** 3 multi-row upserts, about 8 s.
- **Local API → Neon latency:** `/health` ≈ 0.7 s, `/products` ≈ 1.25 s, product detail ≈ 1.25 s. Almost all of that is the round trip from here to Ohio, which every query pays (pool pre-ping, query, image load, rollback ≈ 4–5 round trips). With the API on Render in the same region as Neon (Ohio), each round trip is about 1–2 ms. **Phase 9: deploy the API to Render's Ohio region.** Phase 5/8: consider dropping `pool_pre_ping` (it costs one round trip per request) in favour of `pool_recycle`, and measure the effect.
- The config now accepts the plain `postgresql://` URLs Neon and Render hand out, and converts them to psycopg 3.
- Neon's sample table `playing_with_neon` was left untouched.

## Your to-do (done)
1. **Fill in the Neon URLs** in `.env` (pooled → `DATABASE_URL`, direct → `DATABASE_URL_DIRECT`, scheme `postgresql+psycopg://`). Then I'll run `alembic upgrade head` and the seed against Neon and check the endpoints with real latency.
2. ~~Pick an image provider~~ Done: OpenAI, 131 images kept.

## Done when
- [x] pytest covers the catalog endpoints (pagination for every sort, filters, bad cursors, 404s, hidden drafts).
- [x] Seeding can be re-run with no duplicates, and doesn't overwrite stock (tested).
- [x] Migrations + seed run against Neon. Re-seeding twice gave identical counts and no stock rows over-reserved.

## 3 interview questions this phase lets me answer
1. **"Why cursor pagination over OFFSET, and what's the subtle bug people hit?"** OFFSET scans and throws away rows, and items shift when data changes. Keyset uses the index. The subtle bug is forgetting a unique tie-breaker (`id`): with duplicate prices, items get skipped or repeated at page boundaries.
2. **"How do you keep a seed script from wrecking production data?"** Deterministic keys, upsert on natural keys (slug, SKU), and an explicit list of columns the seed may update. Stock and timestamps are never in that list.
3. **"Where do you enforce data rules: in the app or the database?"** Both, and they do different jobs. The app gives good error messages. CHECK constraints are the last line of defence that holds even when the code has a bug. Example: `stock_reserved <= stock_on_hand`, and there's a test proving the database rejects the bad write.
