# ADR-004: Money stored as integer kobo

- **Status:** accepted (2026-10-12)

## Context
Prices are in naira. Floats can't represent most decimal amounts exactly (`0.1 + 0.2 != 0.3`). Paystack's API takes and returns amounts in kobo (₦1 = 100 kobo).

## Decision
- Every money column is `BIGINT` with a `_kobo` suffix (`price_kobo`, `total_kobo`). Every order has a `currency` column, which is always `NGN` for now.
- Python works with `int` only. Pydantic models reject floats for `*_kobo` fields.
- Turning kobo into "₦45,000" happens **only** in the UI (one `formatNaira()` helper) and in email templates.
- Order totals are the sum of `qty × unit_price_kobo` in integers, worked out on the server. The client never sends a price.
- The webhook compares Paystack's `amount` (kobo) and `currency` with the order exactly.

## Consequences
- **Good:** exact sums, no rounding drift, and the same units as Paystack, so nothing gets converted at the payment edge.
- **Bad:** easy to get a ×100 bug when displaying. A test checks `formatNaira()` and that seeded prices are whole naira.
- **Out of scope:** multiple currencies. If added, amounts would be stored in each currency's smallest unit next to an ISO currency code.

## Alternatives rejected
- **`NUMERIC(12,2)` naira:** also exact, but the payment edge would have to convert and the Python side would need `Decimal`. More to get wrong for no gain.
- **Float:** wrong.
