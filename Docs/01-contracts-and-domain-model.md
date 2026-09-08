# L01 — Contracts and Domain Model

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Provide the only canonical schemas, ports, events and compatibility rules used by all layers |
| Owner | Lead architect |
| Upstream | Product requirements and approved ADRs |
| Downstream | Every implementation layer |

## Part A — Solution design

### Responsibilities

- Define versioned Pydantic models and generated JSON Schemas.
- Define protocols for Salesforce, Git, graph, semantic reasoning, automation repository/framework, locator evidence, test execution, artifact, audit and policy access.
- Define run-state events and normalized error taxonomy.
- Own canonical identifiers, timestamps, enums and compatibility policy.
- Keep vendor SDK objects and framework-specific syntax outside public contracts.

### Core contract groups

| Group | Contracts |
|---|---|
| Intake/change | `AssuranceRequest`, `RequirementSpec`, `ChangeSet`, `SourceSnapshot` |
| Evidence | `EvidenceRef`, `GraphQuery`, `GraphResult`, `IngestBatch` |
| Decisions | `ImpactReport`, `SecurityReport`, `RiskReport`, `CoverageReport`, `TestPlan` |
| Automation | `AutomationAsset`, `AutomationImpactReport`, `AutomationIR`, `AutomationRepairPlan`, `AutomationPatch`, `AutomationValidationReport` |
| Execution | `TestExecutionPlan`, `ExecutionReport`, `RCAReport` |
| Release/governance | `ReleaseDecision`, `Approval`, `PolicyDecision`, `AuditEvent` |
| Workflow | `AssuranceState`, `WorkflowError`, `RunEvent` |

### Identity and version rules

- UUIDv7 for run/evidence/event identifiers.
- Stable canonical keys for project entities, for example `sf:Field:Opportunity.Discount__c`.
- `schema_version` on all externally persisted/exchanged payloads.
- `engine_version` and `policy_version` on deterministic outputs.
- Model/prompt/input hashes on semantic outputs.
- Source/content/base-head hashes on evidence and automation patches.
- UTC ISO-8601 timestamps.

### Compatibility policy

| Change | Rule |
|---|---|
| Add optional field | Minor schema version; old consumers remain valid |
| Add enum value | Minor only if consumers have unknown-value handling; otherwise major |
| Rename/remove/change meaning | Major version and migration |
| Tighten validation | Major unless all retained payloads are proven compliant |
| Internal implementation field | Keep private; do not expose in public schema |

### Invariants

- `ImpactItem`, `SecurityFinding`, `RCAHypothesis` and release reasons contain evidence IDs.
- `AutomationPatch` contains the expected base hash and cannot represent an already-applied change.
- `ReleaseDecision` is immutable and expires when any referenced input changes.
- Errors use stable codes, not vendor exception text.

## Part B — Development notes

### Repository

```text
contracts/
├── models.py
├── enums.py
├── ports.py
├── events.py
├── errors.py
├── schemas/
└── openapi/
```

### Implementation guidance

1. Start with business examples, then model minimum required fields.
2. Keep models serialization-safe and deterministic.
3. Separate write/action requests from read results.
4. Use discriminated unions for source, evidence and execution variants.
5. Generate JSON Schemas and OpenAPI fragments in CI.
6. Maintain representative fixture payloads for every public type.
7. Require an ADR before modifying a published contract.

### Allowed dependencies

- Python standard library, Pydantic and schema tooling.
- No FastAPI route, LangGraph, database ORM, Salesforce SDK, model SDK or framework code.

### Deliverables

- Contract package, generated schemas, event catalogue, error catalogue.
- Compatibility matrix and migration notes.
- Port fakes/mocks for consumer development.
- `LAYER_INDEX.md` and ADR references.

## Part C — Testing and definition of done

### Tests

- Model construction and invalid-payload boundaries.
- Serialization/deserialization round trips.
- JSON-schema conformance for golden payloads.
- Previous-version consumer compatibility.
- Unknown enum/optional-field behaviour.
- Stable hashing and canonical-key normalization.
- Evidence-required property tests.
- Port fake parity with protocols.

### Definition of done

- All public contracts have version, owner, schema and golden examples.
- All cross-layer producer/consumer pairs agree on the same contract revision.
- Breaking changes have ADR, migration and impacted-layer list.
- No contract imports an implementation package.
- Generated schemas/OpenAPI are reproducible and current.
- Error taxonomy covers adapter, validation, workflow and policy failures.
- Contract-test suite is callable independently by every layer.

### Integration handoff

- Publish contract version and content hash.
- Provide changed-schema diff and compatibility classification.
- Provide fixture payloads and consumer action list.
