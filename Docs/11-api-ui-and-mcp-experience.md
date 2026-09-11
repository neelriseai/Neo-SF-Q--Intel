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

### Current dashboard foundation

The implemented dashboard is a read-only typed projection of the assurance-run API. It provides a
responsive command center, keyboard-operable stable tab panels, evidence-state lanes, citation
integrity warnings, recorded specialist activity, validation/execution separation, non-applied
healing proposals, measured controls, guardrails, violations and current-versus-recorded decision
separation. It never turns a recorded activity into a live dependency-health claim and fails closed
when a terminal run lacks an effective decision.

The assurance-run API does not yet expose ordered path receipts, A6 advisory payloads or a
release-authority receipt, so the run view labels them as not exposed rather than synthesizing
them. `/health` persistence and degradation display, runtime validation of the legacy assurance-run
response, zero-sample presentation fixtures and an automated accessibility/contrast audit remain
follow-up work; the capability therefore remains `FOUNDATION`.

The API separately exposes `POST /api/v1/foundation/candidate-evidence` for a fresh host-owned local
Git candidate capture. It accepts an empty request only: any body, query parameter or scope-bearing
header is rejected before service invocation. Successful or expected abstaining captures return
only the bounded `CandidateFoundationEvidence`; the full verified-change, graph and operation-seed
artifacts remain request-local. Missing configuration or current-verification races return a fixed,
typed, `ANALYSIS_ONLY`/`INCOMPLETE`/release-false problem without exception or path text. There is no
GET, list or persistence route. The endpoint does not extend `AssuranceRun` and cannot affect a
release decision.

The dashboard now owns this projection in a separate panel and client state. A fresh, bodyless POST
is bounded before JSON parsing and the response is accepted only after exact schema, authority,
stage order/state/capability, permanent-gap, receipt-lineage, canonical timestamp and canonical
digest replay. A rejected, unavailable, superseded, timed-out or expired capture removes all prior
stage evidence. Valid abstention remains distinct from transport failure. The view renders all
reported measurements, receipts and blocking gaps while stating that local foundation execution is
not evidence of a live org, deployment, build, tests, approval, impact completeness or release
readiness.

`POST /api/v1/assurance-runs/analyze-current-candidate` runs the full host-owned candidate
analysis but returns a separate, maximum-32-KiB `CandidateAssuranceView`. The view contains only
bundle/graph/seed receipt roots, one cross-bound analysis identity, bounded side summaries and run
references. It never embeds the verified-change set, produced graphs, operation-seed artifact or
complete runs. The dashboard retrieves the selected run through the ordinary run resource and
runtime-validates its run/trace, project, source snapshot/graph, ontology, source profile, reasoning
policy/evaluation and decision identities against the selected side before rendering it. The full
`CandidateAssuranceBundle` remains an internal persistence and replay contract.

Candidate component runs and their full bundle publish in one repository transaction and become
immutable through explicit bundle-to-run links. LangGraph checkpoints are not part of that
transaction: both bundle and view therefore declare
`checkpoint_commit_scope=EXCLUDED_FROM_BUNDLE_TRANSACTION` and retain the blocking
`CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE` gap. Durable checkpoint staging/outbox work must
close that gap before crash-safe resume can be claimed.

The dashboard also exposes a separate read-only durable campaign replay panel backed by
`GET /api/v1/live-campaigns/{campaign_id}/status`. The client accepts only a bounded campaign
identifier and runtime-decodes the closed 15-gate projection. It cannot submit receipts, targets,
Salesforce operations or authority, and it always keeps locally valid receipts distinct from the
accepted completion numerator. The current server projection is intentionally incapable of a
release claim, so the UI renders incomplete, zero-accepted and release-ineligible states exactly as
received. This panel proves the Next.js-to-FastAPI-to-service-to-ledger status path; it does not yet
prove a frontend-to-live-Salesforce execution journey.

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
