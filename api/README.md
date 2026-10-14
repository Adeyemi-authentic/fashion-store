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

## Background jobs (outbox worker, ADR-002)

The worker runs inside the API process by default (`WORKER_IN_API=true`, the free-tier plan). To run it separately:

```bash
uv run python -m app.worker_main      # SIGTERM / Ctrl-C finish the job in progress, then stop
```

Jobs: order confirmation, late-payment apology, admin alerts, low-stock alert, Paystack refunds, and two scheduled jobs (expire holds every minute, clean-up hourly). Each job runs **at least once**, and each side effect carries its own idempotency key (emails: `job-<id>`; refunds: check Paystack for an existing refund first). After 6 failed attempts a job is `dead` and an admin is alerted. With no `RESEND_API_KEY`, emails are written to `api/.emails/`.

Inspect the queue: `SELECT type, status, attempts, last_error FROM outbox_jobs ORDER BY id DESC LIMIT 20;` or `GET /metrics`.

## Observability

- **Logs:** JSON lines with `request_id` on every line (from the storefront proxy's `X-Request-ID`, echoed in every response). Jobs carry the id of the request that created them.
- **`GET /metrics`:** Prometheus. Request latency by route, jobs by outcome, queue depth by status, the oldest due job's age, catalog cache hits. Set `METRICS_TOKEN` in production.
- **Sentry:** set `SENTRY_DSN`. Dead jobs are logged as errors.

## Rate limits and caching

- Checkout, login and sign-up: 10/min per IP and per signed-in session (429 + `Retry-After`). The client IP comes from `X-Forwarded-For` only when `X-Proxy-Auth` matches `PROXY_SHARED_SECRET` (set the same value for the storefront).
- Product lists, product detail, categories and facets: in-process cache, 60 s, cleared by any admin write. Live stock is `GET /products/{slug}/stock` (never cached).

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
| GET | `/products/{slug}` | variants, images, attributes; cached up to 60 s (stock numbers included) |
| GET | `/products/{slug}/stock` | live `available` per variant, never cached |
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
| GET | `/metrics` | Prometheus metrics (`Bearer METRICS_TOKEN` when set) |
