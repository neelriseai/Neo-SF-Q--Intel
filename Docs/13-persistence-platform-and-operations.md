# L13 — Persistence, Platform and Operations

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Provide durable run/checkpoint/data/artifact storage, reliable workers, observability, SLOs, backup/recovery and operating procedures |
| Owner | Platform engineer |
| Inputs/outputs | Repository/checkpoint/artifact/audit/outbox ports and operational signals |

## Part A — Solution design

### Deployment profiles

- Hackathon: FastAPI/worker, SQLite/JSONL, local content-addressed artifacts, fixture adapters.
- Pilot: stateless API replicas, PostgreSQL HA, Postgres job queue, worker replicas, approved object store and observability.
- Scale later: managed broker/autoscaling/multi-tenant keys only after measured need.

### Persistence

Core tables: project/snapshot/ingest, graph/evidence/artifact, assurance run/checkpoint/output, approval, automation asset/patch/validation/locator history, execution, policy/prompt/eval/feedback, outbox and audit.

The current local PostgreSQL adapters isolate every Neo-owned table and LangGraph checkpoint table
inside the validated `POSTGRES_SCHEMA` (default `neo_sf_q_intel`). Connections have no `public`
search-path fallback. An ownership/layout marker must exist before migrations; a non-empty unmarked
schema is rejected, and similarly named objects in other schemas are never adopted or altered.

Live receipt replay uses a separate fail-closed availability chain: the owned PostgreSQL schema is
preferred, and an auto-created byte-preserving SQLite ledger is the durable fallback. If neither
ledger can be initialized (including a corrupt or unwritable SQLite file), API health remains
available with `LIVE_RECEIPT_LEDGER_UNAVAILABLE`, while live campaign status returns a sanitized
503 and remains release-ineligible. Process memory is never used as live-acceptance authority.

### Transactions and workers

- Run/idempotency reservation in one transaction.
- Workers claim jobs with `FOR UPDATE SKIP LOCKED` and renewable lease.
- Step output + checkpoint + outbox atomic.
- Optimistic version for approvals/patch writeback.
- Ingest activation transactional.
- At-least-once outbox; consumers deduplicate by event ID.

### Resilience

Per-adapter deadlines/circuit breakers, bounded safe retries, external-ID reconciliation, durable checkpoints, workload bulkheads, project concurrency/backpressure and graceful degradation.

### Pilot SLOs

| Indicator | Target |
|---|---:|
| API availability | 99.5% monthly |
| Read p95 | <500 ms |
| Run acceptance p95 | <2 s |
| Warm static analysis p95 | <120 s for ≤200 changed components |
| Patch generation p95 | <180 s for ≤10 assets, excluding builds/tests |
| RPO/RTO | ≤15 min / ≤4 h pilot |

## Part B — Development notes

### Repository

```text
packages/platform/
├── repositories/
├── jobs/
├── checkpoints/
├── outbox/
└── telemetry/
ops/
├── dashboards/
├── alerts/
└── runbooks/
```

### Implementation order

1. SQLite repositories and local artifact store.
2. Durable run/checkpoint/outbox interfaces.
3. Postgres repositories/job leasing.
4. Structured logging, metrics and traces.
5. Alerts/runbooks/backups/restore.
6. Load/failure/soak and HA exercises.

### Operational metrics

Run/step status/duration/retry; index freshness; graph integrity; evidence completeness; model tokens/errors; automation maturity/compile/discovery/acceptance/false-heal; execution/flakiness; decision distribution/override; auth denials/approval age.

### Engineering rules

- Logs exclude credentials and unrestricted source/record/model content.
- Migration uses expand/migrate/contract.
- Schema creation uses identifier-safe composition; configurable schema names are lowercase private
  identifiers and cannot select `public`, catalog, or `pg_*` namespaces.
- API instances remain stateless.
- Object artifacts are content-addressed/encrypted and referentially checked.
- Unknown external side effects are reconciled before retry.

## Part C — Testing and definition of done

### Tests

- Repository contract parity SQLite/PostgreSQL.
- Transaction rollback, outbox dedupe and lease expiry.
- Worker kill/restart at each checkpoint.
- Queue backpressure/project limits.
- DB/artifact timeout and circuit breaker.
- Migration forward/backward compatibility.
- Backup/restore referential integrity.
- Load/soak/failover and SLO/error-budget calculations.
- Alert routing and runbook exercises.

### Definition of done

- Completed outputs/checkpoints survive restart without duplication.
- Transactional graph/run/patch state remains consistent.
- Dashboards expose agreed SLOs and quality/operational metrics.
- Alerts fire on stuck runs, stale index, eval regression, dependency outage and security events.
- Backup restore reconstructs database/artifact references within RPO/RTO.
- Runbooks are exercised by the operating team.
- Hackathon local profile and pilot profile implement the same repository/port contracts.

### Integration handoff

- Publish environment topology, capacity assumptions and repository migrations.
- Provide health/readiness semantics, dashboards and runbook index.
- Document backup, restore, failover and support ownership.
