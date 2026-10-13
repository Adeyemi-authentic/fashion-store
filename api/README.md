# api

FastAPI app split into modules (catalog, cart, orders, payments, accounts, admin, search, assistant) plus, later, the outbox worker. Python 3.12, managed with [`uv`](https://docs.astral.sh/uv/).

```bash
cd api
uv sync                                   # install
uv run alembic upgrade head               # create/upgrade tables (uses DATABASE_URL_DIRECT from ../.env)
uv run python -m scripts.seed_catalog     # seed ~150 products; safe to re-run
uv run uvicorn app.main:app --reload --timeout-keep-alive 75   # http://localhost:8000/docs

uv run pytest                             # tests (starts a throwaway local Postgres, no Docker)
uv run ruff check . && uv run ruff format --check .
uv run lint-imports                       # module boundaries (ADR-001)
```

Run uvicorn with `--timeout-keep-alive 75` (here and in production). Its default is 5 s. The
Next.js `/api` proxy reuses idle connections, and if uvicorn closes one just as the proxy sends on
it, the browser gets a 500 "socket hang up" (ECONNRESET). The server's keep-alive must outlast the
proxy's.

## Layout

```
app/
  main.py, config.py, db.py
  shared/            base model, cursor pagination
  platform/          outbox_jobs, rate_limits (shared infrastructure)
  modules/<name>/    models.py, service.py (public interface), router.py, schemas.py
migrations/          Alembic; 0001 = full schema from docs/ARCHITECTURE.md
scripts/             catalog seed, image prompt builder
tests/
```

## Endpoints so far

| Method | Path | Notes |
|---|---|---|
| GET | `/products` | filters: `category`, `min_price_kobo`, `max_price_kobo`, `size`, `colour`, `in_stock`; `sort`: `newest`, `price_asc`, `price_desc`, `name`; `limit` (1–60); `cursor` |
| GET | `/products/{slug}` | variants with live `available` count, images, attributes |
| GET | `/categories` | slug + product count |
| GET | `/facets` | sizes, colours and price range on sale; optional `category` |
| GET | `/cart` | the cart for the `cart_token` cookie (empty if none; never creates one) |
| POST | `/cart/items` | `{variant_id, qty}`; adds to the line, creates the cart + cookie if needed; 409 if more than available |
| PATCH | `/cart/items/{variant_id}` | `{qty}`; 0 removes; raising above available is 409, lowering always works |
| DELETE | `/cart/items/{variant_id}` | remove a line |
| GET | `/health` | checks the database |
