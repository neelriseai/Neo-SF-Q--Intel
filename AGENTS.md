# Neo SF Q-Intel

## Product boundary

Build an evidence-grounded Salesforce change-assurance platform. Deterministic services own source facts, impact confirmation, policy enforcement, test obligations and release decisions. LLM agents may normalize, propose, rank and explain, but may not fabricate graph facts, authorize tools or approve releases.

## Runtime target

- Python 3.14.3
- Node.js 26.5.x
- Salesforce CLI 2.148.3 or newer compatible 2.x release
- Local PostgreSQL
- Windows without administrator installation rights
- Local Git; GitHub is optional transport, never a runtime dependency

## Safety

- Use only the explicitly configured non-production Salesforce aliases and synthetic data.
- Never commit credentials, tokens, Salesforce auth state, frontdoor URLs, raw org exports or machine-specific absolute paths.
- Keep repository paths relative. Resolve external roots through environment configuration.
- Reads are the default. Writes, tests with side effects, approvals and metadata operations require explicit policy and current-task authority.
- The administrator alias must never impersonate a VP approval decision.
- Do not weaken Salesforce IP/session/security settings for automation.

## Engineering

- Keep domain contracts independent of FastAPI, Next.js, LangGraph, MCP, Salesforce CLI and Playwright.
- Expose shared application services through API, MCP and agents; do not duplicate domain logic in adapters.
- Agents exchange typed state. Every material claim must cite evidence IDs.
- Inferred semantic edges remain distinct from confirmed source/runtime edges.
- PostgreSQL owns durable run/evidence state; chat history is not product memory.
- ChromaDB is the only supported vector store for future semantic retrieval. PostgreSQL remains
  relational storage and must not acquire a vector-extension dependency.
- PostgreSQL is primary non-vector memory. Startup must self-create its schema and fall back through
  SQLite, defined JSON artifacts and process cache without hiding the active durability mode.
- Add unit and contract tests for every capability and a failure-path test for every tool.
- Run formatting, type, unit and integration checks before commits.

## Genericity and scope protection

- Runtime code must not contain project names, Salesforce object/field names, org aliases,
  record IDs, or application-specific REST routes. Put examples in tests/docs and selected
  source-profile values in environment configuration.
- A capability is `IMPLEMENTED` only when its domain behavior, adapter, positive case,
  negative case and failure path exist. Use `FOUNDATION` for a real but incomplete vertical
  slice and `NEXT` for planned work; never let the dashboard imply a stronger status.
- Give every active requirement a stable requirement ID, capability ID and acceptance class.
  Trace it to exact test node IDs and a source-bound execution receipt; a test filename alone is
  discovery evidence, not acceptance evidence. Keep unit/contract, in-process functional, mocked
  browser, live dependency and adjudicated-golden evidence as distinct test levels.
- Do not promote a capability without current positive, negative, failure, degradation and
  scenario-independence evidence where applicable. Missing categories remain explicit gaps.
- Preserve the capability IDs in `config/capability-scope.json`. A design change may alter
  implementation, but silently deleting or narrowing an agreed capability is not allowed.
- Vague input must abstain. Renaming business entities or adding disconnected graph nodes must
  not change control flow or decision class.

## Fast context and knowledge maintenance

- For every development, debugging, patch, enhancement or impact-analysis task, first read
  `knowledge/project-index.json` and query `knowledge/application-graph.json` for the affected
  capability, files, imports and verification edges. Use those results to select only the relevant
  canonical documents and source files; do not rescan all documentation by default.
- Follow the index `readOrder` when broader authoritative context is genuinely required. Record a
  missing relation/file classification as a knowledge-maintenance gap instead of repeatedly working
  around an incomplete index.
- After adding, removing, moving or materially changing code, policy or canonical docs, run
  `python scripts/catalog/build_project_index.py`. Before commit, `--check` must pass.
- Generated graph edges are discovery aids, not authority or permission.
- Treat historical execution queues, review ledgers, chat summaries and demo runbooks as evidence of
  prior decisions, not active instructions. They may inform risk and known gaps, but the current
  user request, this `AGENTS.md`, current policy/config files and verified source state define the
  active scope. If an older two-hour/three-hour/demo note pulls work away from the current selected
  capability, record it as historical/deferred context and continue with the current scope.

## Focused development and context compression

- Make the smallest coherent, reviewable change. Prefer targeted patches and unified diffs; avoid
  unrelated formatting, large rewrites and full-file regeneration. Rewrites are permitted when
  required for correctness, generated output or a coherent refactor; explain material exceptions.
- Treat edits as explicit changes to behavior, structure and state. Use AST-aware or
  language-aware tooling when it is available and suitable, but do not require it for every
  language, generated artifact or documentation change.
- Start from scoped search, relevant symbols, project-index/graph routes and concise summaries.
  Then read the original implementation and dependencies needed to establish behavior, side
  effects, security and verification. Summaries, signatures, edge lists and indexes are navigation
  aids, never authoritative substitutes for source when behavior is changed.
- Describe tests as state transitions where useful: preconditions, action/event, expected state,
  externally observable effects and invariants. Keep expected results independent of the
  implementation and configuration under test.
