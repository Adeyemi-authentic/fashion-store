# Phase 5: Async jobs, caching, observability

**Status:** built and tested, waiting for review. Please practise the walkthrough below aloud, and answer the 3 interview questions without notes, before saying "go" (per PLAN.md).

## What was built (one concern per commit)
| Commit | Concern | What goes wrong without it |
|---|---|---|
| `d5e17dd` | Request IDs + JSON logs | "My email never came" can't be traced: nothing links the request to the job. |
| `f36973b` | Worker core: `SKIP LOCKED`, leases, fencing, backoff, dead-letter | Two workers run one job. A killed worker strands its job forever. A stalled worker marks a job done while another is still running it. A broken job retries forever. |
| `8fca476` | Email jobs, with the job id as idempotency key | A retried job emails the customer twice. |
| `1cfe606` | Refund job checks first; refund webhooks | A retried refund pays the customer back twice. Refund events overwrite each other (a dedupe-key bug, fixed here). |
| `69a54f6` | Scheduled jobs + low-stock alert | Expired holds and old rows pile up. The schedule gets lost or doubled after a crash. |
| `4d785e2` | Worker in the API or alone; SIGKILL test | No proof that a killed worker loses nothing. |
| `da429b7` | Rate limits + trusted client IP | Card testers and password guessers get unlimited tries. A fake `X-Forwarded-For` would dodge the limit. |
| `85b7853` | Catalog cache + live stock endpoint | Every page view goes to the database. Or, cached naively, stock numbers go a minute stale on the product page. |
| `6479b25` | `/metrics` + Sentry | Nobody notices a growing queue or a dead job. |

**Tests:** 216 API tests, 6 browser journeys (2 paying on Paystack's test page) and the real-Safari check all pass. ruff, mypy and import-linter (3 contracts) are clean. **Your own Phase 4 order's confirmation email** (queued since Phase 4) was sent by the new worker on its first start; it's in `api/.emails/job-6.json`.

## Walkthrough to practise aloud (a paid order's email, and a killed worker)
1. **The webhook marks the order paid.** In the same transaction it inserts an `outbox_jobs` row (`order_confirmation_email`, payload `{order_id, request_id}`). If the transaction rolls back, the job never existed. If it commits, the job exists. No dual write.
2. **The worker loop** (inside the API process on the free tier) polls about once a second:
   `UPDATE outbox_jobs SET status='running', locked_until=now()+60s, attempts=attempts+1 WHERE id IN (SELECT … WHERE (queued AND due) OR (running AND lease expired) … FOR UPDATE SKIP LOCKED) RETURNING …`
   Two workers never take the same row and never wait for each other. The returned `attempts` value is this worker's **fencing token**.
3. **Running the job:**
   - The worker puts the job's `request_id` back into the logging context, so its log lines join up with the webhook's.
   - It runs the handler in a fresh transaction, time-boxed to 80% of the lease.
   - The handler loads the order and sends the email with idempotency key `job-<id>`.
4. **Finishing:** `UPDATE … SET status='done' WHERE id=… AND status='running' AND attempts=<my token>`, committed **together with** anything the handler wrote. If that matches 0 rows, my lease expired and someone else owns the job, so I roll back.
5. **Killed after sending, before committing?**
   - The row stays `running` until the lease runs out. Then another worker claims it (attempts=2) and runs it again.
   - The email goes out with the same key, and Resend (or our file sender) treats it as already sent. **Ran twice, delivered once.**
   - A refund works the same way, but by checking first: list the refunds on that Paystack transaction, and only create one if there isn't one.
6. **It fails instead?** Roll back the handler's writes and put the job back in the queue at `now() + random(0, 2^attempts s)` (full jitter, so failures don't retry in step). After 6 attempts, or straight away for a permanent error such as "order not found", it's `dead` (the dead-letter queue). An admin alert job is queued, logged at error level so Sentry sees it.
7. **Scheduled jobs** (expire holds every minute, clean-up hourly) queue their own next run in the same transaction as their "done". A `singleton_key` with a partial unique index keeps one live copy, even if two workers start at once.

