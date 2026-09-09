# A7 outcome-memory foundation review ledger

## Capability boundary

- Capability: `memory.release-outcomes`
- Related capabilities: `memory.postgresql`, `memory.resilient-fallback`,
  `governance.release-input-integrity`
- Claim: `FOUNDATION`
- Authority: every stored outcome remains `HISTORICAL_CANDIDATE` and `NON_AUTHORIZING`

The slice implements strict trusted-test, typed incident and unverified human-correction domain
records; module-pinned policy/evaluation replay; append-only PostgreSQL, SQLite, JSON and process
adapters; application-service write/read replay; project/snapshot isolation; classified fallback;
and truthful health. It does not enable release authority or historical-quality claims.

## Independent review iterations

The genericity/architecture lane and governance/evidence lane each blocked the first integration
attempt. The implementation was not promoted by relabeling; every P1 below received a code and
adversarial-test change.

| Finding | Severity | Resolution |
|---|---:|---|
| Caller policy and self-hashed record forgery | P1 | Every adapter now requires an immutable replay request, loads module-pinned outcome/evaluation/governance contracts, and replays the authoritative originating run before storage effects. The service resolves the raw run from run persistence. |
| Repository/domain path-safety drift and echoed project values | P1 | One layer-neutral recursive detector covers credentials, Windows/UNC, known POSIX machine roots and file-URI variants. Service scope errors never echo caller or configured identifiers; HTTPS and root-relative API routes remain accepted. |
| Duplicate evidence, selected tests and correction targets | P1 | Shared receipt validation and typed target validation reject duplicate identities before dictionary/set collapse. |
| Incident ambiguity and unbounded/orphan history | P1 | Targets carry kind, ID and run-derived artifact hash. Lifecycle replay is time-monotonic, duplicate-free, depth-bounded and requires the exact predecessor chain to exist in the active adapter before child effects. |
| JSON cross-project read | P1 | JSON `get` verifies stored lineage against the requested project after loading the content-derived path. |
| Unbounded query-kind iterable | P1 | Query normalization consumes no more than the finite outcome-kind bound plus one rejection item. |
| SQLite handle leak on Windows | P1 | Setup, append, read and query explicitly close connections on success and failure; a move/delete regression proves handle release. |
| PostgreSQL `TRUNCATE` bypass | P1 | Embedded and migration DDL block `UPDATE`, `DELETE` and statement-level `TRUNCATE` on outcome and idempotency tables. |
| Fallback/health overclaim | P1 | Only classified initialization unavailability falls back. Runtime corruption, replay, schema, conflict and ambiguous-write failures remain fail-closed. Unknown adapters are non-durable; health separates liveness and persistence readiness. |

## Verification evidence

- Positive derivation, append, restart, get, query and pagination contracts.
- Negative forged policy/evaluation/time/evidence/target/run/history and cross-project cases.
- Failure/degradation cases for PostgreSQL, SQLite, JSON, process cache, schema corruption,
  idempotency conflicts, ambiguous writes and invalid stored records.
- Scenario-independence coverage for renamed project, snapshot, entity and artifact identities.
- Independent reviewers: `a7_full_genericity_review` and `a7_governance_review`.

## Accepted `FOUNDATION` gaps

| Gap | Owner | Acceptance boundary | Priority |
|---|---|---|---:|
| Live PostgreSQL transaction/concurrency verification | `memory.postgresql` | Run the migration and adapter contract against the configured target-machine PostgreSQL instance | P1 planned |
| Public HTTP/MCP outcome surface | `memory.release-outcomes`, `tools.mcp` | Schema-bound project-scoped append/read/query endpoints delegate to the shared service with negative/failure tests | P1 planned |
| Signed or independently anchored append ledger | `memory.release-outcomes` | Detect out-of-band deletion/substitution independently of the mutable storage owner | P1 planned |
| Historical-policy replay and cross-backend reconciliation | `memory.release-outcomes` | Retain trusted prior policy bundles and reconcile degraded writes without weakening current-policy isolation | P1 planned |
| Decision, override, production and healing outcome kinds | `memory.release-outcomes` | Add typed non-authorizing contracts and reviewed evaluation cases | P1 planned |
| A3/A6 prior-outcome receipts | `reasoning.graph-context-compiler`, `reasoning.graph-grounded-agent` | Context compilation and advisory consumption bind retrieved outcome IDs/digests without promoting authority | P1 planned |
| Adjudicated outcome-quality corpus | `governance.measurement-contract` | At least 20 versioned human-adjudicated cases; current manifest remains `NOT_RUN` with zero passes | P1 planned |
| JSON multi-file transaction | `memory.resilient-fallback` | Current receipt/record files publish atomically but not together; identical retry repairs a dangling receipt and conflicting retry is refused | P2 |

Final result: `APPROVED` by `a7_full_genericity_review` and `a7_governance_review` after both lanes
re-checked the remediated runtime, canonical `FOUNDATION` claim, review coverage and generated
knowledge. No P0 or current-claim P1 remains.
