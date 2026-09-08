# L11 — API, UI and MCP Experience

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Expose the same assurance application services through REST, a usable web dashboard and a compact MCP surface |
| Owner | API/UI engineer |
| Inputs | Authenticated user/tool requests |
| Outputs | Versioned resources, progress, evidence and action requests |

## Part A — Solution design

### REST boundary

Base `/api/v1`. Key resources:

- assurance runs/status/events;
- impact, security, risk, coverage and test plan;
- automation impact/plan/patch/validation;
- execution/RCA/release decision;
- evidence and bounded graph views;
- approvals, feedback, cancellation and project ingest.

Rules: idempotency key for mutations, `202` for long work, ETag/If-Match for concurrency, Problem Details errors, cursor pagination, bounded graph/evidence and correlation IDs.

### UI information architecture

```text
Run Overview
Impact | Security | Risk | Coverage | Test Plan
Automation Impact | Repair Plan | Generated Assets | Patch Diff | Validation
Execution | RCA | Evidence | Release Decision | Audit/Approvals
```

Every conclusion/changed line/locator/assertion opens its evidence. Simulation/inference/low-confidence states are visually explicit. Action controls are role/policy/approval aware.

### MCP surface

High-level tools only: analyse change, read evidence/impact/security/test plan, analyse automation impact, generate/validate patch, request execution, diagnose failure and read release recommendation. Direct protected-branch writeback is not exposed.

Resources use stable URIs such as `change://{runId}` and `graph://{project}/component/{key}`.

## Part B — Development notes

### Repository

```text
apps/api/
apps/web/
packages/mcp_server/
contracts/openapi/
```

### Implementation order

1. Read/create run REST endpoints and OpenAPI tests.
2. Server-sent progress events.
3. UI run overview and report tabs.
4. Evidence/graph explorer.
5. Automation patch/validation views and approval controls.
6. MCP tools/resources mapped to application services.

### Engineering rules

- UI and MCP never import domain or orchestration internals.
- Sensitive evidence is separately authorized.
- Large artifacts return links/references, not giant payloads.
- Client cannot choose decision code/confidence/evidence classification.
- Accessibility and keyboard navigation are P0 for operational UI.

## Part C — Testing and definition of done

### Tests

- OpenAPI/schema/Problem Details contracts.
- AuthN/AuthZ and restricted evidence.
- Idempotency, ETag conflict, pagination and size/depth limits.
- SSE reconnect and terminal status.
- UI loading/empty/error/inferred/simulated states.
- Evidence deep links and patch diff rendering.
- Approval-role visibility and forbidden action.
- REST/MCP parity for same run/resource.
- Accessibility and targeted browser tests.

### Definition of done

- User can submit and follow an end-to-end run.
- All major reports, automation artifacts, validation, RCA and decision are visible.
- Every material statement links to evidence.
- UI/MCP call the same application services and return the same run IDs.
- Unauthorized actions/evidence are blocked server-side and hidden appropriately.
- Long operations, failures and recovery are understandable without logs.
- OpenAPI/MCP schemas are versioned and contract-tested.

### Integration handoff

- Publish OpenAPI and MCP tool/resource catalogue.
- Provide UI route/state map and role matrix.
- Document API limits, error codes and accessibility results.
