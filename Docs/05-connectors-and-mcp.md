# Connectors and MCP

## Shared services

FastAPI, agents and MCP call the same application services. MCP contains no domain logic.

Foundation MCP tools:

1. `search_evidence`
2. `analyze_change`
3. `inspect_salesforce` is registered only as a typed `UNAVAILABLE`/`NOT_RUN` status projection.
   It never resolves an alias or invokes a subprocess until the host proves a configured,
   allowlisted, non-production alias before invocation.

Next adapters after live-environment validation:

1. `run_selected_tests`
2. `capture_and_heal_ui`

They are deliberately not exposed as pretend tools: test execution has Salesforce side effects,
and browser healing must first use the real machine-local session handoff and audit path.

## Salesforce transport

Salesforce CLI is the intended local authentication broker. The current transport prototype uses
structured JSON, performs basic route validation, redacts credential-shaped keys and never calls
`org display --verbose`. It does **not yet** enforce an execution-time CLI version, independently
classify/refuse production orgs, cap elapsed time/output size, restrict exact route/query/field
templates, reject duplicate JSON, or emit a durable typed audit receipt. Until those controls and
their failure tests exist, it is not a governed live-evidence adapter.

- Operator alias: read/query/custom REST, scoped tests and task-authorized synthetic writes.
- VP alias: only genuinely assigned approval work and permitted browser/API operations.
- Authentication is machine-local; never copy `.sf`, `.sfdx`, auth URLs or tokens.

## Tool policy

The target tool contract declares capability class, read/write effect, host-owned alias, allowed
personas, exact operation and field policy, timeout, result size and audit behavior. This is an
acceptance boundary, not a statement that every control is implemented. Arbitrary shell, arbitrary
SOQL, delete, permission administration and unrestricted metadata deployment remain prohibited.
