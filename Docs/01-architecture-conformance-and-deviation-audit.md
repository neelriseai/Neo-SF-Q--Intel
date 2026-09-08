# A01 — Architecture Conformance and Implementation Deviation Audit

## 1. Purpose

Establish the actual state of the ~70% implementation before code remediation. This audit compares the repository with the production design and L01-L14, identifies shortcuts and boundary violations, and produces a prioritized register. It is read-only until the report is reviewed.

This document is a procedure and template. It does not claim that any specific repository item is defective until the real code, configuration and runtime evidence are inspected.

## 2. Audit authority and rules

- Architecture authority: production design v1.1, integration master and approved ADRs.
- Code, migrations, tests, deployed config and runtime traces are implementation truth.
- Chat summaries, project indexes and AI-maintained knowledge are discovery aids, not proof.
- Do not fix findings while inventorying; otherwise the baseline becomes unreliable.
- Preserve user/team changes. Use a tagged branch/build or immutable archive for the baseline.
- Every conclusion records file/symbol/config/test/runtime evidence.

## 3. Required outputs

```text
docs/implementation-status/
├── IMPLEMENTATION_DEVIATION_REGISTER.md
├── CAPABILITY_CONFORMANCE_MATRIX.md
├── DETERMINISTIC_LLM_BOUNDARY_AUDIT.md
├── ADAPTER_AND_PERSISTENCE_REALITY.md
├── TEST_EVAL_OBSERVABILITY_COVERAGE.md
├── PRODUCT_FEATURE_CLASSIFICATION.md
└── REMEDIATION_BACKLOG.md
```

## 4. Status taxonomy

| Status | Meaning | Promotion consequence |
|---|---|---|
| `COMPLETE` | Implemented, integrated and proven by applicable tests/runtime evidence | None |
| `PARTIAL` | Material path exists but contract, failure behavior or proof is incomplete | Must be remediated or explicitly scoped out |
| `MOCKED` | Mock returns controlled behavior behind a typed port | Allowed only in declared fixture profile |
| `FIXTURE_ONLY` | Uses representative static source but not a live adapter | Must be labelled; allowed for hackathon |
| `HARDCODED` | Business result or connector response bypasses intended computation | P0 if present in demo decision path |
| `SIMULATED` | Execution/output is generated without the claimed external action | Must be visibly labelled; cannot satisfy live gates |
| `NOT_PERSISTED` | State exists only in process/memory/file not meeting contract | P0 for run/evidence/approval/decision state |
| `NOT_INTEGRATED` | Component exists but production path does not invoke it | Treat capability as absent |
| `BYPASSES_DESIGN` | Crosses forbidden boundary or assigns truth to wrong owner | P0/P1 by effect |
| `NOT_IMPLEMENTED` | No usable implementation | Backlog or scope decision |
| `UNKNOWN` | Evidence insufficient to classify | Investigation required; never counted complete |

## 5. Feature classification

Separately classify product/code so remediation does not erase useful IP.

| Class | Rule |
|---|---|
| `CORE_INVARIANT` | Product identity or safety truth; change requires ADR and approval |
| `PROTECTED` | Valuable differentiator or non-obvious capability; preserve unless impact-approved |
| `EXPERIMENTAL` | Deliberate trial; isolate behind feature/config flag |
| `ACCIDENTAL_COMPLEXITY` | Adds cost without product value; candidate simplification |
| `DEAD` | Unused/replaced and safe to remove after reference proof |

## 6. Audit passes

### Pass 1 — Repository and runtime map

Inventory:

- applications, packages, public entry points and dependency direction;
- contracts/schemas and generated artifacts;
- agents, prompts, skills and model configurations;
- ports, adapters, mocks, fixtures and connector credentials/configuration;
- persistence models/migrations/repositories/checkpoints/outbox;
- graph schema, ingestion and rebuild process;
- policies, approvals, audit and guardrail code;
- tests, evals, datasets, expected outputs and reports;
- telemetry initialization, dashboards, alerts and runbooks;
- CI/local verification and environment profiles.

Evidence examples: import graph, routes, call graph, schema list, DB migration list, configuration dump with secrets redacted, test collection, and one runtime trace.

### Pass 2 — Capability traceability

For each capability map:

```text
requirement
→ design component/layer
→ public contract
→ implementation entry point
→ data store/adapters
→ unit/contract/integration test
→ eval case/metric
→ observability span/metric
→ guardrail/policy
→ runtime evidence
→ remaining gap
```

Minimum capabilities: intake, source diff, ingestion, graph, impact, security, risk, coverage, test selection, automation inventory, repair, generation, validation, execution, RCA, release, approvals, REST/UI/MCP and feedback.

### Pass 3 — Hardcode/mock search

Search for and inspect, without assuming every occurrence is a defect:

- literal Salesforce canonical keys, component lists, impacted nodes and risk scores;
- static test recommendations and release codes;
- fixture data selected by normal production entry points;
- `TODO`, `pass`, `NotImplemented`, stub/default returns and catch-all exceptions;
- in-memory dictionaries/singletons used for durable state;
- fake graph queries or manually assembled evidence paths;
- fixed model outputs or bypass flags;
- environment conditionals that silently downgrade to mock/simulation;
- direct DB/LLM/Salesforce SDK construction inside agents/domain engines;
- validation/authorization disabled for demo mode;
- broad tool/shell/URL capabilities;
- write/deploy actions reachable without policy and approval.

