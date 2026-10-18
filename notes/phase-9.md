# Phase 9: Deploy, CI/CD, packaging

## Headline
- **Live:** [fashion-store-swart-theta.vercel.app](https://fashion-store-swart-theta.vercel.app) (Vercel, iad1) → [flyfit-api.onrender.com](https://flyfit-api.onrender.com/health) (Render free, Ohio) → Neon (us-east-2).
- **"Done when" met:** a test-card checkout on the live URL ended with the order `paid` (2026-10-18). Every number in the README links to a reproducible eval.
- **Deployed numbers:** the catalog API gives ~250 ms p50 from Lagos up to 20 req/s and is CPU-bound by 50 req/s on the free plan. The assistant takes 2.7–7.2 s per message live, against 13–20 s when run locally against Neon.

## What was built (one concern per commit)
1. `d1be6b1`: `.env.example` uses the variable name the API actually reads (`ASSISTANT_DAILY_BUDGET_USD`).
2. `416a04e`: **API Docker image.** uv, production dependencies only, non-root user. Migrations run on start; if one fails, the container never passes `/health` and Render keeps the old one.
3. `808325f`: **CI** on every push and PR.
   - API: ruff, mypy, import-linter, pytest, including the migrations check.
   - Storefront: lint and typecheck.
   - Image smoke test: build the real image, migrate a fresh Postgres, seed, build the storefront against it, fetch a product page through the proxy.
4. `4ddba92`: **Deploy config.** `render.yaml` (Blueprint; deploys only after CI passes), `web/vercel.json` (iad1, next to the API), `docs/DEPLOY.md`.
5. `fb939ae`, `af029dd`: **First CI fixes.** One formatting line, and tests no longer read the developer's `.env` (CI has none).
6. `e94510c`: **Catalog latency on Render** (see below).
7. `1e884af`: **Tests pin `PUBLIC_SITE_URL` to http.** An https URL in `.env` made cookies `Secure`, and the http test client dropped them.
8. `f075717`: **Rate limits for direct calls** (see below).
9. **Packaging:** the README (live link, architecture, results table, interesting problems), [`docs/CASE_STUDY.md`](../docs/CASE_STUDY.md), [`docs/launch/DEMO_VIDEO.md`](../docs/launch/DEMO_VIDEO.md), [`docs/launch/LINKEDIN_POST.md`](../docs/launch/LINKEDIN_POST.md), and a trademark recheck (PLAN 3a).

## Walkthrough to practise aloud: "a push to main, to a paying customer"
1. **Push to `main`.** GitHub Actions runs three jobs: API checks and tests, storefront checks, and the image smoke test.
2. **Vercel deploys straight away.** The build fetches the catalog from Render and prerenders all 131 product pages, so browsing never waits for the API.
3. **Render deploys only after CI passes** (`autoDeployTrigger: checksPass`). The new container runs `alembic upgrade head`, then must pass `/health` before it takes traffic. A failed migration means the old version keeps serving.
4. **A shopper loads a product page.** Vercel serves it pre-built. Live stock comes from `/api/products/{slug}/stock`.
5. **They tap Pay.** `/api/checkout` goes to Vercel's proxy, which adds `X-Proxy-Auth` and the shopper's IP, and forwards to Render. Render checks the per-shopper rate limit, holds the stock and calls Paystack.
6. **Paystack calls Render directly** with the signed webhook, and the order becomes `paid`. The callback page on Vercel shows what the API says, never what the redirect claims.

## Why, and the trade-offs
- **Vercel and Render in the same region as Neon.** Every hop after the shopper's own takes milliseconds. Measured: the assistant went from 13–20 s per message (Lagos ↔ Ohio for ~25 database round trips) to about 3 s.
- **Deploy after CI, not on every push.** Render waits for green checks; Vercel doesn't. A broken storefront build simply fails, and the old version keeps serving.
- **Migrations on container start, not as a separate step.** On the free plan there's no pre-deploy hook. The cost: a slow migration delays startup. That's fine at this size, because every migration so far is additive.
- **Still no Redis (ADR-006).** The free plan saturates between 20 and 50 catalog req/s because it gets a fraction of a CPU. The reads already come from memory, so Redis would add a hop to the same work. The fix for this limit is a bigger instance.
- **Direct calls share one rate-limit key.** Render's load balancer connects from a new address almost every time, so keying unproxied calls by socket address gave each one a fresh limit (40 direct searches in a second, none limited). Shoppers come through Vercel, which proves itself with the shared secret and keeps per-IP limits. The cost: scripts calling Render directly share one budget. That's the intent.
- **The `<200 ms` target wasn't re-measured from inside the US.** From Lagos, the round trip alone is close to 200 ms. The deployed run shows where the limit is (CPU), which is what the Redis decision needed.

## Bugs and surprises found by deploying
- **Vercel Authentication on the team URL.** Every page redirected strangers to a Vercel login. The public URL is the production alias.
- **A mis-pasted `ANTHROPIC_API_KEY`** made the assistant answer everything with its fallback. That was the designed behaviour (no 500s), but it was silent. `docs/DEPLOY.md` now lists it as a check.
- **The rate-limit hole** described above.
- **Tests read `.env`** in two ways (the database, then the site URL). Both are now pinned in `conftest.py`.

## Still open
- **The rate-limit fix isn't on Render yet.** It's pushed and CI passed, but Render hadn't deployed it after more than 10 minutes. Check Render → Events, then re-run the 40-call burst from `docs/DEPLOY.md` step 6.
- The demo video needs recording (script ready) and the LinkedIn post needs publishing (draft ready).
- Paystack business name: rename to the brand in the Paystack dashboard.

## 3 interview questions this phase lets me answer
1. *"Walk me through your deploy pipeline. What stops a bad deploy?"* CI runs lint, types, module boundaries, 299 tests and a migrations check, then builds the real Docker image and runs it against a fresh Postgres. Render deploys only after that. The container must pass `/health` after migrating before it takes traffic, so a failed migration leaves the old version serving.
2. *"Your local load test said 5 ms. What did production say?"* About 250 ms from Lagos, mostly the network, and saturation between 20 and 50 req/s, because the free plan has a fraction of a CPU. I didn't add Redis: the data was already in memory, so the limit is CPU. Local numbers measure the code's own cost; deployed numbers measure the platform.
3. *"What broke when you deployed that didn't break locally?"* The rate limits. Behind Render's load balancer, each direct request came from a new address, so per-IP limits never fired for calls that skipped the storefront. Locally the socket address was always 127.0.0.1, so the tests passed. I found it by attacking the live API, fixed it with one shared key for unproxied calls, and wrote the check into the deploy guide.
