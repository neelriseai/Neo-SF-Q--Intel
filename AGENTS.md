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
