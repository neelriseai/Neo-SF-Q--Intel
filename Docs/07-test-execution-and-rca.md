# L07 — Test Execution and Root-Cause Analysis

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Execute the approved validation plan safely and diagnose failures using change, graph, automation and historical evidence |
| Owner | Execution/RCA engineer |
| Inputs | Approved `TestExecutionPlan`, validated assets, environment capability and evidence context |
| Outputs | `ExecutionReport`, `RCAReport` |

## Part A — Solution design

### Execution service

- Capability discovery by adapter/environment.
- Immutable plan and automation snapshot.
- External execution IDs and reconciliation.
- Per-layer ordering: compile/discovery → Apex/API → targeted UI where policy requires.
- Simulation explicitly labelled; simulation cannot satisfy live proof.
- Result/log/artifact capture by hash.

### Safety

- Separate read and execute identities.
- Test data creation requires sandbox policy/approval.
- Non-idempotent unknown outcomes are reconciled, not blindly retried.
- Timeouts become `INCONCLUSIVE` with retained external ID.
- Execution never applies source patches or deploys metadata.

### RCA classification

```text
PRODUCT_DEFECT
AUTOMATION_DEFECT
LOCATOR_DRIFT
TEST_DATA_DEFECT
ENVIRONMENT_DEFECT
FLAKY_OR_TIMING
UNKNOWN
```

RCA correlates changed components, evidence paths, automation patch/source, logs, stack traces, external status, prior failure signatures and incidents. It returns ranked hypotheses with supporting and contradicting evidence plus next diagnostic action.

### Feedback loop

- Automation-class RCA may request a bounded L06 repair.
- Product defects remain release blockers; L06 must not “heal” tests to accept wrong product behaviour.
- Accepted RCA outcome becomes evaluation/outcome memory after human confirmation.

## Part B — Development notes

### Repository

```text
packages/execution/
├── service.py
├── planner.py
├── reconciliation.py
└── artifacts.py
packages/rca/
├── service.py
├── classifier.py
├── evidence_builder.py
└── failure_signatures.py
```

### Implementation order

1. Simulation/fixture execution adapter.
2. External ID/status/reconciliation model.
3. Result normalization and artifact capture.
4. Deterministic failure classification features.
5. Evidence pack and semantic RCA synthesis.
6. Live Apex/API/UI adapters and stability runs.

### Development rules

- Use `TestPort`; never embed vendor execution SDK in RCA/domain code.
- Store commands/config by hash; never log secrets.
- Semantic RCA can rank/explain but cannot change raw execution status.
- Bound log/context size and preserve exact artifact links.

## Part C — Testing and definition of done

### Tests

- Pass/fail/skip/inconclusive/timeout/cancel normalization.
- External unknown outcome and reconciliation.
- Duplicate execution request/idempotency.
- Credential/authorization denial.
- Simulated versus live result policy.
- Seeded product, automation, locator, data, environment and flaky failures.
- RCA top-1/top-3 accuracy and unsupported-claim cases.
- Repair-loop attempt bounds and loop termination.

### Definition of done

- Approved plan executes or simulates with immutable external/artifact evidence.
- Timeout/unknown outcomes do not duplicate side effects.
- Raw results remain distinct from RCA interpretation.
- Seeded product and automation failures are correctly distinguished.
- Every hypothesis has supporting/contradicting evidence and next action.
- Automation repair feedback cannot mask a confirmed product defect.
- L08 receives complete terminal execution facts or explicit incompleteness.

### Integration handoff

- Publish adapter capability matrix and execution policy.
- Provide normalized result/RCA fixtures to L08/L14.
- Document live-environment prerequisites and reconciliation procedures.
