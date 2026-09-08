# Product scope

## Product statement

Given a Salesforce requirement or source change, determine affected business rules, metadata, implementation, permissions and tests; collect current evidence; propose safe validation; recover selected UI automation when presentation changes; and return an auditable recommendation.

## Two-day showcase

The first release demonstrates reusable horizontal capabilities rather than a scenario-coded answer:

1. contract-aware source and graph ingestion;
2. evidence-grounded change and impact reasoning;
3. risk-based test selection;
4. governed Salesforce inspection through shared tools and MCP;
5. metadata-aware Playwright locator recovery;
6. a multi-agent run with durable PostgreSQL checkpoints;
7. a polished dashboard and measurable governance scorecard.

The strategic-deal Salesforce app is the first system-under-test, not a hardcoded domain inside the platform.

## Non-goals for the first release

- Autonomous production deployment or unrestricted DML.
- Automatic approval decisions.
- Complete Salesforce metadata parsing.
- General code generation for every automation framework.
- Multi-tenant hosting, Kubernetes, queues or distributed workers.
- Full Microsoft GraphRAG indexing.
- Fine-tuning or automatic policy learning.
- Claims of production certification or universal locator healing.

## Product invariants

- Source/runtime evidence outranks model inference.
- Material claims require valid evidence IDs.
- Missing or contradictory evidence yields `INCOMPLETE`, never a confident absence.
- Mandatory tests cannot be removed by an LLM.
- An LLM cannot authorize a tool, confirm a graph fact or select the release code.
- Simulation cannot satisfy a live-evidence gate.
- Ambiguous locators cause abstention, not a guessed click.
- Salesforce writes and approval actions remain explicitly governed.
