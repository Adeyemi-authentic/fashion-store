# FlyFit: eval, load and concurrency results

The targets were written into PLAN.md (Phase 8) before any of this was built. Each row links to the detail and gives the command that reproduces it. Every run uses a throwaway local Postgres (pgserver), never the Neon database. Last updated 2026-10-15.

| What | Target | Result | Met? |
|---|---|---|---|
| Overselling: 200 concurrent buyers for 10 units | exactly 10 paid orders, 0 oversold, stock never negative | **10 paid, 0 oversold**, lowest free stock seen 0 (93 samples during the burst); 190 others got a 409, none a 500 | ✅ |
| The same webhook sent 5 times | 1 payment recorded | **1** payment event; the 5 repeats all answered `duplicate` | ✅ |
| Search recall@10 (50 labelled queries) | ≥ 0.85, and hybrid beats keyword-only | **0.919** vs keyword-only 0.307 (all words) / 0.701 (any word) | ✅ on the development set |
| Search recall@10, held-out (8 scored queries written by the user) | reported separately, same ≥ 0.85 bar | **0.762** vs keyword-only 0.125 / 0.292; vector alone 0.688 | ❌ below the bar, see below |
| Assistant task success (40 scenarios) | ≥ 90% | **98.3%** over 3 runs (97.5%, 97.5%, 100%) | ✅ |
| Assistant made-up prices | 0 | **0** shown, audited against the database. The assistant's price check stopped 4 attempts, all the planted "₦500" from an injection scenario | ✅ |
| Assistant reading another customer's order | 0 | **0**, checked in tool results and in replies | ✅ |
| Prompt injection via product text / user message | 100% resisted | **18/18** (6 scenarios × 3 runs) | ✅ |
| Catalog API p95 latency (cached) | < 200 ms at the tested rate, without Redis | **p95 1.5–5.4 ms from 50 to 800 req/s** on 1 API worker (local); saturates at 1,600 req/s. **On Render's free plan: ~250 ms p50 from Lagos (mostly the network) up to 20 req/s; saturates by 50 req/s** | ✅ locally; on Render the limit is the free plan's CPU, not the cache (see below) |
| Assistant cost and latency | reported per conversation | **5.4 s and $0.0005** per conversation (p95 11.1 s); 4.8 s per shopper message | reported |

## Overselling and duplicate webhooks
`cd api && uv run python -m scripts.load.checkout_race` → [`load/checkout_race.json`](load/checkout_race.json)

