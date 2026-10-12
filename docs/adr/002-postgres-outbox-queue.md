# ADR-002: Postgres outbox table as the job queue

- **Status:** accepted (2026-10-12)

## Context
A paid order needs follow-up work: a confirmation email, low-stock alerts, sometimes a refund. If the order commits but the job is lost (or the job is queued but the order rolls back), the system is inconsistent. This is the "dual write" problem.

## Decision
Jobs are rows in `outbox_jobs`, inserted **in the same transaction** as the change that caused them.

The worker claims jobs with:
```sql
SELECT id, type, payload FROM outbox_jobs
WHERE status = 'queued' AND run_after <= now()
ORDER BY run_after
LIMIT 10
FOR UPDATE SKIP LOCKED;
```
- It marks the claimed jobs `running` with a lease (`locked_until`). If a worker dies, the lease runs out and another worker reclaims the job.
- Failures are retried with exponential backoff plus jitter (`run_after = now() + 2^attempts s`). After N attempts the job becomes `dead`, which acts as the dead-letter queue, and an admin alert is raised.
- Delivery is **at least once**, so every handler is idempotent: emails pass the job ID as Resend's `Idempotency-Key`, and refunds check Paystack for an existing refund first.
- Scheduled work (expiring holds) is a job that re-queues itself with a later `run_after`.

## Consequences
- **Good:** an order can never exist without its jobs. No extra infrastructure. The queue is visible with plain SQL. Two or more workers are safe because of `SKIP LOCKED`.
- **Bad:** polling adds latency (about 1 s, fine for email), and a busy queue adds load to the main database. At this scale that's negligible. The upgrade path is `LISTEN/NOTIFY` to wake the worker, or moving to a real queue fed by the outbox.
- **Free-tier note:** the worker runs inside the API process. While the Render instance sleeps, jobs run late, but nothing *incorrect* happens, because stock correctness never depends on the worker (ADR-003).

## Alternatives rejected
- **Celery + Redis / RQ:** the dual-write problem comes back (commit the order, then the enqueue fails), and it's another service to run. Redis here is allowed to lose data.
- **FastAPI `BackgroundTasks`:** lost if the process dies, and no retries.