## Why, and the trade-offs
- **Polling Postgres, not a broker.** About 1 s of latency is fine for email, there's no extra service to run, and the queue is plain SQL you can inspect. Upgrade path: `LISTEN/NOTIFY` to wake the worker, or feed a real queue from the outbox.
- **At least once + idempotent side effects, not "exactly once".** Exactly-once delivery across a crash isn't possible without the receiver's cooperation. The receiver's cooperation *is* the idempotency key (Resend's header), or our own check before acting (refunds). The SIGKILL test proves both halves: the job ran twice, and the effect happened once.
- **Fencing on `attempts`, not just a lease.** A lease alone lets a paused worker (long GC pause, laptop sleep) wake up after its lease expired and mark a job done while the new owner is still running it. The first version of my lease test **passed even with the fence removed**, because it only covered a stale worker finishing *after* the new owner had finished. I added the case where the new owner is still running. That one fails without the fence (`'done' != 'lost_lease'`).
- **The handler's writes and "done" commit together.** A refund job's database changes, or a scheduled job's next run, can't exist without the job being marked done, or the reverse.
- **Rate limits count failed attempts too**, in their own transaction. Card testers fail most of the time, and a limit that only counts successes wouldn't stop them. Fixed windows allow a burst of up to 2× across a minute boundary. That's accepted, because the goal is stopping scripted abuse, not exact fairness.
- **`X-Forwarded-For` is trusted only from our own proxy.** Every request reaches the API from the Next.js proxy, so the socket IP is useless and the real IP is in that header. But anyone can send that header, so it only counts with `X-Proxy-Auth` = the shared secret. Tested: a script sending a new fake IP each time still gets 429. On Vercel the platform overwrites `X-Forwarded-For`; locally the browser could still set it.
- **The cache stores content, not stock.** The product detail is cached for 60 s and the live stock has its own uncached endpoint. Caching the detail naively would undo Phase 2's "live stock on a cached page". Cart and checkout never read the cache. Admin writes clear it in the same request. With 2+ API processes each one has its own copy, so other processes stay stale for up to 60 s (ADR-006 says when to add a cross-process clear).
- **Metrics are read from Postgres at scrape time** (queue depth, oldest due job), so they're right even while the in-process worker is asleep on the free tier. Route labels use the template (`/products/{slug}`), not the raw path, so the number of time series stays small.

## Decisions made while building
- **Bug in my Phase 4 code:** the webhook's dedupe key was `<event>:<data.id>`, and refund events may have no `data.id`. Every refund event would then be `refund.processed:None`, and all but the first would be dropped. It now falls back to `refund_reference`, then `reference`, then a hash of the body. The collision test fails with the old key.
- **The refund flow is now complete:** `refund_pending` → refund job (check first, then create) → `refund.processed` webhook → `refunded`. A `refund.failed` event flags the order for review and alerts an admin. The refund job refuses any order that isn't `refund_pending`, so a paid order can't be refunded by mistake.
- **Low-stock alert fires once**, on the sale that crosses from above 3 to 3 or fewer. Not on every later sale.
- **Clean-up job:** rate-limit windows older than 1 hour, idempotency keys older than 24 hours (this also clears keys stuck `in_progress` after a crash, a Phase 4 known gap), guest carts idle for 30 days, done jobs older than 7 days. Payment events are kept, as the audit trail.
- **Worker inside the API by default** (`WORKER_IN_API=true`), matching the Phase 9 free-tier plan. `python -m app.worker_main` runs it separately, and SIGTERM finishes the current job first.
- **Emails without Resend** are written to `api/.emails/<key>.json`, where an existing file means "already sent". Add `RESEND_API_KEY` (and a verified sender domain) to send for real.
- **Embedding refresh on product change** moves to Phase 6, where the embedding pipeline and search are built. The job type and handler come with it.

