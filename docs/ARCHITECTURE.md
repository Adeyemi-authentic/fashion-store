# FlyFit architecture

FlyFit is a concept build: a fictional brand, Paystack in test mode, AI-generated images. This document covers how the system is put together. The reasons behind each choice are in [`adr/`](adr/).

## 1. Components

```mermaid
flowchart TB
    B[Browser] -->|"HTTPS, same origin /api/*"| W["Next.js storefront<br/>(Vercel)"]
    W -->|"rewrite /api/* → API<br/>(server to server)"| A
    P[Paystack] -->|"POST /payments/webhook<br/>(direct, signed)"| A
    B -.->|"redirect to pay"| P

    subgraph A["FastAPI app (Render), one app split into modules"]
      direction LR
      catalog --- cart --- orders --- payments
      accounts --- admin --- search --- assistant
    end

    A --> DB[("Neon Postgres + pgvector<br/>source of truth, outbox queue,<br/>rate limits")]
    WK["Worker loop<br/>(outbox jobs, FOR UPDATE SKIP LOCKED)"] --> DB
    WK --> EXT["Resend · Paystack refunds · Voyage · Claude"]
    A --> EXT
```

| Piece | Job | Rule |
|---|---|---|
| Next.js | Pages, and the proxy for every browser API call | The browser never calls the API domain directly ([ADR-005](adr/005-same-origin-api.md)) |
| FastAPI | All business logic | Each module owns its tables and is used by other modules only through its service interface ([ADR-001](adr/001-modular-monolith.md)) |
| Postgres | Every durable fact, plus the job queue | A job is written in the same transaction as the change that caused it ([ADR-002](adr/002-postgres-outbox-queue.md)) |
| Caches | Next.js ISR for pages; in-process TTL cache for catalog API responses | Safe to lose at any time; never the only copy. No Redis ([ADR-006](adr/006-no-redis.md)) |
| Worker | Emails, refunds, hold clean-up, embeddings, alerts | At least once, so every side effect is idempotent. Runs inside the API process on the free tier |

### Module → table ownership

| Module | Owns |
|---|---|
| catalog | `products`, `variants`, `product_images` |
| cart | `carts`, `cart_items` |
| orders | `orders`, `order_items`, `reservations`, `idempotency_keys` |
| payments | `payment_events` |
| accounts | `customers`, `sessions` |
| assistant | `assistant_sessions`, `assistant_messages` |
| (shared infra) | `outbox_jobs`, `rate_limits` (UNLOGGED) |

`admin` and `search` own no tables. They call the other modules' services. One exception is allowed and written down: stock changes (`variants.stock_*`) happen only inside `orders.inventory`, never in `catalog`, so that all the concurrency rules live in one file.

## 2. Data model (ERD)

This is the first draft. Phase 1 turns it into Alembic migrations. All money is `BIGINT` kobo ([ADR-004](adr/004-money-in-kobo.md)).

