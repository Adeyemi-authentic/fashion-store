# Phase 0: Setup and architecture doc

**Status:** written, waiting for review and the account setup below.

## What was built
- Repo layout: `web/` (Next.js, Phase 2), `api/` (FastAPI, Phase 1), `docs/`, `notes/`. Git repo with a repo-local author `Adeyemi-authentic`.
- `docs/ARCHITECTURE.md`:
  - component diagram and a table of which module owns which tables
  - ERD with the invariants tests will check
  - order state machine
  - the full checkout → Paystack → webhook → paid sequence diagram
- ADRs in `docs/adr/`:
  - 001 one app split into modules
  - 002 Postgres outbox queue
  - 003 inventory holds (including late payments and expiry checked on read)
  - 004 kobo integers
  - 005 same-origin API via Next.js rewrites
- `.env.example` covering every service, each value tagged with which app reads it. `.gitignore` keeps `.env` out of git.

## Why, and the trade-offs
- **Correctness lives in Postgres.** Stock holds, order state and jobs are all changed in single transactions. Redis only holds things that are safe to lose. Cost: the database does more work, and a hot item queues on one row. Both are fine at this scale, and ADR-003 gives the upgrade path.
- **Nothing depends on the worker being awake.** That's what makes the $0 hosting option (worker inside the API on a free tier that sleeps) safe instead of a hidden bug.
- **The browser only talks to one origin.** It costs one extra network hop per API call, but cookies work in Safari.

## Decisions made while writing (not in PLAN.md before)
- Multi-item checkouts lock variants in **sorted id order** to prevent deadlocks.
- `CHECK (0 <= stock_reserved <= stock_on_hand)` constraints act as a backstop against bugs.
- The callback page and the webhook share one idempotent `apply_successful_charge()`. Whichever arrives first wins, and the second does nothing.
- Paystack `initialize` is called **after** the checkout transaction commits, never inside it.
- Rate limits: if Redis is down, checkout keeps working (fail open) but the assistant is blocked (fail closed), because the assistant costs money.
- Outbox jobs get a lease (`locked_until`), so a worker that dies mid-job gives the job back.
- Server-side `sessions` table (stores a hashed token), not JWTs, so logout and revoking a session actually work.

## Open question for Phase 4
**What if Paystack `initialize` times out after it actually succeeded?** A retry with the same reference gets "Duplicate Transaction Reference". Plan: store each attempt's reference on the order (`order_ref-1`, `order_ref-2`, …) and accept a webhook for any of them. To be settled with a test in Phase 4.

## Your to-do (needs your logins)
1. **Neon:** create a **new** project `flyfit` (region: AWS eu-central-1 or the closest to Lagos/Render), then run `CREATE EXTENSION vector;`. Copy the **pooled** URL into `DATABASE_URL` and the **direct** URL into `DATABASE_URL_DIRECT`. Change the scheme to `postgresql+psycopg://`.
2. **Upstash:** create a Redis database (free tier, same region) and copy the `rediss://` URL into `REDIS_URL`.
3. **Paystack:** create an account and stay in **test mode**. Copy `sk_test_…` and `pk_test_…`. You'll set the webhook URL in Phase 4, once there's a public URL. Locally we'll use a tunnel.
4. Copy `.env.example` → `.env` and fill these in. **Don't paste keys into chat.** I only need to know they're set.
5. Optional: run `gh auth login` so the repo can be pushed to GitHub later.

## Done when
You can explain the checkout sequence diagram (ARCHITECTURE.md §4) end to end without notes.

## 3 interview questions this phase lets me answer
1. **"200 people try to buy the last 10 units at the same time. Walk me through how you stop overselling."** The conditional UPDATE, row locks, sorted lock order, the CHECK backstop, and why not Redis or optimistic locking.
2. **"How do you make sure a confirmation email is sent if and only if the order is paid?"** The outbox in the same transaction, at-least-once delivery with `SKIP LOCKED`, idempotent handlers, and the dead-letter queue.
3. **"The customer pays, but your webhook arrives 20 minutes later and the stock is gone. What happens?"** Expiry checked on read, the `expired → paid / refund_pending` states, and why the webhook returns 200 instead of an error.
