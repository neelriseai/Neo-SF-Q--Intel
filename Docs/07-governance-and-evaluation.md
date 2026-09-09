# Governance and evaluation

## Measurement contract

Every measured result records the policy version and SHA-256, numerator, denominator,
comparator, threshold, minimum sample size, applicability state and whether failure blocks a
release. A zero denominator is `NOT_APPLICABLE`; it is never rewritten as `0/1` or reported as
success. A population below its declared minimum is `INSUFFICIENT_SAMPLE`, with raw counts shown
and no production-quality claim.

The executable run policy is `config/governance-policy.json`. Its Pydantic contract rejects
missing populations, undeclared metric references, blocking metrics without a blocking control,
and incomplete outcome-to-decision mappings.

## Implemented run-level metrics

| Metric | Numerator | Denominator | Gate |
|---|---|---|---|
| `material_claim_evidence_coverage` | Material claims marked supported whose cited IDs all resolve to `CONFIRMED` or `HUMAN_CONFIRMED` evidence | Material claims emitted by this run | `>= 1.0`; minimum 1; zero population `NOT_APPLICABLE`; blocking |
| `impact_evidence_coverage` | Impact findings with at least one cited ID and every cited ID confirmed | Impact findings emitted by this run | `>= 1.0`; minimum 1; zero population `NOT_APPLICABLE`; blocking |
| `selected_test_evidence_coverage` | Selected validations with at least one cited ID and every cited ID confirmed | Validations selected by this run | `>= 1.0`; minimum 1; zero population `NOT_APPLICABLE`; blocking |

These are evidence-completeness ratios, not model accuracy estimates. Retrieval strength is only a
ranking signal. It is never displayed as a calibrated probability and never overrides evidence
state, source hash, snapshot identity, relation direction or validity window.
Each run also stores the reasoning-policy version/SHA-256 and the frozen retrieval evaluation-set
ID/SHA-256. The reasoning identity covers both canonical policy and evaluation-set content, so
candidate selection can be replayed and an in-place corpus edit cannot retain the old identity.
The run additionally stores canonical ontology ID/version/SHA-256, source-profile
ID/version/SHA-256 and normalized-graph SHA-256. The analysis-input digest binds those identities;
a profile, ontology or normalized topology change cannot retain the old analysis identity.

## Implemented guardrails and hooks

| Control | Stage | Exact trigger | Outcome |
|---|---|---|---|
| `evidence.confirmed-present` | post-analysis | No confirmed evidence exists | `ABSTAIN`, blocking |
| `analysis.semantics-complete` | post-analysis | At least one `AnalysisGap.blocking=true` exists | `REVIEW_REQUIRED`, blocking |
| `claims.confirmed-evidence-coverage` | post-analysis | Claim coverage is failed or sample is insufficient | `ABSTAIN`, blocking |
| `impacts.confirmed-evidence-coverage` | post-analysis | Impact coverage is failed or sample is insufficient | `ABSTAIN`, blocking |
| `tests.confirmed-evidence-coverage` | post-analysis | Selected-test coverage is failed or sample is insufficient | `ABSTAIN`, blocking |

Recommended-test truncation is observable but nonblocking. Unmapped relations are classified from
the canonical materiality of both endpoints: any material endpoint blocks, while a relation solely
between supporting endpoints is visible and nonblocking. Missing evidence-envelope fields remain
`UNVERIFIED`; raw `HUMAN_CONFIRMED` values are denied because approval is a separate governed
receipt. The system does not use broad keyword filters for prompt injection; structural input
schemas, provenance, source trust, tool authorization and output validation provide the boundary
without rejecting legitimate content.

The pre-commit hook validates knowledge freshness, genericity, exact structured scope-review
coverage and the governance policy. The pre-push hook runs the full lint and test suite. A random
file in the review directory cannot approve a policy change, and an honest capability downgrade is
allowed only when the manifest names the capability, old state, new state and a concrete reason.

## Release decision truth table

### Current release-authority interlock

