# Graph-grounded reasoning design review

## Decision

Adopt the Change Evidence Graph as the central reasoning substrate. Preserve the implemented
trusted source graph and deterministic traversal. Add a deterministic context compiler before
provider-backed specialists, keep semantic/vector output in an inferred proposal plane, and retain
release decisions in deterministic governance.

## Why this changes the roadmap

The earlier roadmap treated graph traversal, semantic retrieval and future agents as adjacent
capabilities. That would leave semantic search disconnected and invite agents to reason from
unbounded prose. The new sequence joins them through a typed, hashed `GraphContextPack` and makes
canonical ontology/profile mapping the first portability gate.

## Current truth preserved

- Trusted JSON graph ingestion and snapshot/hash validation: implemented/foundation.
- Deterministic lexical/path seed selection and bounded graph traversal: foundation.
- Standalone in-process embedding similarity: foundation, not part of assurance decisions.
- PostgreSQL graph/audit tables: schema foundation, not complete application memory.
- ChromaDB, context compiler, graph-grounded specialists and outcome memory: `NEXT`.

## Review acceptance

- No demo entity, object, field, alias or route becomes a core reasoning constant.
- No inferred/vector result can satisfy a confirmed evidence or release gate.
- PostgreSQL remains authoritative relational memory; ChromaDB remains derived.
- Missing ontology mappings, provenance or freshness produce explicit gaps/abstention.
- Capability and dashboard claims remain at their actual implementation status.
- Current bidirectional traversal is not represented as causal reasoning; direction-aware
  propagation/path receipts and separate risk policy are the first implementation slice.
- Current release authority is disabled with `RELEASE_EVIDENCE_MODEL_INCOMPLETE`. Safe analysis-only
  graph-agent work proceeds in a separate lane; all release-evidence prerequisites remain mandatory
  before any release-authorizing or rejecting decision is promoted.
- Historical release results are audit facts only; service/API reads expose a revalidated effective
  decision and preserve the displaced result as non-authoritative `recorded_decision`.

## Independent review result

`genericity_reviewer` and `governance_precision_auditor` independently approved the completed
slice with no remaining P0 or P1 findings. The adversarial review covered nested policy mutation,
unvalidated policy copies, historical `GO`/`CONDITIONAL_GO`/`NO_GO`/missing decisions, invalid
current policy, repository audit preservation, scenario coupling, capability status and the
analysis-versus-release roadmap boundary.
