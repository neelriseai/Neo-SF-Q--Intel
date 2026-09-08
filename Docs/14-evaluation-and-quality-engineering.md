# L14 — Evaluation and Quality Engineering

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Prove layer and system correctness, measure semantic quality and block regressions across builds, policies, prompts, models, parsers and framework profiles |
| Owner | QE/evaluation owner |
| Inputs | Golden cases, layer outputs, human corrections and release outcomes |
| Outputs | `EvalResult`, quality gates, regression report and production-readiness evidence |

## Part A — Solution design

### Test pyramid

- Unit: parsers, graph, rules, algorithms and validators.
- Contract: producer/consumer and fixture/live port parity.
- Integration: complete fixture flow and failure/recovery.
- Security: auth, prompt/tool/code sandbox and data handling.
- Performance/resilience: load, soak, failure, resume and restore.
- Semantic evaluations: impact, security, RCA and explanations.
- UAT: Salesforce/QE/release stakeholders on known changes.

### Eval case model

Each case pins input requirement/change, source/graph/automation snapshots, expected impacts/rules/security/obligations/tests/repairs/RCA/decision, must-not-claim items and relevant versions.

### Metrics

```text
impact/business-rule precision and recall
unsupported-claim rate
evidence completeness/validity
security precision and critical recall
test-selection precision/reduction
automation mapping/repair precision
compile/discovery/first-pass execution/acceptance rates
locator-heal precision and false-heal rate
post-acceptance flakiness
RCA top-1/top-3 correctness
release-decision agreement and human override
```

### Promotion policy

Any build/model/prompt/policy/ontology/parser/framework-profile change runs targeted plus core golden suites. No promotion if an agreed threshold regresses or a critical must-not-claim case fails.

## Part B — Development notes

### Repository

```text
packages/evaluation/
tests/
├── unit/
├── contract/
├── integration/
├── security/
├── performance/
└── evals/
    ├── cases/
    ├── expected/
    └── reports/
```

### Initial golden cases

- Strategic discount 15% → 10%.
- Exact below/at/above boundaries.
- Permission-negative bypass.
- Flow changed; Apex test unchanged.
- Unrelated Account rule must not be claimed.
- Salesforce/LLM/embedding unavailable degradation.
- Stale index and failed re-ingest.
- Selenium locator/assertion repair.
- Complete Selenium/Apex/API generation.
- Assertion-free test rejection.
- Stale patch head and protected writeback.
- Product defect versus automation defect RCA.
- Test timeout/inconclusive release decision.

### Development rules

- Human-approved expected results only.
- Retain negative/rejected links/repairs as eval data.
- Compare normalized typed payloads, not prose alone.
- Version cases and expected outputs.
- Report metric confidence/sample size; avoid false precision.

## Part C — Testing and definition of done

### Required gates

- Unit/contract pass.
- Graph/index integrity.
- Automation executable validation.
- Security/SBOM/secret/dependency scans.
- Golden semantic thresholds.
- End-to-end fixture and recovery flow.
- Documentation/handoff freshness.

### Definition of done

- Every critical layer has positive, negative, boundary and failure cases.
- Eval runs record build, source/graph, policy, model/prompt and framework-profile versions.
- Unsupported-claim/must-not-claim checks are automated.
- Critical false negatives/false heals block promotion.
- Human correction/override becomes a reviewed candidate eval case.
- Reports show change versus baseline and exact failing examples.
- System meets integration master and production-readiness gates.

### Integration handoff

- Publish eval corpus version, thresholds and latest report.
- Provide per-layer failed-case ownership routing.
- Document UAT sign-offs, open risks and waiver/expiry process.
