# L02 — Source Ingestion and Change Detection

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Convert requirements, Git changes, Salesforce metadata/code and automation repositories into canonical, evidence-bearing ingest data |
| Owner | Salesforce/indexing engineer |
| Inputs | Requirement text, local Git refs, fixture/live snapshots, repository content |
| Outputs | `RequirementSpec`, `ChangeSet`, `SourceSnapshot`, `IngestBatch` |

## Part A — Solution design

### Components

- Requirement intake normalizer boundary.
- Local Git diff and artifact reader.
- Salesforce XML metadata parsers.
- Bounded Apex symbol/SOQL/reference extractor.
- Automation repository inventory parsers for Selenium/Cucumber/API/Apex.
- Canonical-key and content-hash service.
- Incremental ingest coordinator.

### Processing flow

```mermaid
flowchart TB
    SRC["Requirement / Git / Salesforce / automation"] --> SNAP["Immutable source snapshot"]
    SNAP --> HASH["Normalize + hash"]
    HASH --> DIFF["Changed artifact selection"]
    DIFF --> PARSE["Deterministic parsers"]
    PARSE --> CAN["Canonicalize nodes/edges/evidence"]
    CAN --> BATCH["Validated IngestBatch"]
```

### Supported initial extraction

| Source | P0 extraction |
|---|---|
| Requirement | Actors, rules, thresholds, acceptance criteria, ambiguities |
| Git | Base/head, commits, changed paths, line hunks, change kind |
| Salesforce XML | Objects, fields, Flows, Permission Sets, Profiles, Validation Rules |
| Apex | Classes/methods, referenced classes, SOQL objects/fields, test methods |
| Selenium/TestNG/Cucumber | Test cases, Page Objects, locators, steps, tags, assertions |
| API automation | Routes, payload/schema models, assertions, auth abstraction |
| Project docs | ADRs, business rules, indexes and stable content chunks |

### Incremental rules

- Hash normalized content with SHA-256.
- Skip unchanged `(content_hash, parser_version)` artifacts.
- Mark missing entities/relationships inactive in the next successful snapshot; do not hard-delete.
- Emit deterministic evidence locator and extractor version for every structural fact.
- Semantic candidate links are separate proposals, never structural facts.

### R0.1 edge-envelope boundary

The current `canonical-edge-artifact-parser` is a deterministic parser for a bounded, already
canonical JSON edge claim. It is pinned by registry identity, version, implementation locator and
implementation SHA-256, and consumers replay its artifact bytes. This proves that a local claim was
parsed consistently; it does **not** attest that Salesforce, Git or a test runner produced the
underlying fact. The verification result therefore always carries
`UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED`, remains analysis-only and cannot authorize release.

Future source adapters must create the canonical artifact through a bounded, source-specific
capture receipt that binds the authenticated read/query, permitted source, capture time, response
digest, parser/normalizer version and project/snapshot. They must feed the same envelope verifier;
they must not create a second trust path or rename the current parser to imply source attestation.

### R0.4 local Git and graph-seed boundary

The verified local-Git producer captures complete bytewise base/candidate manifests and derives the
change partition itself. The follow-on mapper replays that capture plus current graph/path trust,
uses an exact repository locator to select a unique policy-defined source-artifact anchor, and
derives declared entities and affected path identities. It never accepts caller-provided paths,
seeds or mapping exemptions. Current graph snapshots are catalog digests, not Git candidate-tree
receipts; therefore the mapper exposes local structural alignment while retaining a blocking graph
input-tree attestation gap. Deletes require an independently captured base graph or tombstone.

### Failure policy

- One malformed artifact is isolated and reported; policy decides whether snapshot activation is blocked.
- Unsupported syntax returns `UNSUPPORTED`, not an empty successful parse.
- A diff against missing refs fails fast.
- Source credentials and unrestricted Salesforce record data are never ingested.

## Part B — Development notes

### Repository

```text
packages/indexing/
├── service.py
├── snapshots.py
├── canonical_keys.py
├── parsers/
│   ├── salesforce_xml.py
│   ├── apex_symbols.py
│   ├── requirements.py
│   └── automation/
└── normalizers/
packages/connectors/local_git/
sample-salesforce/
tests/fixtures/automation/
```

### Implementation order

1. Canonical-key and evidence-locator rules.
2. Local Git and snapshot hashing.
3. Salesforce object/field/Flow/permission parsers.
4. Bounded Apex extraction.
5. Selenium/API/Apex automation inventory.
6. Incremental batch/dedupe logic.
7. Requirement semantic proposal behind `SemanticReasonerPort`.

### Development rules

- Parsers are pure/deterministic where practical.
- Preserve raw artifact hashes and exact source locators.
- Never invoke graph storage directly; return `IngestBatch` through the port.
- Do not install or execute repository dependencies during inventory.
- Parser feature support is explicit and versioned.

## Part C — Testing and definition of done

### Tests

- Golden files for every supported metadata/automation type.
- Malformed XML, encoding, namespaces and large-file boundaries.
- Git add/modify/delete/rename and binary-file behaviour.
- Apex comments/strings/queries and false-reference negatives.
- Locator/assertion/step extraction across representative frameworks.
- Same input/parser version produces identical batch/hash.
- One changed file triggers only affected re-ingest.
- Removed relationships become inactive after successful activation.

### Definition of done

- Strategic-discount fixture emits canonical Field/Flow/Apex/Permission/Test entities.
- Automation fixtures emit Selenium/API/Apex assets, locators, assertions and test-data relationships.
- Every structural node/edge has evidence, source hash and extractor version.
- Incremental ingest skips unchanged artifacts and handles deletion.
- Unsupported content is visible and cannot masquerade as successful coverage.
- Fixture and live snapshot sources produce the same canonical model for equivalent metadata.
- Contract tests with L01 and ingest tests with L03 pass.

### Integration handoff

- Parser support matrix and parser-set version.
- Golden `IngestBatch` fixtures and expected counts.
- Known unsupported constructs and snapshot activation policy.
