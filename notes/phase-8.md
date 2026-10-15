# Phase 8: Evals, load and concurrency tests

**Status:** built and run (2026-10-15). **Waiting for your review.** Every PLAN target is met; the summary is in [`evals/RESULTS.md`](../evals/RESULTS.md). Two things are still open: the held-out search queries (yours to write), and re-running the latency test on the deployed API (Phase 9).

## Headline numbers
| Target | Result |
|---|---|
| 200 buyers for 10 units | 10 paid, 0 oversold, stock never below 0, no 500s |
| Same webhook ×5 | 1 payment |
| Search recall@10 | 0.919 (keyword-only 0.307 / 0.701) |
| Assistant task success, 40 scenarios × 3 runs | **98.3%** |
| Made-up prices shown / order leaks | 0 / 0 |
| Injections resisted | 18/18 |
| Catalog p95, cached, 1 worker | < 6 ms up to 800 req/s (local) |
| Assistant per conversation | 5.4 s, $0.0005 |

## What was built (one concern per commit)
| Commit | Concern | What goes wrong without it |
|---|---|---|
| `cda890a` | 200-buyer race through real HTTP, 2 workers, fake Paystack | The concurrency tests prove one process with one pool; a real burst across processes is untested. |
| `1a7fbf4` | Open-loop catalog latency, with the generator checked | A closed-loop tool hides a slow server, and a saturated generator makes up a slow one. Both happened here. |
| `8c193d9` | Search and policy lookup survive the embedding service being down | A dropped connection to Voyage made /search and the assistant return 500s (found during the eval). |
| `2e91513` | A failing model API gets the fallback reply, not a 500 | "Out of credit" or an outage crashes every assistant message (found when the credit ran out mid-eval). |
| `5e65770` | 40 scenarios, independent audits, 3 runs | "No made-up prices" would rest on the same guard being tested, and a single run would hide variance. |

**Tests:** 299 API tests (3 new, one for each failure found by the evals). ruff, mypy and import-linter are clean.

## Walkthrough to practise aloud: "200 people buy the last 10"
1. **Why through HTTP and 2 processes:** the pytest concurrency tests run in one process with one connection pool. A real burst lands on several processes with separate pools, and only the database is shared. That's the claim being tested: the database is the only thing that decides.
2. **The barrier:**
   - All 200 bags are filled first. A bag isn't a hold, so all 200 succeed.
   - Then all 200 press "pay" in the same instant.
   - 162 see zero stock when the bag is checked. 28 get past that check but lose at the conditional `UPDATE … WHERE on_hand - reserved >= 1`. 10 win.
3. **Payment:** each winner gets a signed `charge.success`. Each payment moves a hold into a real stock deduction, so stock ends at 0 on hand, 0 reserved.
4. **The repeat:** one webhook is sent 5 more times at once. The unique event id makes all 5 return `duplicate`, and 1 payment event exists.
5. **Proving the test can fail:**
   - Remove the conditional `UPDATE`: the database `CHECK` constraint still prevents overselling, but 27 buyers get a 500. That's why "no server errors" is now a check.
   - Drop that constraint too: 37 holds are made for 10 units, and `on_hand >= 0` blocks the extra payments.
   - So there are three layers, and the test notices the first one going.

## Why, and the trade-offs
- **Fake Paystack over HTTP, not a fake in the app.** The API's Paystack address became a setting, and the load test runs a small fake server. The app has no test-only code path, and checkout's real HTTP client is part of what's measured.
- **Open loop, timed from when each request was due.** A closed loop (send, wait, send) slows down when the server does, so it never sees the queue: coordinated omission.
- **The generator was the bottleneck first.** One Python process with a 1,000-connection pool topped out near 150 req/s, while ApacheBench showed the API at about 3,800. Four processes with small pools fixed it. Each now reports its CPU, so the results say when the tool, not the API, is the limit. My first numbers were wrong, and the reason is the lesson.
- **Local latency is not production latency.** One worker on a Mac is far faster than Render's free plan (a fraction of one CPU). The target is met locally with a lot of room to spare; the deployed run in Phase 9 is what decides about Redis.
- **The made-up-price audit doesn't trust the guard.** It checks every shown ₦ amount against the database: prices ×1–10, the order total, sums of cart lines, the shopper's numbers. If the guard had a hole (it had one in Phase 7), the audit would catch what got through.
- **Three runs, not one.** Answers vary. Two runs scored 97.5% and one 100%. A single run would have reported either.
- **Changing checks after seeing results is logged.** Two checks were fixed (scenarios 12 and 15). Both were wrong against their own written intent, and both are in the eval README with the reason. The reported 98.3% was scored before the scenario 12 fix.
- **Haiku stays.** Sonnet 5.5 passed the 25 scenarios it ran, at the same speed, for about 20× the price. That's partial evidence (the run stopped when the credit ran out), but nothing in it argues for paying more.

## Bugs found by this phase
1. **/search and the assistant returned 500s whenever Voyage was unreachable.** This was a Phase 6 gap, found when the hotspot dropped mid-eval. Now: keyword fallback and a word-overlap policy search.
2. **Model API errors became 500s.** Found when the account ran out of credit. Now: the fallback reply, and the stored history stays valid.
3. **"Sold out" could have been a crash without any test noticing.** Only the deliberate break showed that the database constraint saves the stock but not the response. Now a check.
4. **My load generator measured itself.** See the trade-offs.
5. **Two eval checks didn't match their own descriptions** (scenarios 12 and 15). Both logged.

## Open for you
- **Top up the Anthropic API credit.** It ran out during the Sonnet comparison. Until it's topped up, the live assistant gives every shopper the "couldn't finish that" reply (no longer a crash), and search runs without its LLM step. The evals in Phases 7 and 8 cost well under $1 in all. Most of it was the partial Sonnet run, whose exact cost wasn't recorded because it stopped before writing results; the Haiku runs total about $0.13.
- **The ~20 held-out search queries** in `evals/search/heldout.txt`. That's the last piece of the search eval.
- **Paystack business name:** the separate FlyFit business, before Phase 9's public checkout.
- From before: bulk-draft descriptions (~$0.13), and the duplicate "Ilaje Three-Piece Agbada" name in the seed.

## 3 interview questions this phase lets me answer
1. *"How do you know you can't oversell?"*: 200 buyers through the real HTTP stack on 2 processes, with a barrier, for 10 units: exactly 10 paid, stock never below 0. Then the part that makes it believable: I broke the guard on purpose. The database constraint still held, but buyers got 500s, so "no 5xx" became a check. Dropping that too showed the third layer.
2. *"How did you load-test, and how do you know the numbers are real?"*: Open loop at fixed rates, latency timed from when each request was due (no coordinated omission). The first run measured my own generator: it fell over at 150 req/s while ApacheBench showed 3,800. Now each generator process reports its CPU and lateness. And local numbers are labelled local; the deployed run decides about Redis.
3. *"How do you evaluate an LLM agent?"*: Targets fixed in advance. 40 scenarios with programmatic checks, run 3 times for variance. Safety metrics audited independently of the guards they test (prices against the database, leaks in tool results). Every change to a check logged with its reason. And the eval found three production bugs that the unit tests with a fake model never could.
