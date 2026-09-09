# R0.1 trusted-edge-envelope review

Date: 2026-09-09

Capability: `knowledge.trusted-edge-envelope`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

R0.1 may prove the integrity, identity and freshness of a bounded local canonical-edge artifact. It
must not imply that Salesforce, Git or a test runner produced the underlying fact, accept a stored
self-hash as replay, partially promote an aggregate verification, suppress the existing analysis
lane, or enable a release decision.

## Independent review lanes

- Genericity/architecture/scope: `r01_genericity_review` — APPROVED, no P0/P1 findings.
- Governance/evidence/safety: `r01_governance_review` — APPROVED, no P0/P1 findings.

The reviews required external root pins; actual parser implementation and artifact-byte replay;
legal direction/profile mapping; nonempty aggregate scope; deterministic rejection ordering;
release-only gap isolation; observable fail-closed consumer degradation; truthful local-envelope
terminology; upstream-capture gaps on every accepted path; Windows-safe relative locators; and
adversarial state, expiry, tamper, duplicate, reversal, policy-rotation and permutation tests.

## Verified behavior

- Only `CONFIRMED` canonical edge artifacts can compile; raw `HUMAN_CONFIRMED`, inferred, stale,
  rejected, contradictory and unknown states cannot promote themselves.
- Every envelope binds edge/relation/direction, project/snapshot, raw and normalized graph,
  ontology/profile, repository-relative artifact locator and digest, registered parser
  identity/version/implementation, policy, observation time and validity window.
- Consumers receive full current replay inputs. Expired, tampered, incomplete or policy-rotated
  inputs produce stable blocking `RELEASE_ONLY` gaps and cannot partially mark a path complete.
- Missing R0.1 fields appear in a separate blocking release-readiness channel; legacy analysis and
  test selection continue unchanged.
- Paths remain `ANALYSIS_ONLY`, use `local_edge_envelope_complete`, and retain
  `UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED`; verification always reports `release_eligible=false`.
- Renamed source vocabulary/topology, disconnected nodes, edge/artifact order, line endings and
  duplicate structures have deterministic tested behavior.
- Absolute/traversal/ADS/control/device-name/trailing-dot-or-space locators are rejected without
  echoing sensitive path content.

## Retained foundation gaps

- The built-in `canonical-edge-artifact-parser` parses an already canonical claim; an attested
  source-capture producer is still required.
- Historical normalizer replay needs an independently versioned implementation contract.
- Durable persistence and independent/signature anchoring are not yet implemented.
- Complete material-path replay, immutable release-input binding, verified build/obligation
  matrices and scoped human receipts remain R0.2–R0.5.
- `RELEASE_EVIDENCE_MODEL_INCOMPLETE` remains non-bypassable.

These gaps prevent promotion beyond `FOUNDATION`; they do not block the existing analysis/demo lane.
