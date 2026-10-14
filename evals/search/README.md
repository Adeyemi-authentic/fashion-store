# Search eval: 50 labelled queries

This set was committed **before any search code was written**, so it can't have been shaped to fit the results (PLAN.md, Phase 6 "done when").

## Files
- `queries.jsonl`: 50 queries, each with a `kind` and a hand-written labelling **rule**.
- `labels.json`: the relevant products for each query. It is generated from the rules by `uv run python -m scripts.label_search_queries` (run from `api/`).

## How the labels were made
Each rule says what a shopper typing that query would accept. For example, "wedding outfit under 50k" means: occasion includes wedding, not an accessory, from-price ≤ ₦50,000.

The script applies the rules to the catalog's **structured attributes** (category, type, gender, occasion, colours, price). It never looks at search output, so the labels can't drift towards what the search returns.

Only visible products count. The headwear category has no images yet, so it is hidden, which is why no query is about filas or turbans. Stock is ignored, because a sold-out product is still a correct answer: the search shows it with a "sold out" label rather than hiding it.

## Mix of queries
| Kind | Count | Tests |
|---|---|---|
| exact | 15 | Product words as written ("linen shirt") |
| synonym | 15 | Different words for the same thing ("cargo pants" → Cargo Trousers, "vest" → Waistcoat, "indigo dyed" → adire) |
| intent | 7 | Occasion and audience ("owambe outfit", "office wear for women") |
| price | 5 | A price limit in the words ("under 50k", "above 400k") |
| colour | 5 | A colour, sometimes as a synonym ("green aso oke" → Emerald) |
| typo | 3 | Misspellings ("agbadda", "ankra dress") |

Some traps are on purpose. "Owambe" is also a print name, so "Adire Owambe Two-Piece Set" (a casual/work outfit) contains the word without being an owambe outfit.

## Metrics (fixed before measuring)
- **recall@10** = relevant products in the top 10 ÷ min(10, number of relevant products). The cap means a query with 24 relevant products can still score 1.0. The Phase 8 target is ≥ 0.85.
- **MRR**: 1 ÷ the rank of the first relevant result, averaged over the queries.
- **zero-result rate**: how often a query returns nothing at all.

Results by mode (keyword-only, vector-only, hybrid, hybrid + LLM filters) go in `RESULTS.md`.

## Tuning log (newest last)
Each step was measured on all 50 queries. LLM answers vary between runs, so prompt changes were judged on several fresh runs (`--fresh-llm`), not one.

| Step | Change | Result | Kept? |
|---|---|---|---|
| 0 | Baselines | keyword (all words) 0.307, vector 0.902, hybrid 0.899 | — |
| 1 | Sold-out products moved to the end of the results | Vector 0.886 with it, 0.902 without. Vector search always returns candidates, so a sold-out slip dress fell below unrelated in-stock blazers | No: sold-out items keep their place and show a label |
| 2 | Claude turns the query into filters (prompt v1) | 6 runs: 0.913–0.939, mean 0.930 | Replaced by v2 |
| 3 | Keyword search where any word may match (OR) | Keyword-only 0.307 → 0.701, but hybrid 0.899 → 0.890: matching any word adds noise (e.g. the "Owambe" print name) | Only as the fairer keyword baseline |
| 4 | Prompt v2: the store's product types per category go in the prompt. Removed a hint ("naming ceremony → traditional") that mapped one eval query directly | 3 runs: 0.923–0.929, mean 0.925. Fixes "scarf" and "head wrap" being filtered to accessories, a mistake that hides every right answer. Without the overfitted hint, "naming ceremony" drops from 1.0 to 0.5 | Yes: a cleaner prompt at the same score |
| 5 | Skip the LLM for queries under 3 words with no digit | 0.919 vs 0.923 with the same cached answers. Half the queries skip a ~1.5 s call. Colour queries do better without filters (0.98 vs 0.93), 2-word intents slightly worse ("owambe outfit") | Yes |

**Where it stands:** the storefront's search scores **0.919 recall@10** against **0.307** for all-words keyword search and **0.701** for any-word keyword search. The Phase 8 target (≥ 0.85, hybrid beats keyword-only) is met.

**What this eval can't show yet:**
- **It is a development set, not a held-out test.** The same person wrote the queries and the prompt, so the prompt may fit these queries better than real ones. Phase 8 should add about 20 queries written by someone who hasn't seen the prompt, and report those separately.
- **Hybrid ≈ vector here.** Keyword search earns its place on exact names a model may not know, such as print names ("Lekki Sunset", "Harmattan") or a SKU, and none of the 50 queries test that. The held-out set should include some.
- **The remaining misses are mostly ranking, not filtering:** a grand boubou below kaftans for "agbada", and aso-oke for "hand woven fabric".