The default policy currently sets `release_authority.enabled=false`. Analysis, evidence metrics and
gaps remain usable, but the runtime cannot emit `GO`, `CONDITIONAL_GO` or `NO_GO`; after other
blocking checks it returns `INCOMPLETE / RELEASE_EVIDENCE_MODEL_INCOMPLETE`. This is a deliberate
P0 safety interlock because the current evidence model does not yet bind complete edge provenance,
full graph paths, verified candidate builds/per-impact obligations, scoped human approvals,
authoritative conflicts and every release-relevant field into one immutable release input.

R0.1 now supplies an isolated trusted-edge-envelope foundation. It replays current raw and
normalized graph roots, ontology/profile, parser registry and actual implementation, policy,
canonical edge artifact bytes, legal direction/signature, evidence state and freshness. Missing,
expired, duplicated or tampered inputs create deterministic blocking `RELEASE_ONLY` gaps and cannot
partially promote a path. These gaps do not suppress the legacy analysis/demo lane. The result still
states `UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED`, `release_eligible=false` and `ANALYSIS_ONLY`; it is a
necessary input-integrity step, not a reason to relax `RELEASE_EVIDENCE_MODEL_INCOMPLETE`.

R0.2 now adds complete local material-path replay without changing that conclusion. Its separately
pinned policy derives seeds from every graph node and enumerates every reachable material target
under the current propagation policy before applying evidence trust. The exact union of path edges
must pass current R0.1 replay, and the resulting trusted ordered paths must exactly match the
structural scope. Current UTC is sampled internally; a still-valid historical artifact is refreshed
only after every static hop-provenance field matches current replay. Empty or capacity-limited scope,
omission, addition, reorder, reversal, substitution, expiry and tamper produce deterministic
blocking `RELEASE_ONLY` gaps. `CHANGE_SEED_SCOPE_NOT_ATTESTED` and
`UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED` remain, so “coverage complete” means local envelope/path
coverage only and cannot enable a release decision.

R0.3 now supplies an isolated candidate-composition foundation without relaxing that boundary. A
separately self-hashed and externally pinned policy compiles the complete current v2 request and
analysis partitions, exact candidate change/build bytes, current-replayed R0.2 path artifact and
R0.1 identities, deterministic current-policy risk associated with stable structural path
identities, advisory obligation/execution/conflict/human payloads, and the active runtime policy
roots into one canonical expiring manifest. The compiler derives run partitions from the whole run,
samples current UTC internally, validates safe repository-relative locators and rejects omission,
duplication, cross-root reuse, policy rotation, expiry, nested rehashing, fabricated path identities
and swaps of valid path identities between different risk contributions. Candidate composition is
not producer completeness: verified change/build capture, attested risk factors, per-impact
obligations and exact-build executions, authoritative conflict scope, scoped human approvals and
upstream capture remain typed blocking gaps. The manifest and its evaluation therefore stay
`ANALYSIS_ONLY`, `release_eligible=false`, and cannot be passed to release governance as authority.

The required interlock field is a breaking governance-policy contract change, so the executable
policy is version `2.0.0`. The interlock cannot be enabled by a configuration toggle in policy
version 2.x. A future reviewed
enablement contract, schema version and adversarial verification suite must change the validator.
The truth table below is therefore the target decision behavior after that gate, not a claim that
the current analysis-only runtime can authorize release.

Persisted decisions are audit facts, not permanent authority. Service and API read paths revalidate
every historical `GO`, `CONDITIONAL_GO` and `NO_GO` against the current release policy. While the
interlock is active, the public `decision` is `INCOMPLETE / RELEASE_EVIDENCE_MODEL_INCOMPLETE` and
the original value is exposed only as `recorded_decision`, explicitly non-authoritative. Raw
repositories retain the original run document for audit replay.

Guardrail outcomes map deterministically: `DENY -> NO_GO`, `ABSTAIN -> INCOMPLETE`,
`REVIEW_REQUIRED -> INCOMPLETE`, and `REQUIRE_APPROVAL -> CONDITIONAL_GO`. `DENY` wins if several
blocking outcomes coexist.

