# Deploying FlyFit

```
GitHub (main) ── CI (.github/workflows/ci.yml) ──> Render: API image (render.yaml), after CI passes
             └──────────────────────────────────> Vercel: storefront (web/), every push
Browser ──> Vercel /api/* ──rewrite──> Render ──> Neon (us-east-2)
Paystack webhook ─────────────────────> Render /payments/webhook
```

Everything runs on free tiers. Render and Neon are both in Ohio (us-east-2) and Vercel's functions are in Washington (iad1), so every hop after the shopper's own is a few milliseconds.

## What CI checks
On every push and pull request:
- **API:** ruff, mypy (`app/`), import-linter (module boundaries), pytest. The tests include the migrations check: models and migrations agree (`alembic check`), and downgrade → upgrade works.
- **Storefront:** eslint and the TypeScript check.
- **Image smoke test:** builds the same Docker image Render runs and starts it against a fresh Postgres + pgvector, so the image's own migrations run. Then it seeds the catalog, builds the storefront against that API (which prerenders every product page), and fetches a product page through the `/api` proxy.

The Playwright journeys are not in CI: two of them pay on Paystack's real test page. Run them locally (`web/README.md`) or against the live site with `E2E_BASE_URL`.

## First deploy (one time)
Order matters a little: Render's URL is needed by Vercel, and Vercel's URL by Render.

1. **GitHub.** Push the repo (`main`). CI runs on the first push.
2. **Shared secret.** Make one random value and keep it for both services: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
3. **Render.** Dashboard → New → Blueprint → pick the repo. It reads `render.yaml` and asks for each `sync: false` value:
   - `DATABASE_URL`, `DATABASE_URL_DIRECT`: the Neon pooled and direct URLs (the same ones as `.env`).
   - `PROXY_SHARED_SECRET`: the value from step 2.
   - `PUBLIC_SITE_URL`: the Vercel URL you will use, e.g. `https://flyfit-store.vercel.app` (fix it after step 4 if Vercel gives a different one).
   - `PAYSTACK_SECRET_KEY` (`sk_test_` only), `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `EMAIL_FROM`, `ADMIN_EMAIL`. `RESEND_API_KEY` and `SENTRY_DSN` may stay empty.

   The service URL will be `https://flyfit-api.onrender.com` (or similar). Check `https://<that>/health` returns `{"status":"ok"}`. The first start runs the migrations, which on Neon are already at head, so it changes nothing.
4. **Vercel.** Add New → Project → import the repo. **Root Directory: `web`** (the framework is detected). Environment variables (Production):

   | Key | Value |
   |---|---|
   | `API_BASE_URL` | the Render URL, no trailing slash |
   | `PROXY_SHARED_SECRET` | the value from step 2 |
   | `PUBLIC_SITE_URL` | this Vercel URL |
   | `BRAND_NAME` | `FlyFit` |
   | `APP_ENV` | `production` |

   The build fetches the catalog from Render to prerender product pages, so Render must be up (a sleeping instance wakes in under a minute; the build waits).

   Gotchas found on the first deploy (2026-10-18):
   - **Set the variables before the first build.** Without `API_BASE_URL` the build fetches the catalog from `localhost` and `npm run build` exits with 1. (`npm warn Unknown env config "cache-dir"` in the log is Vercel's own and harmless.)
   - **Use the production domain.** Vercel shows several URLs. The team-scoped one (`<project>-<team>.vercel.app`) sits behind Vercel Authentication and redirects strangers to a login page; the production alias (Project → Overview → Domains) is public. Put that one in `PUBLIC_SITE_URL` on **both** services, or Paystack returns payers to a login page and the assistant strips its own links.
   - **Paste API keys without quotes or spaces.** A bad `ANTHROPIC_API_KEY` doesn't fail loudly: the assistant answers every message with its fallback ("Sorry, I couldn't finish that just now…"). An empty key gives "assistant unavailable" instead.
5. **Paystack** (test mode). Settings → API Keys & Webhooks → Test Webhook URL: `https://<render url>/payments/webhook`. No callback URL is needed: each checkout sends its own (`PUBLIC_SITE_URL/checkout/callback`).
6. **Check:**
   - `curl <vercel url>/api/health` returns `{"status":"ok"}` through the proxy.
   - 40 parallel direct calls to `<render url>/search?q=x` should include 429s (calls that skip the storefront share one rate-limit key).
   - Ask the assistant a question and get a real answer, not the fallback.
   - Pay in test mode (below) and see the order turn `paid`. The confirmation email shows in Resend's dashboard; without `RESEND_API_KEY` it is only written to the container's disk, which you can't open on the free plan.

## Paying in test mode
Checkout sends the shopper to Paystack's test-mode page (no real money). There: **Pay with Card**, then pick **Success** (or **Declined**, to see the failure path), then **Pay NGN …**. This is the same flow the Playwright checkout journeys drive (`web/e2e/checkout.spec.ts`). Paystack's other test cards (PIN, OTP, bank transfer) are listed at https://paystack.com/docs/payments/test-payments/.

## Known limits of the free tier
- **Cold starts.** Render's free instance sleeps after about 15 idle minutes. The first visitor after that waits for it to wake (roughly 30–60 s on API-backed calls). Product pages are prerendered and served by Vercel, so browsing is not affected, but search, the bag and checkout are.
- **Late jobs.** The outbox worker runs inside the API, so while it sleeps, emails and refunds wait. Nothing becomes wrong: stock ignores expired holds when read (ADR-003).
- **First webhook after a sleep** can time out; Paystack retries, and a repeat is ignored (`payment_events` is unique).
- **Emails without Resend** are written to the container's disk, which is wiped on every deploy. Set `RESEND_API_KEY` to keep them.

## Every later deploy
Push to `main`. Vercel deploys straight away; Render deploys once CI passes. A migration runs when the new API container starts; if it fails, the container never passes `/health` and Render keeps the old one serving.
