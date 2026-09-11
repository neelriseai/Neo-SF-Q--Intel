# Neo SF Q-Intel

> Development was stopped on 2026-09-11 before any live Salesforce mutation. See the
> [current development checkpoint](Docs/21-development-stop-checkpoint-2026-09-11.md) for verified
> tests, open P1 findings, interrupted files and the exact resume order.

Neo SF Q-Intel is an evidence-grounded Salesforce change-assurance platform under active development. The current vertical slice combines deterministic source/graph analysis, typed specialist workflow stages, risk-based test selection, healing-strategy proposals, executable AI-governance controls and a host-owned no-argument live-baseline service for strictly bounded Salesforce reads. That service is verified offline but disabled until private non-production authority is reviewed and pinned; no live acceptance receipt exists. Provider-backed specialist reasoning, trusted candidate-phase test execution, accepted browser capture/healing and persistent ChromaDB retrieval remain explicit roadmap capabilities.

The authoritative Salesforce system-under-test remains the sibling `SalesForceAgentApp/strategic-deal-assurance` repository. This repository consumes its versioned contract and generated evidence graph; it does not copy Salesforce authentication or app source.

## Runtime profiles

- `AI_PROVIDER=openai`: local development with OpenAI credentials.
- `AI_PROVIDER=azure_openai`: org-machine execution with Azure OpenAI deployments.

Both profiles use the same `ReasoningModel` and `EmbeddingModel` ports. Provider secrets are environment-only.
The configured Salesforce source generator publishes the application graph first and its
digest-bearing project index last. Neo validates both the generated binding and the indexed source
snapshot before any graph node can become confirmed evidence. The independently versioned source
profile remains pinned by `SOURCE_GRAPH_PROFILE_SHA256`; graph regeneration does not rewrite that
policy identity.

## Repository map

```text
apps/web/            Next.js dashboard
packages/browser/    TypeScript locator-ranking foundation and browser-worker roadmap
src/neo_sf_q_intel/  FastAPI, domain, workflow stages, retrieval, tools and persistence
migrations/          PostgreSQL schema
Docs/                Canonical numbered docs plus supplied reference material
tests/               Python unit, contract and frozen evaluation-boundary tests
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

The API prefers PostgreSQL when `DATABASE_URL` is configured. `POSTGRES_SCHEMA` selects Neo's
dedicated private namespace and defaults to `neo_sf_q_intel`; `public`, system, mixed-case and unsafe
identifiers are rejected. The current runtime wires complete
run documents and durable LangGraph checkpoints; its schema also reserves governed relational
tables for evidence chunks, graph edges and tool audit while their application ports remain a
foundation milestone. Required PostgreSQL tables and reviewed migrations apply only after an exact
ownership/version marker is validated. Other schemas—including legacy similarly named tables and
unrelated pgvector tables—are neither migrated nor mutated. If
PostgreSQL is absent or unreachable, the runtime auto-creates the configured `SQLITE_PATH` and
stores complete run documents there. If SQLite also fails, versioned JSON source artifacts and
process memory keep deterministic analysis available. `/health` reports the active mode and reason
for degradation rather than implying PostgreSQL durability.
Persistent vector retrieval is a later ChromaDB capability. PostgreSQL remains relational and no
PostgreSQL vector extension is part of the supported org-machine architecture.

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
