# Connectors and MCP

## Shared services

FastAPI, agents and MCP call the same application services. MCP contains no domain logic.

Implemented MCP tools:

1. `search_evidence`
2. `analyze_change`
3. `inspect_salesforce`

Next adapters after live-environment validation:

1. `run_selected_tests`
2. `capture_and_heal_ui`

They are deliberately not exposed as pretend tools: test execution has Salesforce side effects,
and browser healing must first use the real machine-local session handoff and audit path.

## Salesforce transport

Salesforce CLI is the local authentication broker. The adapter uses structured JSON, enforces minimum compatible CLI version, redacts credential-shaped fields and never calls `org display --verbose`.

- Operator alias: read/query/custom REST, scoped tests and task-authorized synthetic writes.
- VP alias: only genuinely assigned approval work and permitted browser/API operations.
- Authentication is machine-local; never copy `.sf`, `.sfdx`, auth URLs or tokens.

## Tool policy

Each tool declares capability class, read/write effect, allowed personas, timeout, result size and audit behavior. Arbitrary shell, arbitrary SOQL, delete, permission administration and unrestricted metadata deployment are not exposed.
