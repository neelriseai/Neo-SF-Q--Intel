# L05 — Risk, Coverage and Test Selection

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Convert impact/security facts into reproducible risk, validation obligations, coverage gaps and the minimum sufficient test set |
| Owner | QE/decision engineer |
| Inputs | Impact/security reports, graph/test history and policy bundle |
| Outputs | `RiskReport`, `CoverageReport`, `TestPlan` |

## Part A — Solution design

### Risk model

Default weighted factors:

| Factor | Weight |
|---|---:|
| Business/control criticality | 0.18 |
| Blast radius | 0.15 |
| Security sensitivity | 0.18 |
| Data integrity | 0.12 |
| Integration impact | 0.10 |
| Change complexity | 0.08 |
| Coverage gap | 0.12 |
| Historical failure | 0.04 |
| Evidence uncertainty | 0.03 |

`InherentRisk = round(100 × Σ(weight × normalized factor))`. Verified control credit is capped at 20; `ResidualRisk = max(0, InherentRisk - ControlCredit)`.

Policy overrides supersede numeric risk. Critical security bypass, stale evidence, contradictory facts or mandatory-test failure cannot be averaged away.

### Coverage obligations

- Business rules and acceptance criteria.
- Positive, negative and boundary values.
- Security/control and permission paths.
- Changed Salesforce components.
- Integration contracts.
- Historical incident regressions.
- Automation-maintenance and locator stability obligations where applicable.

Mappings record evidence strength: explicit assertion, observed execution, declared mapping or semantic inference.

### Test selection

1. Pin all policy-mandatory tests.
2. Construct uncovered obligation universe.
3. Rank remaining tests by new weighted coverage × reliability divided by execution cost + flakiness penalty.
4. Respect test-layer ordering, prerequisites and concurrency.
5. Return uncovered obligations as `TestSpecification` items.
6. Explain selected and excluded suites.

### Test-layer policy

- Apex for code/Flow/service boundaries and Salesforce-native behaviour.
- API/business-process for rule/data/control behaviour without unnecessary UI cost.
- UI for user-facing navigation/rendering/locator/critical journey obligations.
- Permission-negative tests for access/control changes.
- Full regression only when blast radius/evidence/criticality justifies it.

## Part B — Development notes

### Repository

```text
packages/risk/
packages/coverage/
packages/test_selection/
policies/risk/
policies/coverage/
policies/test_selection/
```

### Implementation guidance

- All formulas and thresholds are versioned and code-reviewed.
- Keep factor calculation functions pure and explainable.
- Preserve raw factor values and evidence IDs in reports.
- Use deterministic weighted set cover; do not ask an LLM to select the final set.
- LLM may explain why a test is selected or formulate missing scenario prose.

### Development order

1. Obligation model and coverage mapping strengths.
2. Risk factor calculators and overrides.
3. Mandatory test rules.
4. Weighted set-cover selector.
5. Historical outcome/flakiness inputs.
6. Explanation adapter.

## Part C — Testing and definition of done

### Tests

- Factor boundaries 0/1 and weight sum.
- Low/medium/high/critical thresholds.
- Override precedence and stale-evidence behaviour.
- Obligation creation for positive/negative/boundary/control cases.
- Mapping-strength eligibility.
- Set-cover optimality on small known cases and determinism on ties.
- Mandatory test cannot be optimized away.
- Flakiness/cost affects only non-mandatory selection.
- Full-regression inclusion/exclusion explanations.

### Definition of done

- Same inputs/policy version yield identical risk, coverage and selection.
- All factor values and overrides link to evidence.
- Strategic-discount scenario creates 10% boundary, permission-negative and API/business obligations.
- Selector chooses the agreed minimum Apex/API/UI/permission set and exposes uncovered scenarios.
- Critical/missing mandatory obligations prevent downstream `GO`.
- Policy change is versioned and regression-tested against golden releases.
- Contract and eval tests pass.

### Integration handoff

- Publish policy bundle/version and factor/obligation fixtures.
- Provide selected/excluded test rationale for demo.
- List mandatory assumptions consumed by L06/L08.
