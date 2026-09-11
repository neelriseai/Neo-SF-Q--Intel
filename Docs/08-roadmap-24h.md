# Twenty-four-hour implementation roadmap

> **Development paused 2026-09-11.** The current verified and interrupted state, open P1 findings,
> live Salesforce non-execution boundary and exact resume order are recorded in
> [the development stop checkpoint](21-development-stop-checkpoint-2026-09-11.md). No roadmap item
> below should be interpreted as authorization to continue or as live acceptance evidence.

## Runtime authority

Build and final verification target the org machine: Python 3.14.3, Node.js 26.5.x, Salesforce CLI 2.148.3 and local PostgreSQL. The development machine may use newer compatible patch releases, but the compatibility gate is the org machine.

## Current fixed execution cycle — eight hours

The scope is not reduced to fit the clock. The eight-hour cycle attempts the complete reusable
vertical and preserves every whole-demo and live Salesforce gate below. A missed gate remains
explicitly incomplete; fixtures, screenshots, manual observations or a renamed status cannot turn
the timebox into a pass.

| Elapsed | Primary integration lane | Salesforce/live-evidence lane | Browser/test lane | Exit evidence |
|---:|---|---|---|---|
| 0:00–0:30 | Freeze exact source candidate, acceptance profile, requirement IDs and campaign root; preflight provider/PostgreSQL/API/UI | Validate host enrollment inputs, target org-machine tool versions and non-production proof prerequisites without executing an unclassified adapter | Freeze Playwright TypeScript project, browser policy, selected scenarios and report locations | One immutable execution plan; no caller-narrowed paths or targets |
| 0:30–3:00 | Connect actual Git candidate → product graph/seeds → context → cooperating real-provider specialists → ordinary run → live PostgreSQL save/resume → FastAPI | Implement strict source-operation profile and `LiveTargetPlan`; execute `SF-L01`–`SF-L05` and `SF-L08` only when their host/data/test authority inputs validate | Implement ephemeral session broker/worker and execute `SF-L06`, `SF-L07`, `SF-L09`; wire the real dashboard path and retain sanitized Playwright reports | Live baseline receipts plus one real persisted run; no mock can satisfy this row |
| 3:00–5:30 | Bind the exact candidate and all prior receipts to one orchestration/campaign root | Execute `SF-C01`–`SF-C06`: restore readiness, exact check-only, independently authorized deploy, post-deploy retrieval, candidate-phase API/Apex evidence, mandatory restore/reconciliation | Repeat candidate-phase Lightning and recovery/readback under distinct current receipts; exercise ambiguity and authorization failures | `15/15` Salesforce campaign only if restoration and zero-residue reconciliation pass |
| 5:30–6:45 | Run the complete real FastAPI/dashboard vertical and three golden scenarios | Reconcile every live receipt to org/source/build/policy roots; verify no unknown remote job remains | Run the runnable Playwright TypeScript suite with machine-readable logging/reporting and negative/degradation paths | Source-bound functional, live, browser and persistence evidence bundle |
| 6:45–7:15 | Freeze development; calculate requirement and gate status only from accepted evidence | Record every live defect/blocker with reproduction, receipt IDs and affected gate | Record browser/API/UI bugs, flaky behavior, screenshots only when sanitized, and proposed CRs | Prioritized P0/P1/P2 defect and change-request ledger; no opportunistic scope additions |
| 7:15–8:00 | Integrate documentation, project index and knowledge graph; prepare the demo runbook | Recheck restoration/quarantine state and operator actions | Rehearse the viewer path without altering evidence | Final `gpt-6-astra` xhigh review and truthful demo-readiness verdict |

Three agents may work in parallel only on these bounded lanes. The primary agent owns contracts,
integration and final evidence reconciliation; parallel agents may not redefine scope, relax a gate,
invent fixture evidence or independently deploy. Astra is invoked once, after development and live
testing freeze, for the final whole-solution design/debug review.

Only a P0 safety issue or a defect that prevents collection/validation of a mandatory gate is fixed
inside the timebox. Other bugs, refinements, change requests and enhancements are retained with
reproduction evidence for the post-review patch cycle. A manual intervention is recorded early and
parallel work continues, but whole-demo completion cannot be claimed until its mandatory gate is
actually closed.

### Eight-hour definition of done

The cycle is complete only when one campaign proves all of the following:

1. actual local-Git candidate capture and product-generated graph/impact/test reasoning;
2. multiple real provider-backed cooperating specialists with governed degradation;
3. live PostgreSQL save, reload and resume with truthful fallback behavior;
4. all fifteen live Salesforce baseline/browser/candidate/restore gates;
5. real MCP/service, FastAPI and dashboard execution rather than fixture projection;
6. runnable Playwright TypeScript automation with structured logs and machine-readable reports;
7. three source-bound golden scenarios and a requirement-to-exact-test/receipt matrix; and
8. successful restoration, no unresolved external job, no leaked secret/session material and no
   release-authority claim.

`15/15` Salesforce campaign completion is necessary but does not alone satisfy these eight
whole-demo outcomes.

## Planned 24-hour schedule and current truth

Elapsed-hour labels are planning order, not completion evidence. The present worktree is not a
demo-ready vertical until the exit criteria below pass on one source-bound run.

| Hours | Planned outcome | Current status |
|---:|---|---|
| 0–2 | Repository, canonical docs, contracts, provider profiles and environment preflight | Foundation implemented; org-machine provider verification pending |
| 2–6 | Auto-created PostgreSQL run/checkpoint memory, SQLite/JSON/cache fallback, hybrid retrieval ports and snapshot checks | Run/checkpoint and replay-safe outcome-memory fallback foundations plus isolated deterministic candidate fusion are implemented; channel adapters, governed graph/metadata/audit ports, live PostgreSQL verification and adjudicated retrieval/outcome quality remain |
| 6–10 | LangGraph workflow and four schema-bounded specialist agents | Deterministic typed stages, a graph-grounded proposal/verifier and an isolated replay-validating advisory consumer are implemented; real provider adapter and durable service/checkpoint workflow integration remain |
| 10–13 | Salesforce services and five MCP adapters | Three foundation tools exist; governed live reads, policy/audit envelope and remaining adapters pending |
| 13–16 | Playwright worker and generic locator-ranking/healing path | Offline locator, browser worker, fixed-alias session broker, pinned coordinator and diagnostic command foundations are implemented; a 2026-09-10 real-org read-only diagnostic passed with zero acceptance credit. Exact target/receipt binding, authorization, action application, before/after persistence and accepted live receipts remain |
| 16–20 | Next.js dashboard: command center, run timeline, impact/tests, healing and governance | Polished command center and governance view implemented; complete timeline/test/healing/degradation views pending |
| 20–24 | Unit/contract/golden tests, live integration correction and rehearsal | The worktree collects 904 Python and 46 Playwright unit/contract tests, including mocked dashboard, candidate, target, receipt and browser boundaries; a frozen full post-integration run, real vertical E2E, live receipts and three golden scenarios remain pending |

## Non-substitutable live Salesforce completion boundary

The Salesforce part of the live integrated demo is accepted only when every required gate in
`config/live-salesforce-acceptance-profile.json` has a current, trusted, source-bound receipt from
the configured org machine. The profile explicitly requires host-owned non-production enrollment,
machine-local CLI authentication, standard REST connectivity, source-contract-derived custom API
execution, scoped Metadata API retrieval and hashing, ephemeral Playwright session handoff, live
Lightning assertions, selected live-org Apex tests, browser recovery/readback and the complete
deployed-candidate/restore campaign.

No live gate may be satisfied by another gate, a fixture, mock, configuration value, historical AUT
result, manual CLI output or an already-open browser tab. Offline tests may prove safe refusal but
cannot increase live completion. Missing evidence is `BLOCKED` or `NOT_RUN`, not an inferred pass.

Every receipt must identify one of `LIVE_BASELINE`, `CANDIDATE_CHECK_ONLY`, `DEPLOYED_CANDIDATE` or
`RESTORED_BASELINE`. A connected baseline and a successful check-only validation do not prove the
candidate is deployed. The candidate claim additionally requires tested restore readiness, exact
check-only and deployment bindings, current-task mutation authorization, post-deploy metadata/API/
Lightning/Apex evidence, restoration and zero-residue reconciliation. Release authority remains
disabled. The detailed contract is
`Docs/18-live-salesforce-demo-execution-contract.md`.

Passing all Salesforce campaign gates is necessary but not sufficient for whole-demo completion;
the provider, PostgreSQL, ordinary-run, real API/dashboard, receipt-replay and golden-scenario gates
below remain independently mandatory.

## Whole-demo gates

- Python imports and provider configuration pass without exposing keys.
- PostgreSQL checkpoint save/resume passes.
- Missing PostgreSQL falls back to an auto-created SQLite schema; missing SQLite falls back to a
  declared non-durable process cache without blocking JSON/graph analysis.
