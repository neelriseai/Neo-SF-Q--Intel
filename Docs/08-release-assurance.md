# L08 — Release Assurance

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Apply deterministic, versioned release policy to immutable evidence and produce `GO`, `CONDITIONAL_GO` or `NO_GO` recommendation |
| Owner | Release/QE lead |
| Inputs | Impact/security/risk/coverage/test/automation/execution/RCA facts and approvals |
| Output | `ReleaseDecision` |

## Part A — Solution design

### Decision principles

- Recommendation is deterministic; LLM may explain but never select the code.
- Human release authority remains accountable in pilot/production.
- Decision references immutable snapshots and expires when any referenced input changes.
- Missing evidence is incompleteness, not low risk.

### Default `NO_GO` blockers

- Required test failed.
- Critical security/control gap unresolved.
- Mandatory obligation uncovered.
- Required generated/repaired asset below required maturity.
- Stale/corrupt/contradictory evidence.
- Required approval rejected/expired.
- Execution used unapproved scope/identity.

### `CONDITIONAL_GO`

No blocker exists, but simulation/environment unavailability, high inferred impact, inconclusive non-blocking test, approved high residual risk or monitoring/rollback/remediation condition remains.

### `GO`

All mandatory obligations covered, required tests passed, no unresolved High/Critical security issue, evidence completeness threshold met, residual risk within limit, snapshots current and mandatory approvals present.

### Decision payload

Recommendation, code, blocking reasons, conditions, unresolved items, residual risk, evidence IDs, policy bundle, source/graph/automation/execution versions, expiry and human authority status.

## Part B — Development notes

### Repository

```text
packages/release/
├── service.py
├── policy.py
├── gates.py
└── explanation.py
policies/release/
```

### Implementation guidance

- Encode gates as versioned data and pure functions.
- Evaluate blockers before score/conditions.
- Preserve every gate result in the report.
- Explanation uses only decision facts/evidence IDs.
- Human override creates a separate `HumanDecision`; never mutate original recommendation.

### Development order

1. Fact completeness validator.
2. Blocking gates.
3. Conditional/GO criteria.
4. Expiry/invalidation.
5. Explanation and human override workflow.

## Part C — Testing and definition of done

### Tests

- One case per blocker and combinations.
- Simulation cannot satisfy default live gate.
- Stale evidence and expired approvals.
- High inferred-only relationship.
- Invalid automation maturity.
- GO/CONDITIONAL/NO-GO golden cases.
- Input/version change invalidates decision.
- Explanation cannot alter code or cite unknown evidence.

### Definition of done

- All decisions are reproducible from persisted facts and policy version.
- Every reason/condition links to evidence or approval.
- Failed/missing mandatory test and invalid mandatory automation produce `NO_GO`.
- Simulation/environment gaps produce policy-correct result.
- Human override is separate, authorized and audited.
- Decision expires/invalidate correctly.
- L14 agreement/unsupported-claim thresholds pass.

### Integration handoff

- Publish release policy version and decision truth table.
- Provide golden decisions to L09/L11/L14.
- List roles allowed to accept conditions/override recommendation.
