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

- Read `knowledge/project-index.json` and `knowledge/application-graph.json` before scanning
  broad documentation. Follow the index `readOrder` for authoritative context.
- After adding, removing, moving or materially changing code, policy or canonical docs, run
  `python scripts/catalog/build_project_index.py`. Before commit, `--check` must pass.
- Generated graph edges are discovery aids, not authority or permission.

## Independent review lane

- For every coherent capability or design slice, start one read-only reviewer sub-agent when
  available. The developer continues independent work while it checks scenario coupling,
  weak/thin layers, scope erosion, evidence/governance gaps and missing failure tests.
- Send the reviewer a concise diff/design summary at the next natural boundary rather than
  blocking each file edit. Resolve all P0/P1 findings before commit or record the owner/status
  in `quality/reviews/` and keep the capability below `IMPLEMENTED`.
- When sub-agents are unavailable, run `scripts/quality/check.ps1 -Full` and apply the checklist
  in `Docs/15-development-assurance-process.md` as the fallback independent review.
