# Demo video script (about 2½ minutes)

Record the screen at 1080p with your voice over. Before you start: open the live site once so the API is awake, use a private window, and set the browser zoom to 110% so the text reads on a phone.

| # | Time | On screen | Say |
|---|---|---|---|
| 1 | 0:00–0:10 | Home page | "This is FlyFit, a concept fashion store I built to answer the questions backend interviewers ask, with numbers. Fictional brand, test-mode payments, AI-generated photos." |
| 2 | 0:10–0:30 | Search *"wedding outfit under 50k"*; point at the results | "Search is hybrid: keyword and vector, merged by rank. A small LLM step reads the budget and the occasion as filters. On 50 labelled queries, 92% of the right products show up in the top 10." |
| 3 | 0:30–1:00 | Open the assistant. Ask *"show me ankara shirts for men"*, then *"add the cheapest in M"* | "The assistant uses tools: search, sizes, policies, the bag. It can't make up a price, because code checks every naira amount against the database before the reply is shown. Across 120 test conversations: 98% task success, zero made-up prices, 18 out of 18 prompt injections resisted." |
| 4 | 1:00–1:30 | Bag → checkout → Paystack test page → **Success** → order shows **paid** | "Checkout holds the stock for 15 minutes in the same transaction as the order. Paystack's webhook marks it paid. Duplicate webhooks are dropped, and a payment that arrives after the hold expired is re-reserved or refunded automatically." |
| 5 | 1:30–1:55 | `evals/RESULTS.md` on GitHub, the overselling row | "The test I'm proudest of: 200 buyers press Pay at once for 10 units. Exactly 10 paid, 0 oversold. I also broke the code on purpose to make sure the test can fail." |
| 6 | 1:55–2:15 | The architecture diagram in the README | "One FastAPI app split into modules, Postgres as the only source of truth and the job queue, no Redis, because the load test said it wasn't needed." |
| 7 | 2:15–2:30 | README top, live link | "Every number links to the command that reproduces it. Link's below." |

## Tips
- Cut the waiting: the assistant takes about 3 s per reply, so trim the pauses in editing.
- If the API was asleep when you start, the first request takes 30–60 s. Wake it before recording.
- Put the live link and the repo link in the description, and say "concept build, test mode" in the first 10 seconds.
- Captions help: most LinkedIn video is watched on mute.
