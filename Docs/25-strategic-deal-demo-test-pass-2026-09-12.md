# Strategic Deal Change Assurance Demo — test pass and bug map

Started: 2026-09-12T04:15:14+05:30

Scenario source: [Strategic Deal Change Assurance Demo](24-strategic-deal-change-assurance-demo.md)

Evidence directory: `.runtime/demo-evidence/20260912-041557`

## Compact slice matrix

| Field | Value |
|---|---|
| Objective | Run the Strategic Deal Change Assurance Demo as a test-first live/demo validation pass before fixing |
| Capability IDs | `assurance.candidate-comparison`, `reasoning.graph-impact`, `knowledge.evidence-graph`, `reasoning.graph-grounded-agent`, `quality.test-selection`, `governance.release-decision`, `runtime.salesforce-live-evidence`, `automation.browser-worker`, `automation.locator-healing`, `automation.applied-healing` |
| Acceptance boundary | Demo evidence only unless signed live Salesforce gates pass; no production/release claim |
| Retry rule | Maximum three retries per test family; broad foundation work is deferred unless it blocks truthful demo output or safety |
| Browser mode request | Prefer visible browser where supported; current live browser CLIs are still headless-only |

## Test evidence summary

| Test family | Evidence file | Result |
|---|---|---|
| Initial platform health | `platform-health.json` | API and dashboard initially unavailable |
| Platform launch recheck | `platform-health-after-api-console.json` | API and dashboard available after correct launch |
| API health | `api-health.json` | HTTP 200; OpenAI provider configured but blocked by credential-source conflict; PostgreSQL persistence active |
| Focused candidate/advisory Python tests | `pytest-candidate-advisory.log` | 116 passed, 1 warning |
| Browser worker/healing Playwright suite | `playwright-browser-hour4.log` | 158 passed, 1 failed |
| Live browser profile | `live-profile.jsonl` | READY; mutation business-action opt-in enabled |
| Live browser smoke | `live-smoke.jsonl` | PASSED; diagnostic-only; cleanup closed |
| Live deployed-candidate readback | `live-candidate-readback.jsonl` | PASSED; one `save-evaluate-live` candidate; cleanup closed |
| Live business action | `live-business-action.jsonl` | FAILED after submit; success text not matched; persistence not verified |
| Candidate analysis API | `api-candidate-analysis.json` | HTTP 503 `FOUNDATION_PIPELINE_UNAVAILABLE` |
| Direct service candidate analysis | `direct-service-candidate-analysis.json` | `FoundationPipelineUnavailable` |
| Live operator advisory API | `api-live-operator-advisory.json` | HTTP 503; candidate/LLM unavailable and live baseline blocked |
| Live Salesforce baseline API | `api-live-salesforce-baseline.json` | HTTP 409 `LOCAL_VALIDATION_PHASE_POLICY_INVALID` |

## Consolidated bug map