```mermaid
erDiagram
    products ||--o{ variants : has
    products ||--o{ product_images : has
    customers ||--o{ orders : places
    customers ||--o{ sessions : "logged in as"
    customers ||--o| carts : owns
    carts ||--o{ cart_items : contains
    variants ||--o{ cart_items : "in"
    orders ||--|{ order_items : contains
    variants ||--o{ order_items : "sold as"
    orders ||--o{ reservations : holds
    variants ||--o{ reservations : "held on"
    orders ||--o| idempotency_keys : "created by"
    orders ||--o{ payment_events : "paid by"
    customers ||--o{ assistant_sessions : chats
    assistant_sessions ||--o{ assistant_messages : contains

    products {
      uuid id PK
      text slug UK
      text name
      text description
      text category
      bigint base_price_kobo
      text status "draft|active|archived"
      jsonb attributes "fabric, pattern, occasion..."
      vector embedding "pgvector"
      tsvector search_tsv
      timestamptz created_at
    }
    variants {
      uuid id PK
      uuid product_id FK
      text sku UK
      text size
      text colour
      bigint price_kobo
      int stock_on_hand "CHECK >= 0"
      int stock_reserved "CHECK >= 0 AND <= on_hand"
    }
    customers {
      uuid id PK
      text email UK "stored lowercase, CHECK"
      text password_hash "argon2"
      text role "customer|admin"
    }
    sessions {
      text token_hash PK
      uuid customer_id FK
      timestamptz expires_at
    }
    carts {
      uuid id PK
      uuid customer_id FK "null for guests"
      text session_token UK
    }
    orders {
      uuid id PK
      uuid customer_id FK "null for guests"
      text guest_email "stored lowercase"
      text status
      bigint total_kobo
      char currency "NGN"
      text paystack_reference UK
      bool needs_review
      text review_reason
    }
    order_items {
      uuid id PK
      uuid order_id FK
      uuid variant_id FK
      int qty
      bigint unit_price_kobo "copied at purchase"
      text name_snapshot
    }
    reservations {
      uuid id PK
      uuid order_id FK
      uuid variant_id FK
      int qty
      timestamptz expires_at
      timestamptz released_at "null = active"
    }
    idempotency_keys {
      text key PK
      text request_hash
      text status "in_progress|done"
      jsonb response_body
      uuid order_id FK
    }
    payment_events {
      text paystack_event_id PK "event:data.id"
      uuid order_id FK
      text type
      jsonb payload
      timestamptz processed_at
    }
    rate_limits {
      text key PK "e.g. checkout:ip:1.2.3.4"
      timestamptz window_start PK
      int count
    }
    outbox_jobs {
      bigint id PK
      text type
      jsonb payload
      text status "queued|running|done|dead"
      int attempts
      timestamptz run_after
      timestamptz locked_until "worker lease"
      text last_error
    }
```

**Invariants that tests check:**
- `0 <= stock_reserved <= stock_on_hand` for every variant (enforced by `CHECK` constraints, so a bug fails loudly instead of overselling).
- `stock_reserved` = the sum of `qty` over that variant's reservations where `released_at IS NULL`.
- `order_items.unit_price_kobo` is never read from `products` after the order is created.

## 3. Order state machine

```mermaid
stateDiagram-v2
    [*] --> pending_payment
    pending_payment --> paid : charge.success, amount + currency match
    pending_payment --> cancelled : customer/admin cancels
    pending_payment --> expired : hold passes expires_at
    expired --> paid : late payment, re-reserve succeeds
    expired --> refund_pending : late payment, out of stock
    cancelled --> refund_pending : payment arrives after cancel
    paid --> fulfilled
    paid --> refund_pending : admin refund
    refund_pending --> refunded : Paystack confirms refund
    fulfilled --> [*]
    refunded --> [*]
    cancelled --> [*]
```

All moves go through `orders.state.transition(order, to_state)` on an order locked with `lock_order()` (`SELECT ... FOR UPDATE`); any move not shown here raises `InvalidTransition`. The webhook, the callback check and the expiry job all use it, so they can never race each other into an invalid state.

## 4. Checkout sequence: checkout → Paystack → webhook → paid

