# L03 — Evidence Graph and Durable Memory

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Store and traverse the verified relationship model connecting business, Salesforce, security, automation, tests and outcomes |
| Owner | Graph/data engineer |
| Inputs | `IngestBatch`, human verification, test/release/outcome events |
| Outputs | `GraphResult`, `EvidenceRef[]`, graph snapshots and project/context indexes |

## Part A — Solution design

### Data planes

- Business: Requirement, BusinessRule, AcceptanceCriterion, BusinessProcess, Control.
- Salesforce: Object, Field, Flow, Apex, LWC, Validation Rule, Permission/Profile, Integration.
- Change: Repository, File, Symbol, Commit, ChangeSet, SourceSnapshot.
- Automation/quality: AutomationAsset, Locator, PageObject, TestData, Assertion, TestCase, TestRun, ValidationRun.
- Outcome: Incident, FailureSignature, ReleaseDecision, HumanDecision.
- Provenance: Evidence, Artifact, IngestRun, Policy/Prompt/Model/Framework versions.

### Evidence model

Every active edge contains source/target, allowed edge type, confidence class, extractor, evidence ID, source hash and validity window.

| Class | Use |
|---|---|
| `CONFIRMED` | Deterministic parser/API/human verified; may drive gates |
| `CORROBORATED` | Independent sources agree; policy may allow gates |
| `INFERRED_HIGH` | Review/corroboration required for critical gates |
| `INFERRED_LOW` | Review item only |
| `REJECTED` | Retained for evaluation; inactive in traversal |

### Storage

- Hackathon: SQLite + JSONL export + NetworkX for bounded algorithms.
- Pilot: PostgreSQL adjacency tables and content-addressed artifacts.
- Optional pgvector for semantic chunks; graph/exact retrieval remains functional without it.

### Snapshot activation

1. Stage nodes, edges and evidence under an ingest run.
2. Validate keys, edge vocabulary, evidence and referential integrity.
3. Mark superseded relationships inactive.
4. Activate the complete snapshot transactionally.
5. Rebuild affected layer/project indexes.

### Query policy

- Bounded depth, node count and edge allowlist.
- Snapshot/version mandatory for reproducible analysis.
- Prefer shortest strongest evidence paths.
- Return confirmed and inferred paths separately.
- Project/tenant authorization enforced before traversal.

## Part B — Development notes

### Repository

```text
packages/graph/
├── service.py
├── repository.py
├── traversal.py
├── scoring.py
├── integrity.py
└── snapshots.py
knowledge/
├── ontology.yaml
└── generated/
architecture/graph-schema.yaml
```

### Implementation order

1. Graph schema and canonical-key constraints.
2. SQLite repository and JSONL export.
3. Transactional ingest activation.
4. Deterministic bounded traversal/path scoring.
5. Project/layer/symbol snapshot generation.
6. PostgreSQL repository parity.
7. Semantic chunks/embedding cache as P1.

### Dependency rules

- Depend only on contracts and persistence abstractions.
- No LLM, UI, FastAPI or live connector calls.
- NetworkX is an algorithm helper, not persistent truth.
- Derived edges record upstream evidence and policy version.

## Part C — Testing and definition of done

### Tests

- Node/edge schema allowlist and invalid pair rejection.
- Duplicate canonical key and content dedupe.
- Orphan evidence/node prevention.
- Transaction rollback on failed integrity validation.
- Traversal depth/size caps and deterministic path ranking.
- Inferred/confirmed separation.
- Snapshot time-travel and inactive-edge behaviour.
- SQLite/PostgreSQL repository contract parity.
- Concurrent ingest and read consistency.

### Definition of done

- One active node per project/canonical key.
- Every active edge references active nodes and evidence.
- Strategic-discount query returns business → Field/Flow/Apex/Permission → automation/test paths.
- Rejected semantic edges cannot influence impact/release gates.
- Failed ingest leaves prior active snapshot unchanged.
- Graph/query result includes snapshot and query-policy versions.
- Project and layer indexes regenerate only for affected areas.
- Backup/export can reconstruct graph/evidence referential integrity.

### Integration handoff

- Publish graph schema version, snapshot ID and query limits.
- Provide traversal fixtures for L04–L08.
- Document storage migration and unsupported query patterns.
