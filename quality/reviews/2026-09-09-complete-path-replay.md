# R0.2 complete graph-path replay review

Date: 2026-09-09

Capability: `knowledge.complete-path-replay`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

R0.2 must derive complete local candidate path scope rather than accept caller-selected targets,
paths or edge subsets. Every edge of every reachable material-target path must pass current R0.1
replay. The result may prove local envelope/path coverage only; it cannot attest the changed seed or
upstream source fact, become release authority, or weaken the existing analysis lane.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — APPROVED, no P0/P1 findings.
- Governance/evidence/safety: `r01_governance_review` — APPROVED, no P0/P1 findings.

The reviews required conservative all-node seed derivation; trust-neutral structural enumeration;
exact current R0.1 edge union; independent propagation, path, edge-policy and registry pins; private
UTC sampling; refreshed historical receipts; full static hop-provenance comparison; bounded typed
failure; and direct rename, diamond, cycle and permutation coverage.

## Verified behavior

- The policy derives all graph nodes as candidate seeds and every policy-reachable material target;
  callers cannot submit a smaller target/path/edge scope or make empty scope appear complete.
- Candidate structural paths contain no fabricated evidence or authority digest. Current R0.1 replay
  must accept the exact edge union, and trusted ordered paths must equal the structural set.
- Seed, path-seed and zero-material-path seed partitions, material outputs, exact edge union, path
  identities/count and hop count are canonical, nonempty where required and hash-bound.
- Capacity truncation, missing/extra envelopes, expiry, policy/root rotation, removal, addition,
  reorder, reversal, substitution, duplication, broken chain and static provenance tamper create
  deterministic blocking `RELEASE_ONLY` gaps.
- Evaluation time is sampled internally in UTC. A still-valid historical artifact is refreshed only
  after current replay and equality of every non-time-derived root/scope/hop field.
- The direct renamed diamond/cycle/permutation test proves deterministic unique simple paths and an
  unchanged verification class without scenario-specific identifiers.
- Path failure does not remove or weaken legacy analysis paths.

## Retained foundation gaps

- Candidate seeds are conservative graph scope, not an attested `VerifiedChangeSet`.
- Underlying source capture remains unattested even when every local edge envelope is current.
- Persistence/independent anchoring and R0.3 immutable release-input composition are pending.
- R0.4 verified build/obligation mapping and R0.5 scoped human/conflict receipts are pending.
- Every result remains `ANALYSIS_ONLY`, `release_eligible=false`, and the global
  `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock remains enabled.

These gaps prevent promotion beyond `FOUNDATION`; they do not block the analysis/demo lane.
