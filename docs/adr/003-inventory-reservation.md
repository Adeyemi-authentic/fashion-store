# ADR-003: Inventory holds with a conditional UPDATE, expiry checked on read

- **Status:** accepted (2026-10-12)

## Context
Stock is limited (e.g. 10 units of a printed adire dress). Many buyers check out at once. Payment happens on Paystack's site and can take minutes, or never finish. We must never sell more than we have, never lock stock up forever, and must deal with a payment that arrives after the hold has expired.

## Decision

**1. Hold stock with one atomic statement per variant:**
```sql
UPDATE variants
SET stock_reserved = stock_reserved + :qty
WHERE id = :id AND stock_on_hand - stock_reserved >= :qty;
```
Zero rows updated means out of stock, and the whole checkout transaction rolls back. Postgres row locks queue concurrent buyers, so the check and the write cannot be separated by another buyer. Variants are updated in **sorted id order** so two multi-item checkouts can't deadlock. `CHECK (stock_reserved BETWEEN 0 AND stock_on_hand)` is the last line of defence.

**2. Track holds in two places, kept in sync:** the `stock_reserved` counter (fast to check) and `reservations` rows (who holds what, until when). Both change in the same transaction, and a test asserts that `stock_reserved = SUM(active reservations)`.

**3. Expiry is checked on read, the worker only tidies up.** Before reserving, checkout releases any expired holds on the variants it touches, in the same transaction. Product pages compute availability as `on_hand - reserved holds that haven't expired`. The scheduled expiry job releases the rest and marks orders `expired`, but if the worker is asleep, stock is still correct.

**4. Paid = hold becomes a real deduction:** `stock_on_hand -= q, stock_reserved -= q`, reservation `released_at = now()`.

**5. Late payment (order already `expired`):** run the same conditional UPDATE again.
- Success → `paid` (the customer gets what they paid for).
- Zero rows → `refund_pending`, a refund outbox job (Paystack refund API), an apology email and an admin alert.

**6. Lock order (added in Phase 4).** Everything that touches stock locks in the same order: **order row → its reservation rows → variant rows (in id order)**. Checkout has no order yet, so it locks its variants (in id order) first. It touches other orders' reservations only with `SKIP LOCKED`, so it never waits on a lock taken in the opposite direction. That's also why checkout only *releases* other orders' expired holds and never marks those orders `expired`: marking an order means locking it, after variants are already locked, which is the reverse of the webhook's order. The webhook instead treats "still `pending_payment`, but my holds ran out" as a late payment.

## Consequences
- **Good:** correct under any amount of concurrency without app-level locks, Redis locks or retry loops. Easy to explain and to load-test (Phase 8: 200 buyers, 10 units → exactly 10 paid).
- **Bad:** a hot item serialises on one row. That's fine at demo scale. At real flash-sale scale, you'd split stock across several "bucket" rows or put a queue in front of checkout.
- **Bad:** a 15-minute hold means stock can look sold out while people abandon payment. Hold length is configurable (`HOLD_MINUTES`).

## Alternatives rejected
- **`SELECT ... FOR UPDATE`, then check in Python, then `UPDATE`:** correct, but more round trips while the lock is held. The single statement does the same job.
- **Optimistic locking with a `version` column + retries:** under heavy contention most attempts fail and retry. Not needed when one conditional UPDATE does it.
- **Redis `DECR` counters:** fast, but it adds a second store that must be kept in sync with Postgres (and there is no Redis in this project, ADR-006). Keeping it in sync with Postgres brings back the dual-write problem.
- **No hold (only deduct when paid):** overselling is then guaranteed, because two people pay for the last unit.