- Reuse defined domain vocabulary and compact matrices for repeated development handoffs. Introduce
  a DSL only when its meaning is unambiguous, reviewed and measurably reduces recurring work.
- Define domain shorthand before use or link its canonical definition. Preserve exact code, API,
  object, field, capability, gate and policy identifiers. Do not use a shorthand whose meaning
  varies by scenario.
- Every condensed matrix, key-value record, edge list or schema must preserve applicable scope,
  triggering conditions, required behavior, exceptions and verification criteria. Distinguish
  requirements, observed facts, assumptions, proposals and unknowns. Missing information is
  unknown, not permission to infer a default.
- Keep authorized agent/review handoffs concise and structured: scope, changed files, capability
  IDs, decisions, verification evidence, uncertainties, gaps and next action. Prefer schemas for
  machine handoffs, but do not require minified JSON/YAML when readability or safety would suffer.
- Use maintained impact indexes and graph edge lists when useful; include source locations and
  freshness information, typed relationships and provenance where relevant, and verify relevant
  edges against source before relying on them. An omitted node, edge or condition does not prove
  absence.
- Correctness, readability, security, evidence integrity and required verification take precedence
  over context or token reduction. If compression hides uncertainty, expand context rather than
  guessing. Adopt compression changes only when representative tasks show reduced total token use,
  including clarification and rework, without reduced task success.
- Do not allow previous demo data, fixture data, stale screenshots, old roadmap priorities or
  repeated historical failures to narrow the implementation into a scenario-specific solution.
  Re-check object/capability boundaries from source and tests before coding behavior.

## Independent review lane

- For architecture/design review, material design refinement, and whole-solution debugging, invoke
  a `gpt-6-astra` sub-agent with `xhigh` reasoning when that model is available. Give it the
  project index/graph route, affected capability IDs, acceptance boundary and current diff; require
  an evidence-cited P0/P1/P2 verdict. This is a standing project instruction and does not require
  the operator to change the main task model. If Astra is unavailable, record that explicitly and
  use the strongest available independent reviewer without weakening any gate.
- For every coherent capability or design slice, start independent read-only reviewer sub-agents
  when available and authorized by the current user request/runtime policy. Keep two lanes:
  (1) genericity/architecture/scope and (2) governance, evidence, safety and verification. The
  developer continues independent work while reviews run.
- Reviewers must challenge scenario/data coupling, hardcoding, pass-through or cosmetically named
  agents, weak/thin vertical slices, scope erosion, capability overclaims, missing negative/failure
  tests, and deviations from the canonical roadmap. A demo path is evidence for a reusable
  capability; it is never the implementation boundary.
- Send reviewers a concise capability/diff summary at natural integration boundaries, not after
  each file write. This preserves pace while catching design errors before they spread.
- Resolve every P0 and every P1 that contradicts a current implementation claim before commit.
  A genuine planned P1 may remain only when its capability is truthfully `FOUNDATION`/`NEXT` and
  the review ledger records its owner, capability ID, reason, acceptance boundary and priority.
  Never hide a gap by weakening a test or changing a label without changing the underlying claim.
- When sub-agents are unavailable, run `scripts/quality/check.ps1 -Full` and apply the checklist
  in `Docs/15-development-assurance-process.md` as the fallback independent review.

## Priorities and verification cadence

1. P0: credential/session safety, authorization, evidence integrity, destructive effects and false
   `GO` paths. Stop the slice and correct these immediately.
2. P1: end-to-end capability correctness, generic contracts, deterministic degradation, replayable
   policy identity, persistence integrity and truthful capability/UI status. Resolve before commit.
3. P2: breadth, performance, usability and polish that do not invalidate current evidence. Record
   these without displacing P0/P1 work.

- Tie every task to one or more existing capability IDs and an explicit acceptance result before
  coding. If architecture changes, update the canonical document and scope manifest first or in the
  same coherent slice.
- Develop vertical slices through contract, service/domain behavior, adapter boundary, observable
  result and tests. A schema, interface, prompt, agent label or dashboard card alone is not a
  completed capability.
- Run focused tests during implementation. Before integration, require positive, negative,
  failure/degradation and scenario-independence or metamorphic tests where applicable. Run the
  full Python, type, browser and dashboard gate before commit/push.
- Re-audit earlier code when a new invariant is introduced. Apply the invariant consistently to
  stored runs, adapters and UI claims rather than protecting only new code.
- A policy, ontology or source-profile identity change is one atomic consumer migration. Update and
  verify every dependent pin, default and replay boundary together; run focused load/startup checks
  before starting another slice. Never leave the runnable tree between incompatible identities.
- Prefer finishing the highest-priority reusable end-to-end vertical before adding another isolated
  foundation. A new foundation requires an explicit roadmap dependency that explains why the
  current vertical cannot proceed without it.
