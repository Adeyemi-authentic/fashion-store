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
uv run python -m scripts.make_admin EMAIL # the only way to grant the admin role
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
| POST | `/auth/signup`, `/auth/login`, `/auth/logout` | session cookie; the guest cart is merged at login |
| GET | `/auth/me` | 401 if not signed in |
| POST | `/checkout` | needs `Idempotency-Key` header; body `{email}` for guests. 201 `{order_id, reference, authorization_url, total_kobo, expires_at}`; 409 out of stock / still processing (`Retry-After`); 422 same key, different body; 502 Paystack unreachable (nothing held) |
| GET | `/orders/by-reference/{ref}` | for the callback page; verifies with Paystack if unsettled |
| GET | `/orders/mine` | signed-in customer's orders |
| POST | `/payments/webhook` | Paystack only, HMAC-SHA512 signed; 401 only for a bad signature, otherwise 200 |
| * | `/admin/...` | products, variants, stock deltas, orders, cancel; 401 visitors / 403 customers |
| GET | `/health` | checks the database |
