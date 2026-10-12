# ADR-006: No Redis. Postgres + Next.js caching until a measurement says otherwise

- **Status:** accepted (2026-10-12). Replaces the original plan to use Upstash Redis.

## Context
Redis was planned for two jobs: a catalog cache and rate limits. The store has about 150 products, demo-level traffic and a single API instance on a free tier. Every extra service adds an account, keys, a failure mode and a sync problem.

## Decision
- **Page cache:** Next.js ISR caches product and category pages. A product change triggers on-demand revalidation (`revalidateTag`) through an outbox job.
- **API cache:** an in-process TTL cache (60 s) for catalog list/detail responses. Admin product edits clear it in the same process. Postgres also keeps the small catalog in its own memory, so cache misses are still fast.
- **Rate limits:** fixed-window counters in an `UNLOGGED` table:
  ```sql
  INSERT INTO rate_limits (key, window_start, count) VALUES (:key, :window, 1)
  ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limits.count + 1
  RETURNING count;
  ```
  `UNLOGGED` skips Postgres's crash-recovery log: writes are faster, and a crash resets the counters, which is acceptable for rate limits. A worker job deletes old windows.
- **Assistant spend cap:** a normal (logged) Postgres row, because it tracks money.

## Consequences
- **Good:** one data store. Fewer accounts, keys and things that can go down. The "everything correct lives in one transaction" story holds without exceptions.
- **Bad:** each rate-limited request does one small write (about 1–3 ms). Negligible at this traffic, and it's measured in Phase 8.
- **Bad:** the in-process cache isn't shared. With **two or more API instances**, a product edit only clears the cache on the instance that handled it, so other instances serve stale data for up to 60 s. **That's when Redis (or a Postgres `LISTEN/NOTIFY` cache-clear broadcast) becomes worth adding.**
- **When to revisit:** if the Phase 8 load test misses catalog p95 < 200 ms, or if the API runs more than one instance. The decision and the numbers go in `evals/RESULTS.md`.

## Alternatives rejected
- **Upstash Redis (original plan):** free and capable, but nothing measured needs it yet, and it's one more service to secure and explain.
- **In-memory rate limits:** zero cost, but reset on every deploy or sleep, and not shared between instances.
