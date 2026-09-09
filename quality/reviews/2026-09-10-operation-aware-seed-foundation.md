# R0.4d operation-aware semantic-seed review

Date: 2026-09-10

Capability: `source.change-seed-mapping`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

This slice may claim complete local operation-to-semantic-seed mapping for the Salesforce source
families admitted by the pinned R0.4c adapter. It must current-replay R0.4a and R0.4c, bind every
Git change and graph delta, use candidate evidence for ADD, base evidence for DELETE and both sides
for MODIFY, and preserve exact tombstones. It must not claim trusted side-specific path replay,
complete Salesforce-family coverage, upstream live-org capture, verified build/test execution or
release authority.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — ACCEPT after remediation.
- Governance/evidence/safety: `r01_governance_review` — ACCEPT after remediation.

## Findings resolved

- The implementation is a new pinned contract rather than an incompatible mutation of R0.4b.
- Source-artifact exclusion is derived from exact file dispositions; runtime code has no demo
  object, field, alias, route or absolute-path literals.
- ADD, DELETE and MODIFY retain exact Git-operation side semantics. Same-ID base/candidate entities
  are not collapsed for MODIFY. Semantic deletion tombstones are bound even when the Git file is
  modified rather than deleted.
- Every change, graph delta and tombstone is partitioned. Nonsemantic files produce
  `NO_SEMANTIC_SEED`; indirect graph effects remain separately visible.
- Historical roots are checked before refreshed comparison. Current repository/graph replay, aware
  UTC sampling, future/expiry/rollback checks and bounded validity remain fail closed.
- R0.4c refresh comparison excludes recomputed residual `freshness_seconds`, while its model still
  proves that duration equals `valid_until - observed_at`.
- A single owner-to-delta index and pinned total reference limits prevent combinatorial expansion.

## Retained foundation gaps

- Trusted BASE/CANDIDATE R0.2/R0.1 path replay has not consumed these side-qualified seeds.
- Source-family, upstream, build, deployment, test, risk, conflict and approval evidence remains
  incomplete.
- `RELEASE_EVIDENCE_MODEL_INCOMPLETE` remains active; artifacts are `ANALYSIS_ONLY` and
  `release_eligible=false`.

## Verification

- Root focused gate: 49 change-seed, graph-production and operation-seed tests passed.
- Genericity reviewer: 28 operation/graph tests, Ruff and diff check passed.
- Governance reviewer: 7 operation tests and 65 combined related tests passed; all pins recomputed.
- Full repository gate: 654 Python tests, 2 Playwright component tests, 7 dashboard E2E tests,
  TypeScript checks and the Next.js production build passed.
