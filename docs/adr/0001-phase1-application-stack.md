# ADR 0001: Phase 1 application stack

**Status:** Accepted  
**Date:** 2026-10-27

## Context

The repository contained only product and architecture documentation. The first implementation needs a runnable full-stack application, a durable database-backed workflow, safe local publishing behavior, and tests without requiring platform credentials.

## Decision

- Use FastAPI and SQLAlchemy 2 for the API/domain boundary, Alembic for database migrations, and PostgreSQL in Docker Compose.
- Use a Next.js App Router dashboard for the local product workflow.
- Use an independently runnable worker polling durable publish jobs in the relational database for this initial slice.
- Implement authenticated bearer sessions using random opaque tokens stored as hashes and Argon2 password hashes.
- Keep publishing mock-only. Persist mock provider idempotency results alongside the application data and revalidate the approval and approver immediately before the mock provider operation.
- Keep local SQLite available for quick development and tests; production/container configuration uses PostgreSQL.

## Consequences

- The first slice runs without third-party credentials and can exercise the approval invariant end to end.
- The worker is intentionally simple and database-polled; queue throughput, distributed locking, backoff, dead-letter operations, and production-scale orchestration need hardening before launch.
- External integrations remain disabled until official API capabilities, OAuth, encrypted credential storage, contract tests, and reconciliation are implemented.
- The local authentication flow is suitable for the starter workflow only and requires a maintained identity provider, account recovery, and abuse controls before production.
