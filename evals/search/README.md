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