- Salesforce contract and graph load through relative/configured paths.
- Every required live Salesforce and candidate-campaign gate passes on the org machine; a CLI smoke
  alone is insufficient.
- Next.js production build passes under Node 26.5.
- Playwright launches the selected local browser and returns a structured snapshot.
- Three golden scenarios produce evidence-backed results.
- Governance scorecard uses measured results, never constants presented as execution.

## Contingencies

Environment- or session-dependent work is tracked in
`Docs/16-deferred-operator-actions.md`. A deferred operator action does not pause unrelated roadmap
work, and a capability remains unavailable or explicitly degraded until its acceptance evidence is
recorded.

- If LangGraph dependency installation fails under Python 3.14, stop and resolve that dependency; do not install an unapproved Python runtime or silently replace durable orchestration.
- Keep the current bounded in-process cosine index for the 24-hour demo. When persistent vector
  retrieval is added, implement only the ChromaDB adapter and preserve deterministic exact/graph
  and PostgreSQL full-text fallback. Do not add a PostgreSQL vector extension.
- If Playwright cannot download Chromium, use an installed Edge channel.
- If the runtime LLM endpoint is unavailable, demonstrate deterministic degradation and label semantic agents unavailable rather than simulating them.

## Current implementation checkpoint

The repository contains a typed LangGraph workflow with four deterministic specialist stages,
deterministic governance, source graph traversal, OpenAI/Azure provider configuration, a standalone
extension-free semantic index, PostgreSQL run/checkpoint wiring and relational foundation schema,
MCP foundations, a Salesforce CLI transport prototype, locator candidate ranking and a polished
dashboard. These pieces are not yet one complete product vertical.
The source graph now normalizes through a strict canonical ontology and source-owned profile; the
ontology/profile/normalized-graph identities are bound to every run. Missing record provenance
remains explicitly unverified rather than inheriting trust from the whole graph.
Semantic results and the isolated advisory pipeline are not yet consumed by ordinary assurance
runs. The browser worker/session/coordinator exist as offline-tested foundations, but live
PostgreSQL, model-provider, Salesforce evidence, trusted test execution, target-bound browser
application and receipt verification remain environment or implementation gates and must not be
represented as completed until they are exercised end to end.

## Demo critical path and exit criteria

Finish one reusable vertical before broadening foundations:

1. Restore coherent ontology, source-profile and policy identities and pass the full local gate.
2. Feed product-produced, side-qualified source-change seeds and trusted graph paths into one
   ordinary assurance run.
3. Invoke one real OpenAI or Azure specialist through strict structured output, cancellation,
   bounded usage and sanitized provider receipts; prove deterministic outage degradation.
4. Persist and resume that run against live PostgreSQL, while retaining truthful fallback tests.
5. Complete live org enrollment, local CLI authentication, standard REST, source-contract custom
   API, scoped Metadata API retrieval and selected live Apex-test gates with distinct receipts.
6. Complete the ephemeral browser-session handoff, live Lightning assertions and one authorized
   capture/candidate/approved-apply/readback journey plus ambiguity and authorization failures.
7. Complete the exact candidate check-only/deploy/post-deploy validation/restore/reconciliation
   campaign; baseline or check-only results cannot substitute for candidate-phase evidence.
8. Drive the real FastAPI service from the Next.js dashboard and retain source-bound Python and
   Playwright machine-readable reports.

The analysis demo may end `INCOMPLETE` and still be valuable. Release authority stays disabled.
The exit receipt must bind requirement IDs, exact test IDs, source snapshot, environment/tool
versions, policy identities, timestamps, persistence mode and sanitized artifacts.

## Reprioritized graph-reasoning sequence

Graph-grounded reasoning is now the primary intelligence path and precedes broad connector or UI
breadth. Existing deterministic graph work is preserved; semantic and agent work must converge on
the same typed context contract.

### Analysis and demo lane

This lane can proceed while release authority remains permanently fail-closed. It produces useful
impact, test and evidence-path analysis, but every public release decision remains `INCOMPLETE`.

