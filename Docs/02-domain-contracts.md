# Domain contracts

## Core types

- `ChangeRequest`: requirement text, repository refs and optional changed paths.
- `EvidenceRef`: immutable identifier, kind, source, locator/hash and confidence.
- `ImpactFinding`: affected entity, relation, severity, confidence and evidence IDs.
- `TestObligation`: behavior to prove, mandatory flag and evidence IDs.
- `TestPlan`: selected/excluded tests with deterministic reasons.
- `ToolRequest` and `ToolResult`: authorized capability, bounded arguments and audit state.
- `HealingIntent`: object, field/action, expected control and preconditions.
- `HealingDecision`: ranked candidates, uniqueness result, evidence and abstention reason.
- `Claim`: text, materiality, evidence IDs and validation state.
- `GovernanceAssessment`: metric values and policy violations.
- `ReleaseDecision`: `GO`, `CONDITIONAL_GO`, `NO_GO` or `INCOMPLETE` plus deterministic reasons.

## Evidence states

| State | Meaning |
|---|---|
| `CONFIRMED` | Parsed source, signed contract, deterministic test or live runtime evidence |
| `INFERRED` | LLM/vector-derived proposal awaiting confirmation |
| `HUMAN_CONFIRMED` | Reviewed semantic relationship with identity and timestamp |
| `STALE` | Evidence snapshot no longer matches current source/runtime |
| `REJECTED` | Invalid, contradictory or outside the allowed evidence set |

## Compatibility

Public payloads carry `schema_version`. Additive optional fields are minor changes; required-field or semantic changes require a major schema revision and producer/consumer contract tests.
