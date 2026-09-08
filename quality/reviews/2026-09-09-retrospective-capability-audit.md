# Retrospective capability audit

Two independent reviewer lanes inspected the earlier implementation against the canonical scope,
genericity rules and governance contract. No P0 remained. Governance measurement and release truth
were corrected in the current slice. The following P1 gaps are visible roadmap work rather than
claims of completed capability.

| Priority | Capability | Current truth | Acceptance boundary / next action |
|---|---|---|---|
| P1 | `orchestration.multi-agent` | LangGraph currently coordinates deterministic typed stages. | Add independent provider-backed specialist ports, schema validation, deterministic provider degradation and negative/failure tests. |
| P1 | `automation.browser-worker` | Locator ranking and strategy proposal exist; no DOM capture or browser apply occurs. | Add ephemeral session handoff, current-DOM receipt, unique-candidate approval, apply/verify evidence and failure tests. |
| P1 | `runtime.salesforce-live-evidence`, `tools.mcp` | Bounded CLI inspection and three foundation tools exist. | Add non-production refusal, route/field policy, timeouts, redacted audit receipts and full MCP execution/failure tests before live evidence is admitted. |
| P1 | `reasoning.semantic-retrieval` | Standalone semantic search exists but does not influence assurance decisions. | Fuse typed semantic proposals into the evidence pack with provenance, ambiguity abstention and metamorphic tests. |
| P1 | `observability.agent-trace` | Bounded activity events and typed deliverables are emitted and persisted with runs. | Add durable tool/model audit ports and complete dashboard timeline, test, healing and degradation views. |
| P1 | `reasoning.graph-impact` | The ontology is versioned but includes relations shaped by the first source application. | Define canonical relation classes and a source-owned profile mapping before claiming cross-application ontology portability. |
| P1 | `quality.live-test-execution` | Release governance validates trustworthy receipts but no runner creates them. | Add a trusted-runner adapter and a real API-to-workflow-to-dashboard test plus three source-independent golden scenarios. |

Owners are the capability owners in `config/capability-scope.json`. These items remain
`FOUNDATION` or `NEXT`; none is demoed as an executed capability before its acceptance boundary
passes. The next horizontal slice is: changed source -> deterministic/semantic evidence pack ->
typed specialist proposal -> governed live read -> trusted test receipt or explicit unavailability ->
browser capture/heal -> durable audit/dashboard.