| Priority | Capability slice | Acceptance result |
|---:|---|---|
| A1 | Canonical ontology plus source-profile relation mapping — foundation implemented | Normalization, independent profile pinning, source-schema bounds and adversarial mapping tests pass; completion still requires upstream record envelopes and proof that trusted first-source behavior is preserved |
| A2 | Direction-aware propagation and versioned analysis-risk policy — isolated foundation implemented | Policy/domain tests now prove relation direction/depth, bounded path enumeration, source/profile-bound ordered edge receipts, inverse/cycle/diamond behavior and no default risk; grounded risk-factor receipts, freshness fields and assurance-workflow integration remain |
| A3 | Deterministic `GraphContextPack` compiler — isolated foundation implemented | Analysis-only pack, stable hash, replay-validated canonical structure, candidate-only unresolved fragments, atomic count/character/byte budgets, explicit omissions and project/snapshot isolation; the A6 isolated consumer now replays it, while seed-rationale receipts, deterministic work-unit/depth accounting, provider-token/time budgets, governed fragment resolution, prior outcomes, semantic-index receipts and durable workflow composition remain |
| A4 | Hybrid candidate fusion — isolated foundation implemented | Exact, lexical, confirmed-graph and semantic candidate receipts are fused deterministically under request/context/channel/freshness roots and a separately pinned evaluation contract; protected deterministic tiers, upstream gaps, conflicts, omissions and outages are explicit; outputs remain non-authoritative. Real retrieval adapters, trusted A3 verification receipts and at least 20 adjudicated hybrid-quality cases remain |
| A5 | Graph-grounded specialist proposal/verifier — isolated foundation implemented | Directly replay-validated A3/A4 inputs, ontology-valid relations, ordered endpoint/evidence binding, strict captured-output verification, conflict abstention, secret-safe degradation and provider identity/timing/token receipts are tested; the A6 isolated consumer replays these artifacts. A real structured-output adapter with hard cancellation, durable workflow/checkpoint integration and at least 20 adjudicated quality cases remain |
| A6 | Assurance workflow integration — isolated consumer foundation implemented | Module-pinned, source-neutral specialist profiles can consume replay-verified A3/A4/A5 artifacts into a separate bounded advisory result; complete-request/root binding, governance replay, failure isolation, duplicate reconciliation, expiry and renamed-topology behavior are tested. Unsupported narrative cannot change deterministic facts or lift `INCOMPLETE`. Durable live-provider capture/consumption receipts, CAS/checkpoint integration, event outbox and service/LangGraph wiring remain |
| A7 | Outcome/evaluation memory — foundation implemented | Strict non-authorizing test/incident/human-correction records bind the originating run and source snapshot; typed incident targets, persisted lifecycle replay, append-only PostgreSQL/SQLite/JSON/cache adapters, service replay-on-read and truthful fallback health are tested. Live PostgreSQL concurrency, HTTP/MCP exposure, signed/tamper-evident anchoring, historical-policy replay, remaining outcome kinds and an adjudicated corpus remain |
| A8 | Dashboard evidence-path view — foundation implemented | Responsive command center distinguishes source-confirmed, human-recorded, inferred, contradictory and unresolved evidence; selected validations from execution receipts; strategy proposals from applied actions; exact recorded semantic/specialist participation from live availability; current decisions from audit-only history; and terminal missing-decision integrity failure. The current run contract exposes citation IDs rather than ordered path receipts and does not expose A6 advisory payloads, persistence health or release-authority receipts; runtime response validation and automated accessibility/contrast audits remain |

### Release-authority lane

These slices are required before the platform can emit any current `GO`, `CONDITIONAL_GO` or
`NO_GO`. They do not block the analysis/demo lane; the runtime interlock isolates the two concerns.