```mermaid
sequenceDiagram
    autonumber
    actor U as Browser
    participant N as Next.js (/api proxy)
    participant A as FastAPI
    participant D as Postgres
    participant P as Paystack
    participant W as Worker

    U->>N: POST /api/checkout (cookie, Idempotency-Key)
    N->>A: forward
    A->>D: BEGIN
    A->>D: INSERT idempotency_keys (in_progress, request_hash)
    Note over A,D: key exists → done: return stored response<br/>in progress: 409 + Retry-After<br/>different hash: 422
    A->>D: release expired holds on these variants
    A->>D: UPDATE variants SET stock_reserved += q<br/>WHERE on_hand - reserved >= q (variant ids in sorted order)
    alt any UPDATE touches 0 rows
        A->>D: ROLLBACK
        A-->>U: 409 out_of_stock (which variant)
    end
    A->>D: INSERT order (pending_payment), order_items (prices copied),<br/>reservations (expires_at = now + 15 min)
    A->>D: COMMIT
    A->>P: POST /transaction/initialize (email, amount kobo, reference, callback_url)
    Note over A,P: called AFTER commit: never keep a DB<br/>transaction open across a network call
    P-->>A: authorization_url
    A->>D: idempotency_keys → done, store response
    A-->>N: 200 {order_id, authorization_url}
    N-->>U: 200
    U->>P: redirect, pay with test card

    par Webhook (source of truth)
        P->>A: POST /payments/webhook (x-paystack-signature)
        A->>A: HMAC-SHA512(raw body, secret key) check → 401 if forged
        A->>D: BEGIN
        A->>D: INSERT payment_events ON CONFLICT DO NOTHING
        Note over A,D: 0 rows = repeat → COMMIT, 200, stop
        A->>D: SELECT order FOR UPDATE
        A->>A: type = charge.success? amount + currency match?
        alt mismatch
            A->>D: needs_review = true, admin alert job
        else order pending_payment
            A->>D: stock_on_hand -= q, stock_reserved -= q,<br/>reservations released, order → paid, outbox jobs
        else order expired (late payment)
            A->>D: re-reserve: success → paid · none left → refund_pending + refund job
        end
        A->>D: COMMIT
        A-->>P: 200
    and Customer returns
        P-->>U: redirect to /checkout/callback?reference=…
        U->>N: GET /api/orders/by-reference/{ref}
        N->>A: forward
        A->>P: GET /transaction/verify/{ref} (only if still pending)
        A->>D: same apply_successful_charge() as the webhook
        A-->>U: order status
    end

    W->>D: claim job (FOR UPDATE SKIP LOCKED)
    W->>W: send confirmation email (Idempotency-Key = job id)
    W->>D: job → done
```

**Key points:**
1. **Steps 3–12 are one transaction.** Either the stock is held *and* the order exists, or neither happens.
2. **The conditional `UPDATE` does all the concurrency work.** Postgres takes a row lock, so 200 buyers racing for 10 units queue up on that row and only 10 `UPDATE`s match. Variants are updated in sorted id order so that two multi-item carts can't deadlock.
3. **The redirect is never trusted.** The callback page either reads state the webhook already wrote, or calls Paystack's verify API itself. Both paths call the same idempotent `apply_successful_charge()`, so it doesn't matter which one arrives first.
4. **Only a forged signature gets a non-200.** Paystack retries anything else, so an unexpected event is recorded and flagged instead of rejected.
5. **Late payments are handled.** An expired hold no longer counts as reserved stock, so the webhook re-reserves the stock, or refunds if it's gone ([ADR-003](adr/003-inventory-reservation.md)).

## 5. Cross-cutting

- **Request IDs:** Next.js sets `X-Request-ID` if it is missing. The API logs it on every line and copies it into each outbox job's payload, so one checkout can be traced from browser to email.
- **Logs:** JSON lines (`ts, level, request_id, module, event, ...`). No card data ever reaches us (Paystack hosts the payment page), and emails are masked in logs.
- **Rate limits (Postgres, ADR-006):** checkout and assistant, per IP and per customer, as fixed-window counters in an `UNLOGGED` table (one upsert per request). The assistant's daily spend cap is a normal (logged) row, because it is money.
- **Caching:** product pages use Next.js ISR, revalidated when a product changes. Catalog API responses use an in-process TTL cache (60 s), cleared in the same process when admin edits a product. This is correct while there is one API instance; ADR-006 says what changes at two.
- **Config:** everything comes from environment variables (see [`.env.example`](../.env.example)). The brand name is one variable.
