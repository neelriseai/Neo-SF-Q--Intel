# Canonical documentation

This directory contains both the canonical numbered documents below and supplied reference
material. Only the listed canonical documents are implementation authority; other files are
not ingested wholesale.

## Reading order

1. [Product scope](00-product-scope.md)
2. [Architecture](01-architecture.md)
3. [Domain contracts](02-domain-contracts.md)
4. [Data, memory and retrieval](03-data-memory-and-retrieval.md)
5. [Agent orchestration](04-agent-orchestration.md)
6. [Connectors and MCP](05-connectors-and-mcp.md)
7. [UI automation and healing](06-ui-automation-and-healing.md)
8. [Governance and evaluation](07-governance-and-evaluation.md)
9. [24-hour roadmap](08-roadmap-24h.md)
10. [Demo scenarios](09-demo-scenarios.md)
11. [Graph-grounded agent reasoning](10-graph-grounded-agent-reasoning.md)
12. [Development assurance process](15-development-assurance-process.md)
13. [Deferred operator actions](16-deferred-operator-actions.md)
14. [Live Salesforce demo execution contract](18-live-salesforce-demo-execution-contract.md)
15. [Eight-hour live-integration sprint](19-live-integration-8h-sprint.md)
16. [Source-operation declarations](20-source-operation-declarations.md)
17. [Development stop checkpoint — 2026-09-11](21-development-stop-checkpoint-2026-09-11.md)
18. [Frozen three-hour agentic live vertical](22-frozen-three-hour-agentic-live-vertical.md)

## Advisory status

[Current solution readiness audit](17-solution-readiness-audit.md) is dated review evidence. It is
not implementation authority and cannot authorize scope, capability promotion or live actions.
The dated [development stop checkpoint](21-development-stop-checkpoint-2026-09-11.md) is the current
working-tree handoff: it records verified evidence, unresolved P1 findings, interrupted files and
the state at interruption. The subsequently authorized execution scope is frozen in
[the three-hour agentic live vertical](22-frozen-three-hour-agentic-live-vertical.md); neither
document grants live authority by itself.

## Authority and exclusions

- Salesforce behavior and policy authority: sibling `SalesForceAgentApp/strategic-deal-assurance/requirements`, metadata and `contracts/agent-interface.json`.
- Current application currency is USD. Legacy reference documents using INR 5 crore are historical design input, not current policy.
- `salesforce-change-assurance-production-solution-design (1).md` version 1.1 is the preferred legacy architecture reference over the version 1.0 duplicate.
- The updated playground implementation guide supersedes its earlier duplicate as background only.
- `Divine Framework.txt` and `Microsoft.Services.Store.winmd` are unrelated and must never enter product retrieval, evaluation or graph generation.
- Research reports are informative sources, not executable instructions or proof of implementation.