Evidence absence, staleness or expiry is an epistemic gap and therefore abstains to `INCOMPLETE`;
it is not proof of an unsafe release. `NO_GO` is reserved for a confirmed failed validation or a
future explicit deny-class security/policy violation.

After governance and the future release-authority contract pass:

| Condition | Decision |
|---|---|
| Any selected validation has one unique, confirmed-evidence-backed `FAILED` result | `NO_GO` |
| High-risk impact has no mandatory validation | `INCOMPLETE` |
| A required validation has no unique result, cites unconfirmed evidence, or is `INCONCLUSIVE` | `INCOMPLETE` |
| Every mandatory validation passes for a high-risk impact | `CONDITIONAL_GO` pending human approval |
| Every selected validation passes and no impact is high risk | `GO` |
| No validation is selected | `INCOMPLETE` |

Selection evidence and execution evidence are different facts. Merely recommending a test can
never produce `GO`. Execution evidence must be a distinct `test-execution` receipt whose test ID,
outcome, configured trusted-runner ID/source, canonical result-artifact SHA-256, source snapshot,
execution time and validity window match the typed result. Future, expired and over-age receipts
are rejected. A policy allowlist is present, but a runtime test-execution producer is still `NEXT`;
the analysis-only workflow therefore returns `INCOMPLETE` rather than manufacturing a pass.

## Historical outcome-memory controls

The A7 outcome policy and invariant manifest are independently self-hashed and module-pinned.
Stored test outcomes must replay to one unique trusted execution receipt. Incident events require a
legal, time-monotonic, bounded and already-persisted predecessor chain. Their targets are typed and
bound to run-derived artifact hashes. Human corrections remain explicit unverified inferred claims;
an opaque authority reference does not turn them into `HUMAN_CONFIRMED` evidence.

Repositories accept only a replay request containing the exact originating run and incident history;
the application service resolves that run from authoritative run persistence instead of trusting a
caller copy. Append and read paths reject forged roots, future time, missing evidence, duplicate
identities, cross-project data, unsafe credential/path text and corrupted normalized storage. These
records are candidates for later reasoning only. The current evaluation manifest is `NOT_RUN`, has
zero adjudicated cases and supports no outcome-quality claim.

## Planned corpus gates (not yet runtime claims)

Corpus promotion gates activate only with at least 20 adjudicated cases and a frozen corpus ID.
Below 20 cases the result is `INSUFFICIENT_SAMPLE` and cannot support an accuracy claim.

| Metric | Numerator | Denominator | Proposed promotion gate |
|---|---|---|---|
| Critical-impact recall | Expected critical impact IDs present in the output | Critical impact IDs in adjudicated golden cases | `>= 0.95`, plus every zero-recall case listed |
| Mandatory-test recall | Expected mandatory test IDs selected | Mandatory test IDs in adjudicated golden cases | `>= 0.95` |
| Unsupported material-claim rate | Emitted material claims adjudicated unsupported | Emitted material claims reviewed in the corpus | `<= 0.01` and zero unsupported release-blocking claims |
| False-heal rate | Applied locator heals that resolve to the wrong target or alter test intent | Applied locator heals with adjudicated outcomes | `0` for critical flows; `<= 0.02` otherwise |
| Release-decision agreement | Runs whose decision exactly equals the frozen deterministic expected decision | Adjudicated runs with an expected decision | `1.0` |

These thresholds are candidate promotion rules until the corpus evaluator, labels and acceptance
process are implemented. They must not appear on the dashboard as achieved metrics before then.

## Planned operational-window gates (not yet implemented)

Operational windows use a declared start/end time and deployment version. Tool audit completeness
is `tool calls with request ID, actor, operation, policy decision, start/end timestamp and redacted
result status / all tool calls`; target `1.0`, minimum 100 calls. Unauthorized execution rate is
`executed calls whose authorization decision was not ALLOW / executed calls`; target `0`, minimum
100 calls. Credential-persistence findings are the count of scanner-confirmed secrets, tokens or
session URLs in logs and stores; target exactly `0` for every release. These remain planned until
the audit writer and operational evaluator exist.