| Bug ID | Scenario step | Capability IDs | Symptom | RCA hypothesis | Severity | Priority |
|---|---|---|---|---|---|---|
| `DEMO-API-001` | Graph-grounded LLM advisory through running platform API | `reasoning.graph-grounded-agent`, `assurance.candidate-comparison`, `ui.assurance-dashboard` | `/api/v1/assurance-runs/analyze-current-candidate` returns `FOUNDATION_PIPELINE_UNAVAILABLE`; API health reports `PROVIDER_CREDENTIAL_SOURCE_CONFLICT` | The running API process sees conflicting provider configuration between process environment and `.env`, so provider calls are blocked even though `.env` contains the intended key | P1 | 1 |
| `DEMO-LIVE-BASELINE-001` | Live Salesforce evidence and operator advisory composition | `runtime.salesforce-live-evidence`, `governance.live-acceptance-evidence`, `demo.live-operator-advisory` | `/api/v1/live-salesforce/baseline` returns `LOCAL_VALIDATION_PHASE_POLICY_INVALID`; operator advisory is blocked | The pinned machine-local validation phase policy no longer matches the configured file/hash or allowed phase expectations | P1 | 2 |
| `DEMO-LIVE-BA-001` | Business-action browser acceptance | `automation.browser-worker`, `automation.applied-healing`, `runtime.salesforce-live-evidence` | Live worker fills six fields and submits one `save-evaluate-live` candidate, but success status is not observed and persistence is not verified | Salesforce save contract or synthetic payload is incomplete for the live Workbench route; likely field validation, unsupported value setting for one field type, missing required lookup, or LWC submit handler behavior | P1 | 3 |
| `DEMO-BROWSER-001` | Browser worker/healing verification gate | `automation.browser-worker`, `automation.locator-healing` | Playwright suite has 158 passed, 1 failed: production coordinator source invariant expected literal `mutationActionsEnabled: false` | Test became brittle after profile-level mutation flag was added. Need assert read-only coordinator behavior, not a stale literal string | P1 | 4 |
| `DEMO-BOOT-001` | Live agentic platform launch | `ui.assurance-dashboard`, `observability.agent-trace` | API and dashboard were initially unavailable; `python -m neo_sf_q_intel.api` exited without serving | Run command/runbook mismatch. Correct command is the console entrypoint `neo-api`; dashboard needs explicit port 3100 | P2 | 5 |
| `DEMO-BROWSER-UI-001` | User-visible live Salesforce browser execution | `automation.browser-worker` | User requested visible browser option; current profile/coordinator/CLI types force `headless: true` | Browser execution contract intentionally hardcoded headless. Need explicit safe `headed` operator mode if a visible browser is required for demo | P2 | 6 |

## Cross-connection and dependency analysis

```text
DEMO-API-001
  -> blocks real LLM advisory through FastAPI/dashboard
  -> blocks operator-visible proof for graph-grounded agent

DEMO-LIVE-BASELINE-001
  -> blocks live Salesforce diagnostic baseline API
  -> blocks live operator advisory composition even if DEMO-API-001 is fixed

DEMO-LIVE-BA-001
  -> blocks full live business-flow acceptance
  -> does not block read-only live smoke or deployed marker readback

DEMO-BROWSER-001
  -> blocks clean browser regression gate
  -> likely independent of DEMO-LIVE-BA-001 because live worker executed and reported sanitized failure correctly

DEMO-BOOT-001
  -> operational startup/runbook defect
  -> dependency for dashboard/platform demo, but not root cause of API provider or live baseline blockers

DEMO-BROWSER-UI-001
  -> demo-observability enhancement
  -> independent of live acceptance correctness
```

## Two-hour fix allocation

| Window | Target | Exit rule |
|---:|---|---|
| 0-25m | `DEMO-API-001` provider conflict | API health provider status becomes ready and candidate API reaches model-backed candidate path, or exact non-secret blocker is recorded |
| 25-50m | `DEMO-LIVE-BASELINE-001` phase policy | Live baseline endpoint no longer fails with `LOCAL_VALIDATION_PHASE_POLICY_INVALID`, or policy mismatch is recorded with required operator action |
| 50-85m | `DEMO-LIVE-BA-001` post-submit classification | Capture sanitized post-submit validation/error reason and attempt one payload/route correction only if low-risk |
| 85-105m | `DEMO-BROWSER-001` red test | Replace stale source-string assertion with behavior/source invariant and rerun focused browser spec |
| 105-115m | `DEMO-BOOT-001` runbook/script | Record correct launch command or add a safe local launch script if already supported by existing conventions |
| 115-120m | Retest/report | Rerun focused checks for fixed items; anything incomplete remains in bug map with next action |

## Claim boundary after this pass

Passing evidence exists for:

- focused candidate/advisory unit and integration tests;
- live Salesforce browser profile generation;
- live browser smoke;
- live deployed-candidate marker readback;
- browser worker/healing behavior except one stale invariant test.

Do not claim yet:

- running-platform LLM advisory through API/dashboard;
- live operator advisory success;
- live Salesforce baseline acceptance;
- full live business-action acceptance;
- visible-browser live execution;
- production/release readiness.
