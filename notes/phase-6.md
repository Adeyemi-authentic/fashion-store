# Phase 6: AI search, recommendations, description pipeline

**Status:** built, tested, checked live, and reviewed by the user (2026-10-14). The PLAN "done when" is met: a labelled set of 50 search queries was committed **before any search code** (`90f812a`), and hybrid search is compared against keyword-only search in `evals/search/RESULTS.md`.

## Headline numbers
| What | Result |
|---|---|
| Search recall@10, storefront setting (hybrid + LLM filters, LLM skipped for short queries) | **0.919** |
| Keyword-only: all words / any word | 0.307 / 0.701 |
| Vector-only / hybrid without the LLM | 0.902 / 0.899 |
| Description drafts passing every check (all 131 products) | 63% first time, 98% within 2 attempts |
| Planted false claims caught (9 kinds × 20 drafts) | **180/180**: literal ones by the rules, every paraphrase by Claude |
| Cost | ~$0.00005 per LLM search, ~$0.001 per description |

## What was built (one concern per commit)
| Commit | Concern | What goes wrong without it |
|---|---|---|
| `90f812a` | 50 labelled queries, written before the search | The queries get shaped to fit the results, and the eval measures nothing. |
| `d809518` | Embeddings: Voyage, refresh job, backfill | Edited products are searched by their old text. |
| `509584e` | Hybrid search + LLM filters + eval harness | "wedding outfit under 50k" either matches nothing (keyword) or ignores the price (vector). |
| `829d996` | Tuning: product types in the prompt, skip the LLM for short queries | "scarf" gets filtered to accessories (scarves are in adire), so every right answer is hidden. Every search pays ~1.5 s. |
| `1bd53c7` | Recommendations | No "similar items", and no "bought together" either, or a made-up one. |
| `be2a9e8` | Description pipeline | A description invents a silk lining or "machine washable". Or a re-seed wipes approved text. |
| `0d1b0c1` | Fewer round trips | The embedding backfill took 10+ minutes from Lagos; it now takes 27 s. |
| `ec2363d` | Reviewer FK `SET NULL` | Deleting an admin account fails if they ever reviewed a draft (found in the live run). |
| `15a8dc1` | Storefront + admin UI | — |

