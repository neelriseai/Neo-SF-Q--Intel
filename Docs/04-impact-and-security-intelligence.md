# L04 — Impact and Security Intelligence

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Determine evidence-backed business, technical and access-control impact of a requirement/change |
| Owner | Domain/security engineer |
| Inputs | `RequirementSpec`, `ChangeSet`, snapshot-bound `GraphResult` |
| Outputs | `ImpactReport`, `SecurityReport` |

## Part A — Solution design

### Impact analysis

1. Resolve changed components through structural evidence.
2. Traverse allowlisted graph edges with bounded depth.
3. Apply relationship-specific propagation rules.
4. Group results by business rule, process, control, component, integration and test obligation.
5. Rank evidence paths using edge confidence, source reliability and path length.
6. Return confirmed and possible impacts separately.

### Security analysis

- Object/field access affected by metadata change.
- Apex class access and Flow execution context.
- Users allowed to change input versus approve outcome.
- Permission/Profile/record-type effects.
- Separation-of-duties and bypass paths.
- Broadened query/API/integration surface.
- Secrets/endpoints introduced in source changes.

### Impact types

```text
BUSINESS_RULE
BUSINESS_PROCESS
DATA_WRITE_OR_VALIDATION
SECURITY_OR_CONTROL
INTEGRATION_CONTRACT
AUTOMATION_OR_TEST
OPERATIONAL_OR_RELEASE
```

### Evidence policy

- A finding requires at least one `EvidenceRef`.
- Confirmed paths may drive deterministic risk/security gates.
- Low-confidence semantic paths create reviewer tasks.
- The engine never writes new graph facts; it emits semantic-link proposals for L03 review.

### Failure behaviour

- Missing graph freshness blocks definitive output.
- No evidence returns `UNKNOWN/REVIEW_REQUIRED`, not “no impact.”
- Contradictory confirmed evidence is surfaced and blocks downstream release decision.

## Part B — Development notes

### Repository

```text
packages/impact/
├── service.py
├── traversal_rules.py
├── propagation.py
└── scoring.py
packages/security/
├── service.py
├── permission_paths.py
├── bypass_rules.py
└── severity.py
```

### Implementation guidance

- Keep propagation/security rules as versioned policy data plus deterministic functions.
- Query only through `GraphPort`.
- Deduplicate by `(canonical_target, impact_type)` while preserving corroborating paths.
- Store shortest strongest path and material alternatives.
- Isolate semantic explanation behind `SemanticReasonerPort`.

### Forbidden dependencies

- Salesforce SDK/connector, FastAPI, UI, test execution, automation framework and release engine.

### Initial rules for demo

- Threshold change propagates from Flow/Apex to `Opportunity.Discount__c` and approval business rule.
- Field/Flow paths identify affected Permission Set/Profile.
- Control analysis identifies negative-permission scenario.
- Unrelated Account rule is not claimed.

## Part C — Testing and definition of done

### Tests

- Table-driven propagation for every allowed edge type.
- Cycles, duplicate paths, depth boundaries and graph explosion caps.
- Confirmed versus inferred classifications.
- Field read versus write severity.
- Flow user/system context and permission bypass cases.
- No-evidence and contradictory-evidence handling.
- Precision/recall on golden impact cases.
- Unsupported-claim negative cases.

### Definition of done

- All findings contain valid evidence IDs and snapshot version.
- Strategic threshold change returns expected business/technical/security/test impacts.
- Unrelated entities are excluded from confirmed impact.
- Security report includes affected grants, control path, bypass scenarios and severity.
- Deterministic rerun produces byte-equivalent normalized output.
- Critical inferred-only finding is review-required and cannot alone gate release.
- Unit/contract/eval thresholds agreed with L14 pass.

### Integration handoff

- Publish impact/security policy versions.
- Provide expected reports for golden graph fixtures.
- Document propagation limits and known unsupported Salesforce semantics.
