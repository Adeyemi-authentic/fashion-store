# Description pipeline eval

`uv run python -m scripts.eval_descriptions` (from `api/`) makes real Claude calls (about $0.18 for the full run) and writes `RESULTS.md`.

## What it measures
1. **Drafting every visible product.** How many drafts pass every check first time, pass after the one retry (the failures are fed back to the drafter), or still fail and need an admin's edit.
2. **Planted false claims.** A false sentence is added to drafts that passed, nine kinds × 20 drafts. Each one counts as caught by the free **rule checks**, caught by the **Claude claim check**, or **missed**. This is the check's recall on known-bad text.

The planted set measures recall only. The other kind of error, the claim check rejecting a draft that was fine, shows up as a lower pass rate. It was tuned by reading every rejection reason (tuning log below).

## Tuning log
| Step | Change | First-time pass | Still failing | Planted caught | Notes |
|---|---|---|---|---|---|
| v1 | First prompts | 47% | 7% | 60/60 | 30 products. Drafts read like spec sheets ("It is listed for casual and work occasions"). The checker flagged "bold" as a claim |
| v2 | Shopper voice for the drafter; checker told to ignore style words | 13% | 30% | 60/60 | 30 products. Worse: the freer drafter wrote more, and the checker still flagged things that follow from the product type ("a peplum hem adds shape at the waist") and styling tips ("wear it over a dress") |
| v3 | Checker: what is true of every product of that type counts as supported; styling suggestions and impressions aren't claims; the test is "could a shopper find it false when it arrives?" | 83% (30 products), **63% (all 131)** | 0% / **2%** | 60/60, **180/180** | Kept. 3 harder paraphrases were added before the full run (a lining, "won't shrink", sizing advice); all caught |

## Limits
- **The planted claims were written by the person who wrote the checker.** Real drafts can fail in ways no one planted. That's why every draft still goes to an admin, and the admin page shows each check's result.
- **The rule list is English-only and literal.** It catches "silk", but not a made-up local fabric name. The Claude check is the backstop, and the planted results show how much rests on it: every paraphrase was caught by Claude, not the rules.
- **Pass rates vary between runs** (83% vs 63% first-time pass on the first 30 products vs all 131) because the model's drafts vary. The numbers are for one run each.
