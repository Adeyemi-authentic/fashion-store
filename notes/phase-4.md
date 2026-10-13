# Phase 4: Checkout, Paystack, inventory

**Status:** built and tested, waiting for review. Please practise the walkthrough below aloud, and answer the 3 interview questions without notes, before saying "go" (per PLAN.md).

## What was built (one concern per commit)
| Commit | Concern | What goes wrong without it |
|---|---|---|
| `81ccda7` | Order state machine: `lock_order()` + `transition()` | The webhook, callback and expiry job each change status their own way. A slow webhook could turn `refunded` back into `paid`. |
| `40df41b` | Stock holds: conditional `UPDATE`, expiry checked on read, sorted locks | Overselling. Abandoned checkouts making stock look sold out while the worker sleeps. Deadlocks between two multi-item carts. |
| `5f129b9` | Paystack client + fake + HMAC check | A slow Paystack hangs checkout. Signature checks leak timing. A missing key would accept every webhook. |
| `9d92c95` | Checkout: idempotency keys, one transaction, Paystack after commit | A double-tap creates two orders and holds the stock twice. Locks are held while waiting on Paystack. |
| `e562c80` + `516a727` | Webhook: signed, stored once, 200 except forgeries, late payments | Free goods for a forged POST. Paying (and emailing) twice. Paystack retrying for days. A late payment either lost or oversold. (The second commit fixes an import cycle I introduced; see below.) |
| `89efe31` | Callback check: verify with Paystack | Typing `?reference=…` into a URL looks paid, or the page sits on "pending" until the webhook arrives. |
| `2d23c22` | Admin cancel | A stuck order keeps its stock for the full 15 minutes. |
| `0b2f458` | Checkout UI, callback page, order history | (the user-facing side) |
| `34156b1` | e2e: real Paystack test checkout | No proof the whole thing works against the real provider. |

**Tests:** 173 API tests and 6 browser journeys pass, 2 of which pay on Paystack's real test page. The real-Safari cart check passes. ruff, mypy and import-linter are clean.

## Walkthrough to practise aloud (one checkout, start to finish)
1. **Customer taps "Pay".** The browser makes one random `Idempotency-Key` for this attempt and sends `POST /api/checkout`. Next.js forwards it to the API (same origin, so the cookie is first-party).
2. **Claim the key.** `INSERT … ON CONFLICT DO NOTHING` into `idempotency_keys`, scoped to this buyer.
   - If a twin request with the same key is mid-transaction, Postgres makes this insert **wait** until the twin commits.
   - Then we find the key and either replay the stored response (twin finished), answer 409 + `Retry-After: 1` (twin still running), or answer 422 (same key, different body).
3. **Same transaction: hold the stock.**
   - Lock every variant in the cart **in id order**.
   - Release *other* orders' expired holds on those variants, skipping any someone else has locked.
   - Run `UPDATE variants SET stock_reserved = stock_reserved + q WHERE id = … AND stock_on_hand - stock_reserved >= q` per variant. Zero rows updated means out of stock: roll **everything** back and answer 409 with the SKUs.
