# Neo SF Q-Intel

Neo SF Q-Intel is an evidence-grounded, multi-agent Salesforce change-assurance platform. It combines deterministic source/graph analysis, bounded LLM reasoning, risk-based test selection, governed Salesforce tools, metadata-aware Playwright healing and measurable AI-governance controls.

The authoritative Salesforce system-under-test remains the sibling `SalesForceAgentApp/strategic-deal-assurance` repository. This repository consumes its versioned contract and generated evidence graph; it does not copy Salesforce authentication or app source.

## Runtime profiles

- `AI_PROVIDER=openai`: local development with OpenAI credentials.
- `AI_PROVIDER=azure_openai`: org-machine execution with Azure OpenAI deployments.

Both profiles use the same `ReasoningModel` and `EmbeddingModel` ports. Provider secrets are environment-only.
`SOURCE_GRAPH_SHA256` is a non-secret trust anchor: update it only after independently reviewing
and regenerating the configured source graph. A mismatch prevents its nodes from becoming
confirmed evidence.

## Repository map

```text
apps/web/            Next.js dashboard
packages/browser/    TypeScript Playwright worker
src/neo_sf_q_intel/  FastAPI, domain, agents, retrieval, tools and persistence
migrations/          PostgreSQL schema
Docs/                Canonical numbered docs plus supplied reference material
tests/               Python unit, contract and golden evaluations
```

Read [Docs/README.md](Docs/README.md) before implementation or ingestion.

## Local start

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
npm install
.\.venv\Scripts\neo-preflight
```

Set only one provider profile in `.env`. `AI_PROVIDER=openai` uses the OpenAI model names;
`AI_PROVIDER=azure_openai` uses the Azure endpoint, API version and deployment names. Do not
put model names in Azure deployment fields unless those are the actual deployment names.

Start the API and dashboard in separate terminals:

```powershell
.\.venv\Scripts\neo-api
npm run dev
```

The API uses PostgreSQL and durable LangGraph checkpoints when `DATABASE_URL` is configured.
Without it, the health endpoint reports `in-memory-demo`; that mode is for local UI work only.

## Genericity and architecture gates

The platform core is checked for scenario-specific project names, object/field identifiers, org
aliases, REST routes, absolute paths and forbidden layer imports. Capability maturity is tracked
in `config/capability-scope.json`; incomplete layers stay `FOUNDATION` or `NEXT` rather than being
presented as finished. Node roles, severities, traversable relations and result bounds live in
the reviewed, versioned `config/reasoning-policy.json`, not in business-scenario code.

```powershell
python scripts/catalog/build_project_index.py
.\scripts\quality\check.ps1 -Full
git config core.hooksPath .githooks
```

The generated `knowledge/project-index.json` is the fast context entry point. It contains the
canonical reading order, content hashes and capability inventory. `knowledge/application-graph.json`
contains deterministic local import/reference edges. Run the generator after material changes;
the hook refuses stale knowledge.