### Pass 4 — Deterministic versus semantic ownership

| Task | Required owner | Audit failure example |
|---|---|---|
| Git diff/hash/parser | Deterministic | Model summarizes a diff and creates change truth |
| Graph traversal/entity existence | Deterministic | Model invents or selects confirmed impacted component |
| Security permission path | Deterministic | Prompt decides access/control truth |
| Risk/coverage/test selection | Deterministic | Score or mandatory tests produced by prose response |
| Release code | Deterministic | LLM selects `GO` |
| Requirement normalization | Semantic proposal + schema/review | Unvalidated prose becomes rule truth |
| Ambiguous mapping | Semantic proposal + evidence/review | Inference stored as confirmed edge |
| Code/RCA/explanation | Bounded semantic + independent validation | Proposal treated as executed/verified fact |

### Pass 5 — Data and persistence reality

Verify:

- which repository implementation each profile actually binds;
- whether restart preserves run, checkpoint, approval, evidence and decision state;
- whether DB migrations match models and are invoked in setup;
- whether writes are transactional at required boundaries;
- whether artifact/evidence IDs resolve and hashes match;
- whether graph rebuild is deterministic and snapshot activation transactional;
- whether semantic/vector storage is optional rather than a source of truth;
- whether retention, deletion and backup/restore paths exist.

### Pass 6 — AI assurance coverage

For every semantic task record:

- prompt/version/hash and input schema;
- model/provider/configuration;
- context compiler and evidence allowlist;
- structured output validation;
- abstention and invalid-output behavior;
- tool exposure and authorization;
- trace coverage and content-redaction policy;
- offline cases/metrics/thresholds;
- production feedback and regression route.

## 7. Deviation record schema

```yaml
deviation_id: DEV-0001
capability: impact_analysis
severity: P0
status: HARDCODED
design_reference: L04/Impact analysis
implementation_evidence:
  files: []
  symbols: []
  tests: []
  runtime_trace_ids: []
observed_behavior: ""
expected_behavior: ""
deterministic_or_semantic_owner: deterministic
user_or_release_risk: ""
protected_feature_impact: none
recommended_remediation: ""
acceptance_evidence: []
owner: ""
target_iteration: ""
```

## 8. Severity and priority

| Priority | Criteria | Examples |
|---|---|---|
| P0 | Can make evidence, security, execution or release claims false/unsafe | Hardcoded impact, LLM release code, unapproved write, fake live execution |
| P1 | Blocks reproducibility, durability or meaningful quality measurement | No DB checkpoint, no golden corpus, no claim validation, stale graph |
| P2 | Limits performance, operability or extensibility without false assurance | Missing cost dashboard, limited adapter capability |
| P3 | Documentation/clean-up or deferred scale need | Optional vector tuning, non-critical UI refinement |

Remediate by risk and dependency, not by the number of files or apparent coding speed.

## 9. Capability conformance matrix template

| Capability | Contract | Real path | Fixture path | Persistence | Unit | Contract | Integration | Eval | Trace | Guardrail | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Requirement normalization |  |  |  |  |  |  |  |  |  |  |  |
| Impact analysis |  |  |  |  |  |  |  |  |  |  |  |
| Automation repair |  |  |  |  |  |  |  |  |  |  |  |
| RCA |  |  |  |  |  |  |  |  |  |  |  |
| Release decision |  |  |  |  |  |  |  |  |  |  |  |

Complete for all capabilities listed in Section 6.

## 10. Remediation ordering algorithm

1. P0 safety/truth violations.
2. L01 contracts and forbidden dependency violations.
3. Real persistence/checkpoint/evidence integrity.
4. Adapter truth and fixture/live/simulation labelling.
5. Observability and trace reconstruction.
6. Golden dataset and evaluators.
7. Guardrail and claim-verification enforcement.
8. Workflow reliability and human oversight.
9. Remaining functional backlog.
10. Optional embeddings, additional models/adapters and fine-tuning experiments.

## 11. Audit-specific tests

- Forbidden import/dependency architecture tests.
- Static scan for known hardcoded decision outputs.
- Configuration test proving profile-to-adapter bindings.
- Restart test for durable entities.
- Fixture/live contract parity.
- Graph rebuild/hash reproducibility.
- Trace coverage assertion for every material workflow step.
- Capability endpoint test that rejects mislabeled simulated output.
- Evidence reference resolution test.
- Prompt/tool inventory diff against approved manifests.

## 12. Definition of done

- Every public capability has one row with evidence-backed status.
- Every P0/P1 deviation has an owner, target and acceptance proof.
- Deterministic tasks are not delegated to LLMs.
- All mocks/fixtures/simulations are behind explicit adapters and visible capability states.
- Real database, LLM and Salesforce connector status is stated accurately.
- Product invariants/protected features are recorded before remediation.
- The audit is reviewed before any batch auto-remediation request is given to a coding assistant.

## 13. Safe coding-assistant audit instruction

> Pause feature development. Treat the production solution design, integration master, L01-L14 and approved ADRs as architecture authority. Perform a read-only repository and runtime audit using A01. Create the required reports and populate the Implementation Deviation Register with file, symbol, test and trace evidence. Identify hardcoded, mocked, fixture-only, simulated, non-persistent, non-integrated and design-bypassing behavior. Separately identify deterministic responsibilities incorrectly delegated to an LLM. Do not modify code, prompts, configuration, migrations or tests during the audit. Do not classify a capability complete without applicable runtime/test evidence.
