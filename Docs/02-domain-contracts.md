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
- `CandidateAssuranceBundle`: internal, canonical and fully replay-validated candidate evidence;
  cross-binds the foundation, exact graph/operation-seed artifacts, project, per-side source roots,
  ontology/profile identities, reasoning identities and immutable component runs. Its repository
  transaction covers the bundle and runs, not workflow checkpoints, which remains an explicit
  blocking gap.
- `CandidateAssuranceView`: bounded client/MCP projection containing receipt roots and side/run
  references only. Large graph, seed and run artifacts are deliberately absent and are never
  accepted from a client response as authority.
- `OutcomeRecord`: immutable, append-only historical candidate bound to the complete originating
  run, project/source snapshot, graph/ontology/profile roots and module-pinned outcome/evaluation
  policies. Implemented payloads cover trusted test execution, typed incident lifecycle events and
  unverified human-correction claims. Incident targets carry kind, identifier and a canonical
  run-derived artifact hash; identical bare identifiers in different artifact kinds stay distinct.

Outcome records are always `HISTORICAL_CANDIDATE` / `NON_AUTHORIZING`. Recording or retrieving an
outcome replays its originating run and current pinned contracts. It cannot confirm a graph edge,
satisfy a release obligation, authorize a tool, approve a release or mutate the authoritative run.

## Target contracts

The live-evidence foundation now includes an independently compiled `ExpectedExecutionContract`
and a typed `ExecutionAssertionArtifact`. The contract binds exact execution, plan, scope,
producer/runner/tool-version, assertion/predicate/cardinality/projection, metadata-member and
dataset roots without authorizing execution. The runner artifact embeds the sanitized result bytes,
recomputes their size and hash, binds every assertion to one exact result role, and carries a
host-configured runner-key signature. Exact contract, result and assertion bytes are stored
append-only in private-schema PostgreSQL or SQLite and replayed before the receipt producer may
derive an outcome or digest. PostgreSQL outage can degrade explicitly to SQLite; neither outage may
degrade to volatile memory evidence.

The browser foundation separates `LocatorIntent` candidate discovery from independently authorized
reversible recovery. A signed recovery permit binds one prior unique worker candidate, exact
precondition and expected changed state, complete campaign/source/candidate/org/actor roots,
restoration and cleanup. The resulting signed evidence uses
`CANDIDATE_DISCOVERED → PROPOSAL_APPROVED → ACTION_APPLIED → OUTCOME_VERIFIED`; discovery alone is
never `HEALED`. Production target/receipt wiring and live Salesforce application remain incomplete.

The complete tool and live test-execution verticals still require domain-level `TestObligation` /
`TestPlan` and authorized `ToolRequest` / `ToolResult` contracts. Outcome memory still needs
decision/override/production outcome kinds, historical-policy replay and public HTTP/MCP contracts.
Their absence is why those capabilities remain `FOUNDATION` or `NEXT`.

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