4. **Same transaction: the order.** Create a `pending_payment` order, copy prices into `order_items` (never re-read later), add `reservations` rows (expire in 15 minutes, using the database's clock), and **commit**. Holds and order now exist together, or not at all.
5. **After the commit: Paystack.** Call `initialize` with a 10 s timeout. No transaction is open, so a slow Paystack holds none of our locks. If it fails: cancel the order, release its holds, delete the key, answer 502. Retrying the same key then starts fresh.
6. **Second transaction:** mark the key `done` and store the response. The browser goes to Paystack's hosted page. Card data never touches us.
7. **Customer pays. Two things race:**
   - **Webhook:**
     - Check the HMAC-SHA512 of the *raw* body. A forgery gets 401, the only non-200.
     - Insert into `payment_events` with key `event:data.id` (a repeat delivery answers 200 "duplicate").
     - Ignore anything that isn't `charge.success`.
     - Otherwise run `apply_successful_charge()`.
   - **Callback page:** polls `GET /orders/by-reference/{ref}`. If the order isn't settled, the API asks Paystack's verify API (outside any transaction), then calls the **same** `apply_successful_charge()`.
8. **`apply_successful_charge()`**, with the order row locked:
   - Already paid or later → no-op. That's what makes webhook + callback + redelivery safe.
   - Wrong amount or currency → `needs_review`, admin alert, still 200.
   - Pending, with unexpired holds covering every item → the holds become a sale (`on_hand -= q`, `reserved -= q`, holds released). The order becomes `paid`. Bought items leave the bag, and a confirmation-email job goes in the outbox.
   - Holds ran out (even if nothing marked it expired) → expire the order. Then try to sell from free stock inside a savepoint: every item, or nothing. Success → `paid`. Stock gone → `refund_pending`, with refund (Paystack transaction id), apology email and admin alert jobs.
   - Cancelled order, money arrived anyway → `refund_pending`.
9. **All jobs** are outbox rows written in the same transaction as the change that caused them. The worker that runs them comes in Phase 5. Nothing about stock correctness waits for it.

## Why, and the trade-offs
- **Expiry is checked on read, and the worker only tidies.**
  - `available_qty()` = on hand − reserved + *expired-but-unreleased holds*: a correlated subquery that uses the partial index `ix_reservations_active`.
  - The free hosting tier's worker can sleep for hours and stock is still right. Tested with no job running at all.
  - Cost: one small subquery per availability read.
- **Two records of every hold** (a counter and reservation rows), always changed in the same transaction. The counter makes the conditional `UPDATE` cheap. The rows say who holds what until when. Every inventory and checkout test ends by checking that they agree (`reserved_mismatches() == []`).
- **The lock order is the deadlock rule:** order → its reservations → variants in id order.
  - Checkout has no order, so it locks variants first, and touches other orders' reservations only with `SKIP LOCKED`.
  - That's why checkout releases other orders' expired holds but **never marks those orders expired**: that would mean locking an order after its variants, the reverse of the webhook.
  - The test with carts [a,b] vs [b,a] fails with `DeadlockDetected` when the sorting is removed.
- **Paystack is called after the commit, never inside it.** Holding row locks on a hot variant during a 1–10 s network call would queue every other buyer behind it. Cost: a crash between commit and Paystack leaves an `in_progress` key and a pending order. Its holds expire after 15 minutes and the expiry job marks it `expired`, so nothing is lost or oversold. The customer's next attempt gets a new key.
- **Keep the bag until payment succeeds** (`orders.cart_id`, migration 0002). Emptying it at checkout would make an abandoned payment cost the customer their bag. Cost: two pending checkouts from one bag are possible. The second holds its own stock, and the first expires.
- **200 for everything except a bad signature.** Paystack retries any non-2xx. A wrong-amount event is a problem for a human, so it gets a flag and an alert, not a rejection.
- **The redirect is never trusted.** The callback page shows what the API says, and the API asks Paystack itself.
- **Refunds and emails are outbox jobs, not inline calls.** They happen at least once, after the payment transaction commits. Phase 5 makes each handler idempotent (Resend `Idempotency-Key` = job id; check for an existing refund first).

## Decisions made while building
- **New edge in the state machine: `cancelled → refund_pending`** (money arrives after a cancel). Added to the diagram.
- **Stock availability moved to `orders/availability.py`**, a leaf module. My first version had the catalog import all of `orders.service`, which created an import cycle: catalog → orders → cart → catalog. Whether the app started depended on import order. A new test imports every module *first* in a fresh interpreter; 4 of 7 fail on the old import.
- **Late payment when the order is still `pending_payment` but its holds ran out:** expire it first (a valid edge), then follow the normal late-payment path. One path to test, not two.
- **Idempotency keys are scoped per buyer** (`c:<customer id>` or `g:<hash of guest token>`), so two shoppers who happen to send the same key value never see each other's orders. Tested.
- **Callback status `under_review`** for flagged payments, so a customer isn't told "pending" forever.
- **Picker and pay buttons are disabled until hydrated.** The e2e test found that a tap on a fast, prerendered product page, before React had hydrated, did nothing at all: the add-to-bag request was never sent. Real slow phones would hit this too. A `useSyncExternalStore` flag keeps the buttons disabled for those few hundred milliseconds.
- **Paystack's hosted page shows your account's business name ("HiiPower Solns").** The storefront says "fictional brand". Before the public launch, either rename the Paystack test business to something neutral or mention it in the README. **Your call**; noted for Phase 9.

## Bugs I made and caught this phase
1. **Committed `e562c80` with 2 test files failing to import.** My command didn't stop on a failed test run. Fixed in `516a727`, and every commit since only goes in if the full suite passes.
2. **An e2e clean-up race put back one extra unit of stock.** Each spec cleaned up when it finished, specs run in parallel, and two clean-ups returned the same paid order's unit twice. Neon had one extra `FF-049-DEEP-INDIGO-S`, which I set back to the seed's 9. Now there's a single global teardown, and the clean-up locks the orders it handles. After a full run, **every SKU matches the seed** (checked SKU by SKU), with 0 orders and 0 reserved left.
3. **Test-only bugs:** an idempotency mutation check, a session-expiry `MissingGreenlet`, and a reused events table. Each was found and fixed before committing.

## Measurements
- **API:** 173 tests in about 10 s. Each concurrency claim has a mutation check: the test fails when the protection is removed.
  - Idempotency claim removed → 3 tests fail.
  - Sorted locking removed → `DeadlockDetected`.
- **Concurrency:** 30 buyers racing for 10 units → exactly 10 holds. 12 shoppers through the full HTTP checkout racing for 5 units → exactly 5 orders, and the counter matches the reservation rows.
- **Live Paystack (test mode), 2 journeys:** checkout → hosted page → "Success" → callback → paid in a few seconds, including Lagos → Ohio database latency. These ran twice, plus the Safari cart check.
- **Network note:** one `/health` call took **72 s** during this phase because the hotspot dropped. Everything is fine at the usual ~0.75 s. The API on Render next to Neon removes this (Phase 9).

## Known gaps (on purpose, later phases)
- **The webhook isn't live-tested yet.** Paystack can't reach `localhost`. It's covered by signed-payload tests (forged, duplicate, concurrent duplicate, wrong amount/currency, late, refund). Phase 9: set the webhook URL in the Paystack dashboard to the Render URL and repeat the e2e payment with the callback check disabled.
- **The outbox jobs aren't run yet** (no emails, no refunds actually sent). Phase 5 adds the worker, retries, the dead-letter queue and idempotent handlers. `expire_overdue_orders()` exists and is tested; Phase 5 schedules it.
- **Stale `in_progress` idempotency keys** (crash between commit and Paystack) stay at 409 for that key. The holds expire anyway. Phase 5's clean-up job deletes keys older than 24 hours.
- **No rate limit on checkout yet** (Phase 5).
- **The order list doesn't link to an order page with its items**; the admin sees status, buyer, total and item count. Phase 5/9 if needed.

## Done when
- [x] The same webhook twice gives one payment (sequential, concurrent, and as a different event id).
- [x] A wrong amount or currency leaves the order unpaid and flagged, and returns 200.
- [x] A forged signature gets 401 (and so does a missing one), with nothing stored.
- [x] An expired hold puts stock back even with the worker stopped: shoppers see it and can buy it, with no job running.
- [x] A late payment with stock left becomes `paid`; one with no stock left becomes `refund_pending` (refund, apology and alert jobs).
- [x] Two concurrent checkouts with the same idempotency key create one order.
- [x] The same key with a different body gets 422.
- [x] `stock_reserved` matches the reservations table after the concurrency tests (checked at the end of every stock test).

## 3 interview questions this phase lets me answer
1. **"200 people try to buy the last 10 units at once. Walk me through how you avoid overselling."** Each checkout runs one conditional `UPDATE … SET stock_reserved = stock_reserved + q WHERE stock_on_hand - stock_reserved >= q` per variant, inside the transaction that creates the order. Postgres row locks make the competing updates queue on that row. Each one re-checks the condition against the latest committed value, so exactly 10 match and the rest update 0 rows and get a clean 409. No app-level locks, no retries, no Redis. Multi-item carts lock variants in id order, so they can't deadlock; I proved that by removing the sort and watching Postgres report `DeadlockDetected`. A `CHECK (stock_reserved <= stock_on_hand)` is the backstop if the code is ever wrong. Tested: 30 buyers for 10 units → exactly 10 holds.
2. **"A customer pays after their 15-minute hold expired. What happens?"** We never ignore money. The webhook (or the callback check, through the same function) locks the order, sees the holds have expired (even if no job has marked it yet), marks it `expired`, then tries to take the units straight off the shelf in a savepoint: every item or nothing. If they're still free, the order is `paid` and they get their goods. If someone else bought them, the order becomes `refund_pending`, and an outbox job refunds the exact Paystack transaction. They get an apology email, and an admin is alerted. All of it happens in one transaction with the payment-event record, so a retry can't do it twice.
3. **"How do you make checkout safe against double-taps and network retries?"** The client sends an `Idempotency-Key` per attempt. The server claims it with `INSERT … ON CONFLICT DO NOTHING` in the same transaction as the order, so a concurrent twin waits for the first to commit. A finished key replays the stored response. An unfinished one gets 409 + `Retry-After`. The same key with a different body gets 422, because guessing is worse than failing. Keys are scoped per buyer. The Paystack call happens after the commit, so the key's work and the order are atomic, but no lock is held across the network. Tested with two simultaneous requests: one order, one Paystack transaction.