- **Setup:** the real API under uvicorn with **2 worker processes**, a local fake Paystack server (the API's Paystack client is pointed at it with `PAYSTACK_BASE_URL`), and 200 buyers, each with their own cookies and a bag holding 1 of the same variant.
- **The burst:** all 200 press "pay" at one barrier. The burst takes 0.54 s; checkout p50 is 0.35 s and p95 0.51 s.
  - 10 orders are created.
  - 28 buyers reached the stock hold and lost the race there (`out_of_stock`).
  - 162 saw zero stock at the bag check first (`bag_needs_attention`).
- **Payment:** the 10 winners are paid by signed webhooks. One webhook is then replayed 5 times at once, and a forged one gets a 401.
- **Can the test fail? Checked by breaking the code on purpose:**
  - With the conditional `UPDATE` removed, the database `CHECK (stock_reserved <= stock_on_hand)` still refused to oversell, but 27 buyers got a 500. That's why "no server errors" is one of the checks.
  - With that constraint dropped as well, 37 holds were created for 10 units, and `CHECK (stock_on_hand >= 0)` refused the extra payments. So there are three layers, and the test sees the first one fail.

## Catalog latency
`cd api && uv run python -m scripts.load.catalog_latency` → [`load/catalog_latency.json`](load/catalog_latency.json)

The request mix is 45% product pages, 40% listings, 10% facets and 5% categories, all cached after a warm-up. It's open loop: requests go out on a fixed schedule, and latency is timed from when each request was due.

| Rate | p50 | p95 | p99 | Errors |
|---|---|---|---|---|
| 50 req/s | 4.1 ms | 5.4 ms | 6.1 ms | 0 |
| 100 | 3.2 | 4.6 | 5.5 | 0 |
| 200 | 2.6 | 3.8 | 7.5 | 0 |
| 400 | 1.9 | 2.5 | 9.2 | 0 |
| 800 | 0.9 | 1.5 | 11.3 | 0 |
| 1,600 | 362 | 7,216 | 10,687 | 0 (saturated) |

Lower latency at higher rates is real, not an error. At low rates macOS wakes idle processes a few ms late (timer coalescing), and the strict "from when it was due" number includes that. The "from send" figures in the JSON are 0.2–0.8 ms lower.

### Deployed (Render free plan, 2026-10-18)
`cd api && uv run python -m scripts.load.catalog_latency --target https://flyfit-api.onrender.com --rates 10 20 50` → [`load/catalog_latency_remote.json`](load/catalog_latency_remote.json)

Run from Lagos on a phone hotspot against the API in Ohio. A bare `/health` takes 0.55–0.79 s from here (TCP connect alone 0.13–0.28 s), so most of each number below is the network, not the API.

| Rate | p50 | p95 | p99 | Errors |
|---|---|---|---|---|
| 10 req/s | 249 ms | 281 ms | 573 ms | 0 |
| 20 | 250 | 430 | 560 | 0 |
| 50 | 3,635 | 9,213 | 12,819 | 0 (saturated) |

The generator kept its schedule (late p95 ≈ 1 ms), so the queueing at 50 req/s is the server. Render's free instance gets a fraction of a CPU; the same code saturates at 1,600 req/s on a full laptop core, which puts the free plan at well under a tenth of that. **No Redis (ADR-006 stands):** the cached reads already come from memory inside the API, so Redis would add a network hop to the same work. The ceiling is CPU, and the fix is a paid instance (or more of them), not a cache. In practice shoppers rarely hit the catalog API at all: product, category and home pages are prerendered and served by Vercel, and only search, the bag, checkout and the assistant call Render. The < 200 ms target is not testable from Lagos (the round trip alone is close to it); it would need a run from inside us-east.

**Caveat:** this is one uvicorn worker on an M-series Mac, with the load generator on the same machine. Render's free plan has a fraction of one CPU, so the deployed numbers will be lower. Phase 9 re-runs this with `--target <api url>`. Redis gets added only if that run misses the target (ADR-006).

**The first version of this test measured the wrong thing.** One Python process with a 1,000-connection pool fell behind its own schedule at about 150 req/s. ApacheBench on the same endpoint showed the API doing about 3,800 req/s. Four generator processes with small pools fixed it, and each one now reports its CPU use, so a saturated generator is visible in the results.

## Search
`cd api && uv run python -m scripts.eval_search` → [`search/RESULTS.md`](search/RESULTS.md) and [`search/README.md`](search/README.md)

The numbers are unchanged from Phase 6, re-run on 2026-10-15 after the embedding-failure fallback was added.

**Held-out set:** `cd api && uv run python -m scripts.eval_search --set heldout` → [`search/HELDOUT_RESULTS.md`](search/HELDOUT_RESULTS.md). 10 queries written by the user without seeing the prompt ([`search/heldout.txt`](search/heldout.txt)); 2 have no right product in the catalog, so 8 are scored. The storefront's search gets **0.762**, below the development set's 0.919. Nothing was tuned on these queries; why each one missed is in [`search/README.md`](search/README.md).

## Assistant
`cd api && uv run python -m scripts.eval_assistant --runs 3` → [`assistant/RESULTS.md`](assistant/RESULTS.md), with every transcript, and [`assistant/README.md`](assistant/README.md) for the scoring rules and check-fix log.

- **Model:** `claude-haiku-5-5` at medium effort.
- **The 2 failures out of 120 conversations:**
  - Scenario 12, run 1: a correct reply ("no linen shirt under ₦30,000", then the cheapest one with its price) failed a check that didn't match its own spec. The check has since been fixed and the fix is logged.
  - Scenario 22, run 2: the gele's length was answered without looking at the size guide. A real miss.
- **Model re-check (PLAN: Haiku while building, re-checked here):** Sonnet 5.5 passed all 25 scenarios it ran, at 4.8 s per message, the same as Haiku. The run stopped there because the API account ran out of credit. On this evidence, a model about 20× the price buys no measurable improvement on these tasks, so Haiku stays.
