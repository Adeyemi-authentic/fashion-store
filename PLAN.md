# AI-Native Fashion Store: Build Plan

Concept build for a fictional Lagos fashion brand, **FlyFit**. The brand name lives in one config value so it can be swapped. Note: a US company (FLYFIT Holdings LLC) holds a FLYFIT trademark for athletic apparel, so the site, README and case study must clearly say "fictional brand, concept build", and nothing should copy that company's logo or styling.
Website case #2 of 3 in Section 2.5.A of the 5-month plan. It also covers Month 3 (deployment + MLOps-lite).

**Why this project exists:** to prove two things to an AI-engineer hiring manager:
1. I can design a backend with real system-design problems (concurrency, payments, async jobs, caching, observability) and explain the trade-offs.
2. I can make AI do real work in a product (hybrid search, recommendations, a tool-using shopping agent, a content pipeline) and measure it with numbers.

**Decisions locked (2026-10-12):** industry = fashion, payments = Paystack (test mode), scope = full version, brand = FlyFit, product images = AI-generated (labelled as such), working mode = Claude writes all phases including 4 and 5, user reviews.

**Working rule:** after each phase, stop for review and wait for "go". For Phases 4 and 5 (the interview stories), Claude writes the code in small reviewable commits, one concern per commit (e.g. idempotency, stock hold, webhook, late payment), each with a short explanation of what could go wrong without it. The phase notes for 4 and 5 add a walkthrough the user can practise explaining aloud, and the user should be able to answer the 3 interview questions without notes before saying "go". Each phase ends with a short `notes/phase-N.md` (what was built, why, the trade-offs, and 3 interview questions this phase lets me answer).

---

## 1. Architecture

```
Next.js storefront (Vercel)
        |  same-origin /api/* (Next.js rewrites -> Render)
        |  HTTPS (JSON)
FastAPI app: a single app split into modules (Render)
  catalog | cart | orders | payments | accounts | admin | search | assistant
        |                         |
  Neon Postgres + pgvector     Worker process
  (source of truth, job        (outbox jobs via
   queue, rate limits)          FOR UPDATE SKIP LOCKED)
        ^
  Paystack ---- webhook ----> /payments/webhook
```

**Why one app with modules instead of microservices:** one developer, one database, low traffic. Microservices would add network failures, distributed transactions and deployment overhead with no benefit at this scale. Module boundaries are enforced in code (each module owns its tables and exposes a service interface), so any module could be split out later. This decision gets written up as ADR-001.

**Why the browser only talks to the Next.js domain:** the storefront (Vercel) and API (Render) are on different domains, so a session cookie set by the API would be a third-party cookie, which Safari blocks and Chrome restricts. All browser calls go to `/api/*` on the storefront domain, and Next.js rewrites them to the API. Cookies are then first-party (`HttpOnly; Secure; SameSite=Lax`), and CORS is not needed. Paystack webhooks go straight to the API, since no cookie is involved. (Alternative if a custom domain is bought later: `flyfit.example` + `api.flyfit.example`.) Written up as ADR-005.

**Why a Postgres job queue instead of a Redis queue or Celery:** a job is written in the same transaction as the order (the outbox pattern), so an order can never exist without its follow-up jobs, and no extra infrastructure is needed.

**Why no Redis (ADR-006, decided 2026-10-12):** at this size Postgres does everything Redis would. Next.js page caching (ISR) and a small in-process cache handle the catalog, an `UNLOGGED` Postgres table handles rate limits, and the spend cap is a Postgres row. One fewer service and set of keys. Redis gets added only if the Phase 8 load test misses the latency target, and the measurement is the reason.