## Bugs I made and caught this phase
1. **`configure_logging()` removed every log handler**, including pytest's capture. Now it replaces only its own. Also, Alembic's `fileConfig()` was **disabling all existing loggers** when the tests ran migrations in-process, which silenced `app.access`. Fixed with `disable_existing_loggers=False`.
2. **A string-replace edit silently did nothing.** The low-stock alert wasn't in the code at all, because the formatter had already re-wrapped the text my script searched for. The test caught it. My edit scripts now assert their anchors exist.
3. **Two tests assumed things that weren't true:** an empty `payment_events` table, and an exact job list. They now check only their own rows.
4. **An empty log file during the live check** was my shell's fault. I restarted the API while the old process still had the log file open, and the two processes overwrote each other. It now uses a new log file per start.

## Measurements
- **SIGKILL test (real processes):** worker A is killed after the side effect and before the commit. The job stays `running` until its 3-second lease runs out, then worker B finishes everything. The killed job ran **twice** with **one** effect, `attempts == 2`. Takes about 5 s.
- **Three workers, 20 jobs:** each job ran exactly once.
- **Through the real proxy:** 10 × 422 (empty bag) then **429**, with `Retry-After`. The API's warning line carries the request id sent through the proxy (`trace-ratelimit-001`).
- **Cache:** the first read is a miss, the second a hit, both logged with key and cache name. An admin rename shows immediately.
- **Neon after all the e2e runs:** stock equals the seed except the 3 units you bought (FF-012-PINK-BLACK-M, FF-114-SAND-L, FF-044-SKY-INDIGO-XL), with 1 order (yours), 0 holds and 0 reserved.

## Known gaps (on purpose, later phases)
- **No Resend key yet**, so real emails aren't sent. Phase 9: add `RESEND_API_KEY` and a verified sender domain, or keep file mode for the demo.
- **Sentry and `METRICS_TOKEN` are unset locally.** Phase 9 sets both, plus a real `PROXY_SHARED_SECRET` on both sides.
- **Embedding refresh job:** Phase 6.
- **Assistant rate limit and spend cap:** Phase 7.
- **The webhook still isn't live-tested** (Paystack can't reach localhost): Phase 9.

## Done when
- [x] Killing the worker mid-job loses no jobs and runs none twice where it matters: a real worker process is SIGKILLed after the side effect, before the commit. All jobs end done, and the effect happened once (idempotency key).
- [x] A cache hit shows up in the logs (`{"msg": "cache hit", "cache": "catalog", "key": "products:…"}`), tested and seen live.
- [x] The 11th checkout attempt in a minute from one IP gets a 429: tested directly, and through the real Next.js proxy.

## 3 interview questions this phase lets me answer
1. **"Your worker crashes halfway through sending an email. What happens?"** The job was claimed with a lease, not deleted, so it stays `running` until the lease expires. Then another worker reclaims it with `FOR UPDATE SKIP LOCKED` and runs it again. Delivery is at least once, so the email send carries the job id as Resend's `Idempotency-Key`, and the second send is a no-op. I proved it with a real process: SIGKILL after the send, before the commit. The job ran twice and the customer got one email. A paused (not dead) worker can't mark the job done afterwards either, because completion is fenced on the claim's attempt number.
2. **"How do you rate-limit without Redis, and how do you know the client's real IP behind a proxy?"** A fixed-window counter in an `UNLOGGED` Postgres table: one `INSERT … ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count` per request, in its own transaction so failed attempts count too. A crash resets the counters, which is fine for a rate limit. Behind the Next.js proxy every request comes from the proxy, so the IP comes from `X-Forwarded-For`, but only if the request carries our shared-secret header. Otherwise a script just sends a new fake IP each time. Signed-in users are also limited per session, so rotating IPs doesn't help.
3. **"What do you cache, and how do you stop the cache from lying about stock?"** Catalog content (lists, product detail, categories) sits in an in-process TTL cache for 60 s, cleared on every admin write. Stock is never served from it: the product page reads a separate uncached `/stock` endpoint, and cart and checkout read the database. So the worst a stale cache can do is show a "from" price or an in-stock badge up to a minute old. It can't sell what doesn't exist. Hits and misses are logged and exposed in `/metrics`. With several API instances each has its own cache, which is the point where a shared clear (Redis or `LISTEN/NOTIFY`) becomes worth adding.
