# ADR-001: One app split into modules, not microservices

- **Status:** accepted (2026-10-12)

## Context
One developer, one database, demo-level traffic. The interesting problems (overselling, repeated webhooks, job retries) are about data correctness, not scale.

## Decision
A single FastAPI app split into modules: `catalog, cart, orders, payments, accounts, admin, search, assistant`.
- Each module owns its tables (see the ownership table in ARCHITECTURE.md). Other modules call its service functions and never query its tables directly.
- Imports between modules go through `module/service.py` only. A lint rule (`import-linter`) enforces this in CI from Phase 1.
- The worker is the same codebase started with a different entry point.

## Consequences
- **Good:** checkout can hold stock, create the order and queue jobs in one ACID transaction. With services split apart, that would need a saga plus compensating actions.
- **Good:** one deploy, one set of logs, and no service-to-service network failures to handle.
- **Bad:** everything scales together, and one bad deploy takes down everything. Acceptable at this size.
- **Way out:** because tables and interfaces are already separated, any module (most likely `assistant`, which has very different load and latency) can be split out later behind the same interface.

## Alternatives rejected
- **Microservices:** distributed transactions, extra deploys and network failure modes, with no benefit at one developer and low traffic.
- **No module rules ("just a Flask app"):** fast at first, but nothing stops `catalog` code from changing stock and breaking the inventory rules in ADR-003.
