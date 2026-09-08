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

## Independent review lane

- For every coherent capability or design slice, start independent read-only reviewer sub-agents
  when available. Keep two lanes: (1) genericity/architecture/scope and (2) governance, evidence,
  safety and verification. The developer continues independent work while reviews run.
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