- Live Salesforce mutation is prohibited until the exact non-production org is independently
  classified and a versioned mutation-authorization receipt binds current-task authority, the
  fixed host-owned alias plus non-secret org fingerprint/class, exact source/build/manifest and
  operation set, check-only receipt, pre-state digest, bounded expiry, tested metadata-and-data
  restore artifact/receipt, and post-restore reconciliation including residue/deletes. Unknown or
  production org, dirty or mismatched source, partial check-only, stale restore proof, or caller
  alias/scope override is `POLICY_BLOCKED`/`NOT_RUN` before subprocess or browser action.

## Live Salesforce acceptance boundary

- Treat `config/live-salesforce-acceptance-profile.json` and
  `Docs/18-live-salesforce-demo-execution-contract.md` as the non-substitutable acceptance boundary
  for the live integrated demo. A fixture, mock, manual observation, historical screenshot or open
  browser tab may never satisfy a positive live gate or increase live completion.
- Before any product-controlled Salesforce subprocess or browser action, verify a current
  host-owned non-production enrollment, exact org/actor binding and task-scoped operation authority.
  Caller-supplied or unknown classification is blocked before dispatch; identity is revalidated
  after authentication and before dependent operations.
- Derive custom REST operations, Metadata API scope, selected tests and Lightning targets from the
  pinned source contract and evidence graph, then intersect them with host policy. Contracts,
  models and callers cannot grant authority or widen routes, fields, manifests, tests or personas.
- Keep CLI authentication, standard REST connectivity, source-contract custom API execution,
  scoped Metadata API retrieval, ephemeral Playwright session handoff, live Lightning assertions,
  selected live-org tests and browser recovery as separate receipt gates. Passing one never proves
  another.
- Preserve explicit `LIVE_BASELINE`, `CANDIDATE_CHECK_ONLY`, `DEPLOYED_CANDIDATE` and
  `RESTORED_BASELINE` evidence phases. Baseline and check-only evidence cannot validate the deployed
  candidate. A live-candidate claim requires exact deployment binding, post-deploy reconciliation,
  candidate-phase API/UI/test evidence and successful restore/residue reconciliation.
- Authentication material stays in the machine-local broker. A frontdoor URL is created only after
  classification, handed directly in memory to an isolated Playwright context and never stored,
  logged, printed, placed in a prompt or returned through MCP/API output.

## Agent observability and deliverables

- Every agent/capability stage emits a structured, bounded event containing run/trace ID,
  capability ID, agent/stage, status, input evidence IDs, output artifact IDs, policy versions and
  hashes, duration, degradation/gap codes and error class. Never log prompts containing secrets,
  credentials, tokens, frontdoor/session URLs or unredacted raw org payloads.
- Each agent returns a typed deliverable with: conclusion/proposal, cited evidence IDs, material
  assumptions, blocking and nonblocking gaps, deterministic measurements/guardrail outcomes, and
  the next permitted action. Narrative detail may explain this artifact but cannot replace it.
- Logging must be diagnostic, not noisy: no repeated full documents, hidden chain-of-thought,
  fabricated confidence percentages or duplicate events. Keep enough detail to replay decisions
  and identify which policy, source snapshot, runner and adapter produced each fact.
- Capability review evidence belongs in the structured review ledger. Runtime facts belong in
  governed stores. Chat messages are coordination context, not the system of record.
- Verification output must be durable and sanitized: assign a unique immutable test-run/execution
  ID plus a separate deterministic input/root digest (and optional idempotency key), and retain
  machine-readable Python and Playwright results, environment/tool versions, timestamps, source
  snapshot, policy identities and artifact indexes. Console-only output is diagnostic, not a
  current acceptance receipt.
- Until a versioned receipt schema and validator enforce those fields, receipt-based promotion is
  an acceptance target: no capability may be newly accepted from an ad hoc report.

## Graph-grounded reasoning

- The evidence graph and its ontology are the primary reasoning substrate. Before any future
  model-backed specialist reasons about impact, security, tests, healing or RCA, a deterministic
  context compiler must select a project/snapshot-isolated subgraph, evidence paths, source
  fragments, policies and prior outcomes within an explicit budget.
- Model/vector output is a proposal plane, never the authority plane. An agent may propose typed
  semantic relations only between entities visible in its context pack and must cite pack evidence.
  It may not create confirmed structural edges, grant permissions, satisfy execution gates or
  choose a release decision.
- Deterministic validation must reject cross-project/snapshot references, missing or expired
  evidence, unknown ontology mappings, unsupported paths and conflicting facts. Inferred edges stay
  `INFERRED` until independently confirmed; they cannot satisfy a blocking release gate.
- Keep the canonical ontology independent of the first Salesforce application. Source-owned
  profiles map application/vendor relation names into canonical relation classes. Adding a source
  must not require demo literals or business fields in the core reasoning policy.
- PostgreSQL owns authoritative graph provenance and outcome memory when those ports are complete;
  JSON/SQLite provide degraded factual continuity. ChromaDB is a rebuildable semantic candidate
  index only and must never become evidence authority.
- Do not call the platform or a capability `GraphRAG` unless it actually implements and evaluates
  the claimed GraphRAG retrieval modes, summaries, provenance and refresh semantics. Use
  “graph-grounded reasoning” or “ontology-guided evidence retrieval” for the architecture here.