**Tests:** 266 API tests (50 new) and 8 browser journeys (2 new: search, similar items). ruff, mypy and import-linter (4 contracts; a new one keeps `search` out of other modules' tables) are clean. Live check on Neon: migrations 0004–0006 applied, all 150 products embedded with `voyage-4`, one real draft passed all nine checks first time and was then rejected (so the live description is unchanged), and stock still matches the seed apart from your real order.

## Walkthrough to practise aloud: "wedding outfit under 50k"
1. **The browser** calls `/api/search?q=…` itself, not the page server. So the API's per-IP rate limit (30/min) sees the shopper's IP through the proxy, not our server's.
2. **The gate:** 3+ words or a digit, so the LLM runs. Two network calls go off **in parallel**:
   - Claude Haiku turns the query into filters.
   - Voyage embeds the raw query.
3. **Claude's answer is constrained** by a JSON schema whose enums are the store's real categories, occasions and colours, so it can't invent a value. The prompt also lists each category's product types, so it knows a scarf is adire.
   - The query goes in `<query>` tags as data. The only thing the model can return is that JSON.
   - Each value is checked again before SQL, and contradictory prices are dropped.
   - Answer: `occasion=wedding, max ₦50,000`, search text "wedding outfit".
4. **Two candidate lists, filters inside both:**
   - Full-text (`websearch_to_tsquery`, all words must match).
   - pgvector cosine distance: an exact scan, because at ~150 rows that takes about a millisecond.
   - Top 50 from each.
5. **Reciprocal rank fusion:** score = Σ 1/(60 + rank). The two lists score on different scales, so only ranks are combined. A product high in both lists wins.
6. **Nothing left after filtering?** Drop everything except the price and try again, then drop the price too. The page then says "Nothing matched every part of your search".
7. **Failure:** no key, a 5 s timeout, an API error or a refusal all mean "no filters", and the search still works. The LLM answer and the query embedding are cached for an hour; the answer is keyed by prompt version + vocabulary + query.

## Why, and the trade-offs
- **The eval set came first, labelled by rules, not by eye.** Each query has a hand-written rule ("occasion wedding, not an accessory, ≤ ₦50,000"), applied to the catalog's attributes. Labels can't drift towards what the search returns, and anyone can read why a product counts as relevant.
- **Hybrid ≈ vector on this set, and I say so.** Voyage alone scores 0.902. Keyword search earns its place on exact names a model may not know (print names, SKUs), and none of the 50 queries test that. The LLM filters are what beat vector, on price (0.87 → 0.97) and intent (0.81 → 0.86).
- **LLM answers vary between runs (±0.02 recall)**, so prompt changes were judged on several fresh runs, not one. Prompt v2 scored the same as v1 (0.925 vs 0.930) but was kept: it fixes a failure that hides every right answer, and drops a hint that was overfitted to one eval query.
- **The LLM is skipped for queries under 3 words.** Half the queries skip a ~1.5 s call, for −0.004 recall. "owambe outfit" loses its occasion filter, and colour queries actually do better without one.
- **OR keyword matching was tried and rejected for hybrid** (0.899 → 0.890): matching any word pulls in noise like the "Owambe" print name.
- **Sold-out products keep their place in search.** Moving them last was tried, but vector search always returns candidates, so a sold-out slip dress fell below unrelated in-stock blazers (vector 0.886 vs 0.902). The card says "Sold out" instead. Recommendations *do* drop sold-out items, because there the point is something you can buy.
- **"Bought together" needs 2 shared paid orders and stays hidden until then.** With 1 real order that's honest. A threshold of 1 would show everyone one shopper's odd basket.
- **Description checks in two layers.** Free regex rules catch literal claims (silk, machine washable, handmade, ₦, a number not in the facts). A Claude claim check catches paraphrases ("keeps you cool", "a soft inner layer"). The planted-claim test shows the split: every paraphrase was caught by Claude, not by the rules.
- **Tuning the checker was a precision/recall trade.** Checker v2 rejected style ("bold") and definitions ("a peplum hem shapes the waist"), and only 13% of drafts passed first time. v3 asks "could a shopper find this false when it arrives?": 83% pass first time on the same 30 products, and still 100% of planted claims are caught.
- **Nothing goes live without an admin.** An edited draft is re-checked by the rules (the admin is the human reviewer, so no extra LLM call). A failed draft can only go live after an edit that passes the rules.
- **The job releases its database transaction before calling the LLM**, so a 10 s Claude call doesn't hold a connection idle in a transaction. Bulk drafting spaces jobs 3 s apart, so order emails don't queue behind 130 LLM calls on the single in-process worker.

## Decisions made while building
- **Model:** `voyage-4` (newer than voyage-3.5, same 1024 dimensions, so no column change). Claude `claude-haiku-5-5` everywhere, as PLAN says. Low effort for query parsing, medium for drafting and checking.
- **A re-seed no longer overwrites descriptions** that an admin wrote or approved (`products.description_source`).
- **Latency from here:** a search makes ~10 database round trips at ~0.37 s each (Lagos → Neon in Ohio), so 4–6 s locally. On Render next to Neon a round trip is ~2 ms, and the LLM call (~1.5 s) is the only cost that matters. Phase 8 measures it there.

## Bugs I made and caught this phase
1. **Non-deterministic eval.** Ties were broken by random UUIDs (new in each throwaway database), and the LLM disk cache was keyed with Python's `hash()`, which is randomised per process. The same code gave 0.930, 0.933, 0.913. Ties now break by slug and keys use sha256; two runs in a row now match exactly.
2. **Sold-out demotion** (see trade-offs): the test that was meant to prove it worked showed it pushed the right product off the page.
3. **The embedding backfill** saved one product per round trip, 10+ minutes from Lagos. It's now batched (27 s).
4. **A foreign key with no delete rule** blocked deleting a test admin. Found only by running the real e2e clean-up against the new table.
5. **A test changed a variant's price and didn't put it back.** Caught in review before commit: the seed-equality check would have flagged it later.

## Open for you
- **A held-out search set.** I wrote both the 50 queries and the prompt, so the queries are a development set. For Phase 8, could you write ~20 queries without looking at the prompt (including some print names like "Lekki Sunset")? I'll report them separately.
- **Draft all descriptions?** Admin → Descriptions → "Draft all template descriptions" costs about $0.13 for the catalog. Then you approve each one. I haven't run it on Neon, because what goes live is your call.

## 3 interview questions this phase lets me answer
1. *"How do you know your hybrid search is better than keyword search?"*: 50 queries labelled by written rules before the search existed. recall@10 0.919 vs 0.307/0.701. Then the honest part: hybrid ≈ vector here, the gain comes from LLM filters on price and intent, and it's a development set, so a held-out set comes next.
2. *"How do you stop an LLM from making things up in product copy?"*: Facts as data. Two check layers, with a planted-claim test proving each one's job. One retry with the failures fed back. A human approves, and an edit is re-checked.
3. *"What happens to search when the LLM is slow or down?"*: It's an optional step running in parallel with the embedding, with a 5 s timeout and caching. Any failure means "no filters". It's skipped for short queries. Filters that match nothing are relaxed, but the price is kept.