| Priority | Capability slice | Acceptance result |
|---:|---|---|
| R0.1 | Explicit trusted edge envelope — isolated foundation implemented | Canonical edge-artifact bytes are replayed through a pinned deterministic parser and bound to project/snapshot, raw and normalized graph, ontology/profile, extractor implementation, policy, evidence state and freshness. Missing, unknown, disabled, expired, duplicated or tampered inputs produce stable blocking release-only gaps while legacy analysis remains available. Upstream Salesforce/Git/source capture is still unattested, so every result remains `ANALYSIS_ONLY`, `release_eligible=false`, and cannot satisfy R0.2+ authority |
| R0.2 | Complete graph-path replay — isolated foundation implemented | A pinned policy derives every reachable material-target path from all graph nodes, then requires the exact union of current R0.1 envelopes and exact ordered structural/path replay. Empty, truncated, missing, extra, reordered, reversed, substituted, expired or tampered inputs create blocking release-only gaps. The result proves only local envelope/path coverage: change seeds and upstream capture remain unattested, every artifact is `ANALYSIS_ONLY` and `release_eligible=false`, and R0.3+ remain mandatory |
| R0.3 | Immutable release-input binding — candidate-composition foundation implemented | A separately pinned policy binds the complete v2 run/request candidate partitions, exact change/build bytes, current-replayed R0.2 paths and R0.1 roots, deterministic current-policy risk per stable structural path, advisory obligation/execution/conflict/human payloads, and current runtime policy identities into a canonical expiring manifest. Omission, duplication, unsafe locators, cross-root reuse, policy rotation, expiry, rehashing, fake paths and valid-path score swaps fail closed. Producer completeness is not yet non-caller-narrowable, so exact build/change, risk-factor, obligation/execution, conflict, approval and upstream-capture gaps remain; every result is `ANALYSIS_ONLY`, `release_eligible=false`, and R0.4–R0.6 remain mandatory |
| R0.4 | Verified change and obligation matrix — R0.4a–R0.4d local foundations, host composition, API capture and dashboard projection implemented | R0.4a derives complete base/candidate input trees and ADD/MODIFY/DELETE from actual local-Git bytes. R0.4b preserves the earlier replayed path foundation. R0.4c independently replays both trees into file inventory plus a typed, provenance-bound Salesforce semantic graph for explicitly supported source families, with recomputed deltas and tombstones. Each side discovers exactly one DX descriptor at any repository depth and resolves its package roots relative to that descriptor; ambiguity, aliases, unsafe/overlapping roots and lookalike source paths fail closed without narrowing whole-tree accounting. R0.4d replays that product graph, maps ADD from candidate, DELETE from base plus tombstones and MODIFY from independently preserved sides, explicitly accounts for nonsemantic changes and partitions every graph delta. A separately pinned, zero-scope-argument host pipeline composes and current-verifies R0.4a/R0.4c/R0.4d with exact receipt/gap contracts, configured nested-project binding, bounded safe projections, sanitized stage failures and no partial-artifact promotion. A fresh POST exposes only that projection, rejects all caller scope and never persists or contaminates legacy assurance evidence. Its independent dashboard panel bounds and strictly runtime-decodes the projection, replays canonical digests, clears stale evidence and keeps release state unchanged. `EXECUTED` is execution status only: evidence stays `INCOMPLETE`, `ANALYSIS_ONLY` and release-ineligible. Developer indexes never become product evidence. Next connect side-qualified seeds to trusted per-side path replay and bind trusted build outputs, complete impact/control obligations and exact-build execution receipts. Semantic-family, upstream, live-org, build, obligation, execution and release gaps remain |
| R0.5 | Scoped human confirmation and conflict sets | Separate expiring approval receipts; order-independent proposition conflicts; unresolved authoritative conflict blocks |
| R0.6 | Historical-decision revalidation | API/service reads expose only a current-policy effective decision; recorded legacy decisions remain audit-only |

Live Salesforce, test runners and browser capture remain necessary evidence producers, but their
interfaces should feed this graph/context architecture rather than creating parallel reasoning
paths. MCP remains a distribution adapter and does not move ahead of the intelligence slices above.

The current `RELEASE_EVIDENCE_MODEL_INCOMPLETE` interlock remains enabled throughout R0.1–R0.6 and
cannot be removed by a policy toggle. Release-authorizing decisions resume only through a reviewed
new governance/evidence schema with adversarial tests for every prerequisite.

R0.1 deliberately proves local canonical-edge artifact provenance, not the origin of the underlying
Salesforce, Git or test fact. Its built-in `canonical-edge-artifact-parser` is therefore not called a
source extractor. A future upstream capture adapter must attest the source read and produce the
artifact before any edge can become release evidence. R0.2 must consume only the complete,
currently replayed envelope set for every edge in a material path; it cannot infer completeness from
a self-hashed receipt or accept a partially verified subset.

## Post-demo vector milestone

Add ChromaDB behind `SemanticEvidenceIndex` only after the governed ingestion and retrieval contract
is complete. Acceptance requires snapshot-isolated collections, idempotent upsert/delete, embedding
version migration, project isolation, provenance returned with every hit, corrupted-index rebuild,
and contract parity with the in-process adapter. ChromaDB remains derived/rebuildable; PostgreSQL
retains authoritative run, evidence, chunk metadata/content, graph, audit and checkpoint records,
with SQLite, defined JSON and process cache providing progressively reduced fallback modes.

Before presenting PostgreSQL as complete agent memory, verify the outcome adapter against the live
target database and add application ports and tests for governed chunk/metadata ingestion, graph
edges, tool audit, healing outcomes, evaluations and approvals.
