# L10 — Connectors and External Integrations

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Implement replaceable adapters for Salesforce, Git, LLM/embeddings, automation repositories and test execution |
| Owner | Integration engineer |
| Inputs/outputs | L01 port contracts only |

## Part A — Solution design

### Adapter catalogue

| Port | P0 | Later |
|---|---|---|
| `SalesforcePort` | Fixture adapter | Direct API, DX MCP, Hosted MCP |
| `GitPort` | Local Git | GitHub/enterprise SCM |
| `SemanticReasonerPort` | Approved enterprise LLM or deterministic stub | Additional providers |
| `EmbeddingPort` | Null/exact retrieval | Approved embeddings |
| `AutomationRepositoryPort` | Local repository/worktree | Enterprise SCM adapter |
| `AutomationFrameworkPort` | Selenium Java, Apex, API | Cucumber/Playwright/others |
| `TestPort` | Simulation/fixture | Live Apex/API/UI execution |
| `ArtifactStorePort` | Local content-addressed store | Approved object storage |

### Adapter policy

- Normalize errors: `AUTH`, `AUTHZ`, `RATE_LIMIT`, `TIMEOUT`, `UNAVAILABLE`, `INVALID_RESPONSE`, `UNSUPPORTED`, `CONFLICT`.
- Enforce timeouts, bounded idempotent retries, jitter and circuit breakers.
- Return canonical contracts only.
- Separate Salesforce read and execute identities.
- Validate OAuth issuer/audience/scope; do not pass incoming tokens blindly downstream.
- Fixture/live variants pass identical port contract tests.

### Capability discovery

Adapters report supported operations, environments, limits and safety class. Orchestration never assumes live Salesforce, embeddings, browser execution or writeback.

## Part B — Development notes

### Repository

```text
packages/connectors/
├── salesforce_fixture/
├── salesforce_live/
├── local_git/
├── llm/
├── embeddings/
├── artifact_store/
└── test_execution/
packages/framework_adapters/
```

### Implementation order

1. Fixture Salesforce, local Git, local artifact and simulation test adapters.
2. Enterprise LLM adapter.
3. Framework adapters used by the demo.
4. Direct Salesforce read/test adapter.
5. Embeddings and MCP-specific Salesforce adapters.

### Engineering rules

- Credentials are injected from approved secret provider, never returned/logged.
- SDK models do not cross the adapter boundary.
- External correlation IDs are retained for audit/reconciliation.
- Read/write capabilities use separate interfaces or explicit action policy.
- No domain rule inside adapter.

## Part C — Testing and definition of done

### Tests

- Shared port contract suite against every adapter.
- Auth/authz/rate-limit/timeout/unavailable/error normalization.
- Retry only idempotent safe operations.
- Fixture/live canonical parity.
- Token audience/scope and credential redaction.
- External unknown outcome and reconciliation.
- Capability discovery/unsupported behaviour.

### Definition of done

- Core flow works with fixture/local adapters only.
- Swapping fixture/live adapter requires configuration, not domain code change.
- All external failures become canonical typed failures.
- No credential or raw token appears in payload/log/artifact.
- Timeouts/retries/circuit breakers are configured and observable.
- Live adapter uses least-privilege non-production identity.
- Contract tests and security review pass.

### Integration handoff

- Publish capability matrix, configuration keys and allowlisted endpoints.
- Provide adapter fakes plus failure fixtures.
- Document credential scopes, quotas and reconciliation runbook.
