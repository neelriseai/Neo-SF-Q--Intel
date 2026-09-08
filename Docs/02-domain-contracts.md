# Domain contracts

## Implemented core types

- `ChangeRequest`: requirement text, repository refs and optional changed paths.
- `EvidenceRef`: immutable identifier, kind, source, locator/hash and typed evidence state.
- `ImpactFinding`: affected entity, relation, severity, evidence-ranking strength/basis and evidence IDs. Ranking strength is not a calibrated probability or release gate.
- `TestSelection`: selected validation, mandatory/recommended classification, reason and evidence IDs.
- `HealingProposal`: evidence-bound strategy proposal, deterministic ranking basis and approval flag;
  it is not proof that a browser action occurred.
- `AgentActivity` and `AgentDeliverable`: bounded trace summary, conclusion, evidence/artifact
  references, policy identity, measurements, gaps and next permitted action.
- `Claim`: text, materiality, evidence IDs and validation state.
- `TestExecution`: selected test ID, typed outcome, runner ID, immutable result hash, source snapshot, execution/expiry time and confirmed execution-receipt IDs.
- `GovernanceAssessment`: policy identity, exact metric values, typed guardrail decisions and violations.
- `ReleaseDecision`: `GO`, `CONDITIONAL_GO`, `NO_GO` or `INCOMPLETE` plus deterministic reasons.
- `CanonicalOntology` / `SourceGraphProfile`: strict source-neutral classes, legal relation
  signatures and exhaustive source vocabulary mappings with pinned identities.
- `NormalizedGraph`: deterministic canonical nodes/edges, mapping and trust gaps, source/profile/
  ontology identities and a stable normalized-graph SHA-256.
- `AssuranceRun` schema `2.0.0`: binds raw source snapshot/digest plus ontology, source-profile and
  normalized-graph identities used by the analysis in addition to reasoning/governance policy
  identities. Pre-A1 documents load explicitly as schema `1.0.0` with unavailable identities and
  remain subject to the current fail-closed decision view.

## Target contracts

The complete tool, test-execution and browser verticals will add domain-level `TestObligation` /
`TestPlan`, authorized `ToolRequest` / `ToolResult`, and executable `HealingIntent` /
`HealingDecision` contracts. Their absence is why those capabilities remain `FOUNDATION` or `NEXT`.

## Evidence states

| State | Meaning |
|---|---|
| `UNVERIFIED` | A source record is missing a complete evidence envelope or an independently valid human-approval receipt |
| `CONFIRMED` | Parsed source, signed contract, deterministic test or live runtime evidence |
| `INFERRED` | LLM/vector-derived proposal awaiting confirmation |
| `HUMAN_CONFIRMED` | Reviewed semantic relationship backed by a separate scoped identity, timestamp and validity receipt |
| `STALE` | Evidence snapshot no longer matches current source/runtime |
| `REJECTED` | Invalid, contradictory or outside the allowed evidence set |

## Compatibility

Public payloads carry `schema_version`. Additive optional fields are minor changes; required-field or semantic changes require a major schema revision and producer/consumer contract tests.