### Stack
| Layer | Choice |
|---|---|
| Storefront | Next.js (App Router) + TypeScript + Tailwind |
| API | FastAPI, Python, SQLAlchemy 2 + Alembic migrations, Pydantic |
| Database | Neon Postgres + pgvector (a new Neon project; never reuse another project's DB URL) |
| Cache / rate limit | Next.js ISR + in-process TTL cache; rate limits in an `UNLOGGED` Postgres table (no Redis, ADR-006) |
| Payments | Paystack test mode (amounts in kobo, webhook signature = HMAC-SHA512 in `x-paystack-signature`) |
| Embeddings | Voyage (already used in earlier weeks) |
| LLM | Claude API; claude-haiku-5-5 while building, model choice re-checked during evals |
| Email | Resend free tier, or logged to the console in dev |
| Tests | pytest (+ testcontainers or a Neon branch for DB tests), Playwright for key flows |
| Load / concurrency tests | k6 or Locust + a custom asyncio concurrency script |
| Observability | structured JSON logs + request IDs, Sentry free tier, a `/metrics` endpoint |
| CI/CD | GitHub Actions (lint, typecheck, tests, migrations check) -> deploy on Render + Vercel |

### Running cost target: about $0/month
Free tiers for Neon, Vercel, Render web service, Sentry and Paystack test mode. **Open decision (Phase 9):** a Render background worker costs about $7/month. The free alternative is running the worker loop inside the API process. **Leaning: free option.** It is safe because stock availability ignores expired holds on read (Phase 4), so nothing breaks while the free Render instance sleeps; jobs just run late. Known limitations to document: emails are delayed after idle periods, and the first webhook after a sleep hits a cold start (Paystack retries, so it is still delivered).

---

## 2. Core data model (first draft, finalised in Phase 0)

- `products` (id, slug, name, description, category, base_price_kobo, status, embedding vector, created_at)
- `variants` (id, product_id, sku, size, colour, price_kobo, stock_on_hand, stock_reserved). No `version` column: the conditional `UPDATE` in Phase 4 already prevents overselling, so optimistic locking would be redundant.
- `product_images`
- `customers` (id, email, password_hash, role: customer | admin)
- `carts` / `cart_items` (server-side cart, keyed by session cookie, merged into the account cart at login)
- `orders` (id, customer_id or guest_email, status, total_kobo, currency, paystack_reference UNIQUE, needs_review, review_reason)
- `idempotency_keys` (key UNIQUE, request_hash, status: in_progress | done, response_body, order_id, created_at): one row per checkout attempt
- `order_items` (price is copied in at purchase time, never read back from `products`)
- `reservations` (variant_id, order_id, qty, expires_at, released_at). `variants.stock_reserved` must always equal the sum of unreleased reservations; both change in the same transaction, and a test checks this.
- `payment_events` (paystack_event_id UNIQUE, type, payload, processed_at): stops a repeated webhook being processed twice
- `outbox_jobs` (id, type, payload, status, attempts, run_after, last_error): the job queue
- `assistant_sessions` / `assistant_messages` (for evals and debugging)

**Order state machine:**
```
pending_payment -> paid -> fulfilled
pending_payment -> cancelled          (customer abandons / admin cancels)
pending_payment -> expired            (hold timed out)
expired         -> paid               (late payment, stock re-reserved successfully)
expired         -> refund_pending -> refunded   (late payment, stock gone)
paid            -> refund_pending -> refunded
```
Moves between states go through one function only, which rejects any move not on this list.

---

## 3. Phases

### Phase 0: Setup and architecture doc (about 1 session)
- Folder layout `web/`, `api/`, `notes/`, `docs/`; git repo (commits as Adeyemi-authentic).
- `docs/ARCHITECTURE.md`: the diagram above, an entity-relationship diagram (ERD), and a sequence diagram for checkout -> Paystack -> webhook -> paid.
- ADRs: 001 one app with modules vs microservices, 002 Postgres outbox queue, 003 inventory reservation strategy (including late payments after a hold expires), 004 price stored in kobo as integers, 005 same-origin API via Next.js rewrites (first-party cookies).
- New Neon project, Paystack test account (needs your login), `.env.example`.
- **Done when:** you can explain the checkout sequence diagram end to end without notes.

### Phase 1: Data model, catalog seed, API skeleton (2 sessions)
- SQLAlchemy models + Alembic migrations; `GET /products` (filters, sort, pagination by cursor rather than page number), `GET /products/{slug}`.
- Catalog seed: about 150 products across 6-8 categories (ankara, adire, aso-oke, agbada/kaftans, modern ready-to-wear, accessories), with size/colour variants and realistic ₦ prices.
- Product images: AI-generated (decided). One shared style prompt (lighting, background, model framing) so the 150 products look like one brand; generated once by a script, stored in object storage/`web/public`, and labelled "AI-generated images" in the footer and README.
- **Done when:** pytest covers the catalog endpoints; seeding can be rerun safely without creating duplicates.

### Phase 2: Storefront (2-3 sessions)
- Home, category listing with filters, product page (variant picker, stock shown), cart drawer, mobile-first.
- Server-side cart API (`/cart`), using a session cookie, called through the `/api/*` rewrite (ADR-005). Verified in Safari as well as Chrome.
- Speed: Next.js caching (ISR) for product pages, image optimisation.
- **Done when:** Lighthouse scores 90+ on mobile and the full browse-to-cart flow works on a phone-width screen.

### Phase 3: Accounts and admin (2 sessions)
- Sign-up/login (argon2 password hashes, httpOnly session cookies), guest checkout kept, cart merged at login.
- Admin: product CRUD, stock adjustment, order list with status filter. Role checks enforced on the API, not just hidden in the UI.
- **Done when:** a customer gets a 403 on admin routes (tested), and the cart merge is tested.

### Phase 4: Checkout, Paystack, inventory (3 sessions) - the core system-design phase
- `POST /checkout` requires an `Idempotency-Key` header:
  - First request: insert the key with `status = in_progress` and a hash of the request body, create the order, store the response, set `status = done`, all in one transaction.
  - Same key, same body, already done: return the stored response.
  - Same key, same body, still in progress (two requests at once): the unique constraint makes the second one fail to insert; it returns `409 Retry-After: 1` so the client retries and then gets the stored response.
  - Same key, different body: `422`.
- Stock hold in one transaction: `UPDATE variants SET stock_reserved = stock_reserved + :q WHERE id = :id AND stock_on_hand - stock_reserved >= :q`. Zero rows updated means out of stock. The hold expires after 15 minutes.
- Stock availability never depends on the worker being awake: availability = `stock_on_hand - (reservations not released AND expires_at > now())`. The expiry job only tidies up (sets `released_at`, decrements `stock_reserved`, marks the order `expired`), and the reserve step releases any expired holds on that variant first, in the same transaction.
- Start a Paystack transaction -> redirect -> callback page that checks status (never trusts the redirect alone).
- `POST /payments/webhook`, all in one transaction:
  1. Check the HMAC-SHA512 signature over the raw body. Forged -> `401` (the only non-200 response).
  2. Store the event under its unique ID. A repeat -> `200` and do nothing.
  3. Ignore any event type other than `charge.success` (`200`).
  4. Check amount and currency (`NGN`) against the order. Mismatch -> keep the order unpaid, set `needs_review`, alert admin, return `200` (a 4xx would make Paystack retry forever).
  5. Order `pending_payment` -> `paid`: turn the hold into a real stock deduction, write outbox jobs.
  6. Order `expired` (late payment): try to re-reserve with the same conditional `UPDATE`. Success -> `paid`. Out of stock -> `refund_pending`, an outbox job calls the Paystack refund API, the customer gets an apology email, admin is alerted.
- **Done when:** these tests pass: the same webhook arriving twice gives one payment; a wrong amount or currency leaves the order unpaid and flagged (and returns 200); a forged signature gets a 401; an expired hold puts stock back even with the worker stopped; a late payment with stock left becomes `paid`, and one with no stock left becomes `refund_pending`; two concurrent checkouts with the same idempotency key create one order; same key with a different body gets 422; `stock_reserved` matches the reservations table after the concurrency test.

### Phase 5: Async jobs, caching, observability (2-3 sessions)
- Worker: claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, retries with exponential backoff, moves a job to `dead` after N attempts (a dead-letter queue), and is safe to run as 2+ copies at once.
- Jobs: order confirmation email, expiring stale holds (scheduled), refreshing embeddings when a product changes, a low-stock alert to admin, Paystack refunds for late payments.
- Jobs run at least once, so each side effect carries its own idempotency key: emails send the job ID as Resend's `Idempotency-Key`; refunds check for an existing refund on the transaction before creating one.
- Caching without Redis: Next.js ISR for product pages (revalidated when a product changes) + an in-process TTL cache for catalog API responses, cleared when a product changes. Per-IP + per-customer rate limits on checkout and the assistant, using fixed-window counters in an `UNLOGGED` Postgres table (old windows cleared by a worker job).
- Observability: request ID passed through logs and jobs, Sentry, `/metrics` (request latency, job queue depth, failed jobs).
- **Done when:** killing the worker mid-job loses no jobs and runs none twice where it matters (tested); a cache hit shows up in the logs; the 11th checkout attempt in a minute from one IP gets a 429 (tested).

### Phase 6: AI search, recommendations, description pipeline (2-3 sessions)
- Hybrid search: Postgres full-text + pgvector similarity, merged with reciprocal rank fusion. An LLM turns a messy query into filters ("wedding outfit under 50k" -> category, max price, occasion).
- Recommendations: "similar items" (nearest neighbours by embedding, excluding out-of-stock) + "frequently bought together" (counted from orders).
- Description pipeline: LLM drafts a product description from structured attributes -> automatic checks (no claims that aren't in the attributes, length, tone) -> admin approves. Runs as an outbox job.
- **Done when:** a labelled set of 50 search queries exists (built *before* tuning), and hybrid search is compared against keyword-only search.

### Phase 7: Shopping assistant agent (3 sessions)
- Chat widget on the storefront. Tools: `search_products`, `get_product`, `check_stock`, `add_to_cart`, `get_order_status` (only the logged-in customer's own orders), `get_policy` (a small RAG over shipping/returns/size-guide pages).
- Guardrails: every ₦ amount in a reply must appear in a tool result (a check validates the output); the agent cannot place an order or change a price; tool output is treated as data, never as instructions; rate limit + daily spend cap.
- Logs every conversation for evals.
- **Done when:** a manual run of the 10 hardest scenarios passes before the formal eval.

### Phase 8: Evals, load and concurrency tests (2 sessions)
Targets written now, before building (only change them with a written reason):

| What | Target |
|---|---|
| Overselling: 200 concurrent buyers for 10 units | exactly 10 paid orders, 0 oversold, stock never negative |
| The same webhook sent 5 times | 1 payment recorded |
| Search recall@10 (50 labelled queries) | >= 0.85, and hybrid beats keyword-only |
| Assistant task success (40 scenarios) | >= 90% |
| Assistant made-up prices | 0 |
| Assistant reading another customer's order | 0 |
| Prompt injection via product text / user message | 100% resisted |
| Catalog API p95 latency (cached) | < 200 ms at the tested request rate (rate recorded in the results). Measured without Redis first; Redis is added only if this target is missed (ADR-006) |
| Assistant cost and latency | reported per conversation (for example "2.1 s, $0.004") |

- Results in `evals/RESULTS.md` with the commands to re-run them.

### Phase 9: Deploy, CI/CD, packaging (2 sessions)
- Docker for the API, GitHub Actions (tests + migrations check on every PR), Render + Vercel, live Paystack test-mode checkout with test cards documented.
- README (architecture diagram, the eval table, an "interesting problems" section), a case-study page for the portfolio site, demo video, LinkedIn post draft.
- Label clearly as a concept build with a fictional brand, payments in test mode.
- **Done when:** a stranger can buy a product with a Paystack test card on the live URL, and every number in the README links to a reproducible eval.

**Estimate:** about 22-26 sessions, roughly 5-6 weeks at 16 hrs/week.

---

## 3a. Plan review log (2026-10-12): gaps found and how to handle them

Read this before starting Phase 0, 2, 4 and 5. Each item says what can go wrong, the fix, and where in the plan the fix lives.

| # | Problem | Fix | Where |
|---|---|---|---|
| 1 | Storefront (Vercel) and API (Render) are on different domains, so the session cookie is third-party. Safari blocks it and Chrome restricts it, which breaks the cart and login. | The browser calls only `/api/*` on the storefront domain; Next.js rewrites forward to the API. Cookies are first-party (`HttpOnly; Secure; SameSite=Lax`), no CORS. Webhooks go straight to the API. | Section 1, ADR-005 (Phase 0), Phase 2 (test in Safari) |
| 2 | Customer pays after the 15-minute hold expired and the stock was given back. Money is taken with no order to fulfil. | State machine allows `expired -> paid` and `expired -> refund_pending -> refunded`. Webhook re-reserves with the same conditional `UPDATE`: success -> paid; out of stock -> refund job + apology email + admin alert. | Section 2 state machine, ADR-003, Phase 4 webhook step 6 |
| 3 | Returning 4xx for a wrong-amount webhook makes Paystack retry forever. | Only a forged signature gets 401. Everything else gets 200: repeats ignored, non-`charge.success` ignored, wrong amount/currency -> order stays unpaid and is flagged `needs_review`. | Phase 4 webhook steps 1-4 |
| 4 | Idempotency holes: same key with a different body, two requests with the same key at once, emails/refunds repeated by at-least-once jobs. | `idempotency_keys` table (request hash + status). Done -> stored response; in progress -> 409 + Retry-After; different body -> 422. Each job side effect has its own idempotency key (Resend `Idempotency-Key` = job ID; refunds check for an existing refund first). | Section 2, Phase 4 checkout, Phase 5 jobs |
| 5 | Render free tier sleeps, so an in-process worker does not release expired holds and stock looks sold out. | Availability ignores holds whose `expires_at` has passed, at read time. The expiry job only tidies up. Correctness never depends on the worker being awake. | Phase 4 stock hold, Section 1 cost note |
| 6 | Smaller items. | Drop `variants.version` (the conditional UPDATE already prevents overselling). Test that `stock_reserved` = sum of unreleased reservations. Add `cancelled` state. Model `claude-haiku-5-5`. | Section 2, Phase 4 tests, Stack |
| 7 | Redis was an extra service with no measured need. | Dropped (ADR-006). Postgres + Next.js ISR + an in-process cache; Phase 8 decides if Redis is ever needed. | Section 1, Phase 5, Phase 8 |

**Brand note:** FlyFit is the chosen name, but a US company (FLYFIT Holdings LLC) holds a FLYFIT apparel trademark. Keep the name in one config value, label everything as a fictional concept build, and don't imitate their branding. Revisit before the public launch in Phase 9.

**Images note:** AI-generated, one shared style prompt for all 150 products, generated once by a script that can be rerun (`scripts/generate_images.py`), labelled as AI-generated on the site and in the README.

---

## 4. Out of scope (on purpose)
Real payments, shipping-provider integration, multiple currencies, multiple vendors (marketplace), mobile app, Kubernetes. Each one gets a line under "what I'd do next" in the README, which is also good interview material.

## 5. Open decisions
- Render worker: $7/month background worker vs worker inside the API process (Phase 9). Leaning: in-process (safe because of review item 5).
- FlyFit name: confirm before the public launch in Phase 9 (see the brand note in 3a).

