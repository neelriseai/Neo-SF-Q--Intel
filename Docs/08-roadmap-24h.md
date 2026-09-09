# Twenty-four-hour implementation roadmap

## Runtime authority

Build and final verification target the org machine: Python 3.14.3, Node.js 26.5.x, Salesforce CLI 2.148.3 and local PostgreSQL. The development machine may use newer compatible patch releases, but the compatibility gate is the org machine.

## Planned 24-hour schedule and current truth

| Hours | Planned outcome | Current status |
|---:|---|---|
| 0–2 | Repository, canonical docs, contracts, provider profiles and environment preflight | Foundation implemented; org-machine provider verification pending |
| 2–6 | Auto-created PostgreSQL run/checkpoint memory, SQLite/JSON/cache fallback, hybrid retrieval ports and snapshot checks | Run/checkpoint and replay-safe outcome-memory fallback foundations plus isolated deterministic candidate fusion are implemented; channel adapters, governed graph/metadata/audit ports, live PostgreSQL verification and adjudicated retrieval/outcome quality remain |
| 6–10 | LangGraph workflow and four schema-bounded specialist agents | Deterministic typed stages, a graph-grounded proposal/verifier and an isolated replay-validating advisory consumer are implemented; real provider adapter and durable service/checkpoint workflow integration remain |
| 10–13 | Salesforce services and five MCP adapters | Three foundation tools exist; governed live reads, policy/audit envelope and remaining adapters pending |
| 13–16 | Playwright worker and generic locator-ranking/healing path | Locator ranking library implemented; browser capture/apply worker pending |
| 16–20 | Next.js dashboard: command center, run timeline, impact/tests, healing and governance | Polished command center and governance view implemented; complete timeline/test/healing/degradation views pending |
| 20–24 | Unit/contract/golden tests, live integration correction and rehearsal | Unit, contract and mocked dashboard E2E exist; real vertical E2E and three golden scenarios pending |

## Gates

- Python imports and provider configuration pass without exposing keys.
- PostgreSQL checkpoint save/resume passes.
- Missing PostgreSQL falls back to an auto-created SQLite schema; missing SQLite falls back to a
  declared non-durable process cache without blocking JSON/graph analysis.
- Salesforce contract and graph load through relative/configured paths.
- Salesforce CLI read-only smoke passes on the org machine.
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

The repository now contains a typed LangGraph workflow with four deterministic specialist stages,
deterministic governance, source graph traversal, OpenAI/Azure provider adapters, a standalone
extension-free semantic index, PostgreSQL run/checkpoint wiring and relational foundation schema,
three MCP tools, a bounded Salesforce CLI adapter, locator ranking and the first dashboard.
The source graph now normalizes through a strict canonical ontology and source-owned profile; the
ontology/profile/normalized-graph identities are bound to every run. Missing record provenance
remains explicitly unverified rather than inheriting trust from the whole graph.
Semantic results are not yet fused into assurance decisions. Live PostgreSQL, model-provider,
Salesforce evidence, trusted test execution and browser-worker verification remain environment or
implementation gates and must not be represented as completed until they are exercised end to end.

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
| R0.2 | Complete graph-path replay | Every material release input cites every typed edge; tamper/removal/reversal/illegal signature makes release incomplete |
| R0.3 | Immutable release-input binding | Canonical digest covers change/build, graph/evidence/paths, risk, obligations/execution, conflicts and all policies; mutation/policy/expiry fails closed |
| R0.4 | Verified change and obligation matrix | Planned text alone cannot authorize release; each impact/control maps to exact-build mandatory test receipts |
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
