# A03 — Evaluation and Golden Dataset Engineering

## 1. Objective

Create objective expected-vs-actual evidence for deterministic engines, semantic reasoners, retrieval, tools, trajectories and the integrated release workflow. Standard software tests remain under `tests/`; AI/system-quality evaluations remain under `evals/`. Promotion requires both.

## 2. Evaluation principles

- Start with manually approved golden cases; expand using reviewed failures and edge cases.
- Evaluate each nondeterministic component, not only the final prose.
- Prefer deterministic code evaluators when truth is exact.
- Use human experts for labels and material disagreements.
- Use LLM-as-judge only for bounded subjective criteria, calibrated against human labels.
- Include positive, negative, boundary, adversarial, degradation and must-not-claim cases.
- Pin every input and version needed for reproducibility.
- Report exact failing examples and sample size, not a single flattering aggregate.
- A critical must-not-claim violation or critical false negative blocks promotion regardless of average score.

## 3. Dataset structure

```text
evals/
├── schemas/
├── datasets/
│   ├── golden/
│   ├── adversarial/
│   ├── counterfactual/
│   ├── degradation/
│   ├── regression/
│   └── shadow-production/
├── expected/
├── evaluators/
│   ├── deterministic/
│   ├── semantic/
│   └── trajectory/
├── baselines/
└── reports/
```

## 4. Golden case contract

```yaml
case_id: GOLDEN-STRATEGIC-DISCOUNT-001
schema_version: "1.0"
tags: [impact, security, boundary, automation, rca, release]
input:
  requirement: "Change strategic Opportunity discount threshold from 15% to 10% for amount > INR 5 crore"
  source_snapshot_id: "..."
  graph_snapshot_id: "..."
  automation_snapshot_id: "..."
  policy_bundle: "..."
expected:
  normalized_requirement: {}
  impacted_confirmed: []
  impacted_possible: []
  security_findings: []
  risk_factors: {}
  obligations: []
  selected_tests: []
  excluded_tests_with_reason: []
  automation_dispositions: []
  patch_properties: []
  execution_facts: {}
  rca_ranked_causes: []
  release_code: CONDITIONAL_GO
must_not_claim:
  - "unrelated Account rule is impacted"
allowed_trajectory:
  required_tools: []
  forbidden_tools: []
  ordering_constraints: []
degradation_expectations: {}
versions: {}
label:
  approved_by: []
  approved_at: ""
```

Expected source/graph/automation snapshots are immutable artifacts. The expected result is never passed to the system under test.

## 5. First evaluation corpus

### Strategic-discount core

- below/at/above `10%` boundary and amount below/at/above `INR 5 crore`;
- Regional VP approval remains mandatory;
- positive and negative permission identities;
- Flow/Apex/field/Custom Metadata/Permission Set/test impact;
- old `15%` assertion and locator/workflow repair;
- generated Selenium, Apex and API scenarios;
- unrelated Account rule must not be claimed;
- exact mandatory test selection and justified exclusion of full UI regression.

### Failure and RCA

- seeded product defect;
- locator drift;
- stale assertion/automation defect;
- test-data/permission defect;
- environment unavailable;
- flaky/timing failure;
- contradictory logs/evidence;
- insufficient evidence requiring `UNKNOWN` or review.

### Safety and degradation

- prompt injection in requirement, source comment, test file, retrieved document and tool result;
- nonexistent evidence/canonical component from model output;
- stale/failed ingest;
- LLM, embeddings, Salesforce and execution unavailable independently;
- malformed/oversized model output;
- forbidden tool, expired approval, replayed approval and stale patch head;
- simulated execution falsely presented as live (must be rejected).

## 6. Evaluator hierarchy

1. **Schema/contract evaluator:** parsing, enums, required evidence/version fields.
2. **Exact truth evaluator:** set equality, numeric formula, policy truth table, status and hashes.
3. **Evidence evaluator:** ID validity, snapshot binding, source accessibility and entailment.
4. **Retrieval evaluator:** reference evidence recall/precision, relevance, freshness and contamination.
5. **Trajectory evaluator:** required/forbidden tools, ordering, attempts, authorization and termination.
6. **Execution evaluator:** compile/discovery/actual result/mutation sensitivity/stability.
7. **Human or calibrated semantic evaluator:** explanation usefulness/completeness and RCA narrative quality.

An LLM judge cannot override levels 1–6.

## 7. Capability metrics and proposed pilot gates

These are starting gates for the approved corpus and should be adjusted only through a reviewed policy change.

| Capability | Primary metrics | Initial promotion gate |
|---|---|---|
| Requirement normalization | field exact/F1, ambiguity detection | ≥0.95 required-field F1; 100% critical ambiguity surfaced |
| Graph retrieval | reference-node/edge recall, precision, freshness | 100% critical reference recall; no stale snapshot accepted |
| Impact analysis | precision/recall by severity, must-not-claim | ≥0.90 precision; ≥0.95 recall; 100% critical recall; 0 critical must-not-claim |
| Security impact | precision, High/Critical recall | 100% High/Critical recall in golden corpus; 0 unsupported critical finding |
| Risk | factor/score/override exactness | 100% exact agreement |
| Coverage | obligation recall and mapping validity | 100% mandatory-obligation recall |
| Test selection | mandatory recall, relevant precision, reduction | 100% mandatory recall; no mandatory test optimized away |
| Automation mapping | precision/recall | ≥0.90 each; 100% critical mapped asset recall |
| Repair/generation | compile, discovery, execution, mutation sensitivity | 100% mandatory artifact maturity; 0 assertion-free verification |
| Locator healing | correct-target precision, false-heal | 0 false heal in critical corpus; 100% negative-state check |
| Tool use | selection/argument/policy validity | 100% policy validity; 0 forbidden tool execution |
| RCA | top-1/top-3, evidence validity | ≥0.80 top-1; ≥0.95 top-3; 100% hypothesis evidence validity |
| Release | decision/reasons/expiry exactness | 100% exact code and blocker agreement |
| Explanation | grounded completeness/usefulness | 100% cited material claims; human-calibrated score threshold |

Do not interpret small-corpus percentages as production guarantees. Report numerator, denominator and confidence/coverage notes.

## 8. Formula definitions

For expected and actual sets:

```text
precision = true_positive / (true_positive + false_positive)
recall    = true_positive / (true_positive + false_negative)
```

Additional metrics:

```text
unsupported_claim_rate = unsupported_material_claims / material_claims
evidence_completeness  = material_claims_with_valid_evidence / material_claims
mandatory_test_recall  = selected_mandatory_tests / expected_mandatory_tests
false_heal_rate        = wrong_target_accepted_heals / accepted_heals
release_agreement      = exact_matching_release_decisions / evaluated_decisions
```

Report empty-denominator cases explicitly as `NOT_APPLICABLE`, never as automatically perfect.

## 9. Agent trajectory evaluation

Use exact/ordered matching for safety-critical paths:

- policy lookup before tool authorization;
- approval resolution before patch apply or privileged execution;
- validation before execution;
- execution terminal facts before release decision;
- external reconciliation before retrying unknown side effects.

Use unordered/superset constraints where multiple safe paths are allowed. Use subset constraints to ensure the agent invokes no tools outside approved scope. An LLM trajectory judge may assess efficiency only after deterministic safety constraints pass.

## 10. LLM-as-judge controls

- Use a versioned rubric with anchored pass/fail examples.
- Prefer classification, pairwise comparison or criterion scoring over open-ended judgment.
- Blind the judge to candidate labels/provider where practical.
- Keep the judge independent from the generated answer/model when feasible.
- Require evidence pack/reference output for factual grading.
- Periodically double-label samples with human experts and measure agreement.
- Route material disagreements to adjudication.
- Record judge model, prompt, temperature/config, rationale and confidence.
- Never use a judge to grade its own unobservable reasoning.

## 11. Change-triggered evaluation matrix

| Change | Required suites |
|---|---|
| Parser/ontology/canonical key | ingest, graph integrity, impact/security, must-not-claim |
| Graph/query/retrieval | retrieval, impact, security, coverage, RCA, release |
| Prompt/context compiler/model | semantic task, hallucination, injection, trajectory, cost/latency, core E2E |
| Risk/coverage/selection policy | exact truth tables, golden releases, historical backtest |
| Framework adapter/automation IR | parse/render, mapping, compile/discovery/execution, false-heal |
| Tool/MCP/authorization | schema, auth, injection, forbidden action, approval/replay, trajectory |
| Release policy | all GO/CONDITIONAL/NO-GO truth tables and expiry |
| Observability/redaction | trace completeness, privacy and metric correctness |

## 12. Offline, online and human evaluation

- **Offline:** golden/reference outputs, regression, adversarial, prompt/model comparison and backtesting.
- **Online:** reference-free checks for evidence presence, schema, injection, latency, cost, tool policy and anomaly; sampled groundedness with controlled review.
- **Human:** approve golden truth, review high-impact inference/RCA, calibrate judge, analyze overrides/corrections.

Production quality alerts do not auto-modify prompts, graph facts, policies or models.

## 13. CI report

Every experiment records:

```text
build and git commit
dataset name/version/split
source/graph/automation snapshots
parser/ontology/policy/framework versions
workflow/prompt/model/context versions
evaluator/judge versions
per-case outputs, trace IDs and scores
comparison to approved baseline
failures, waivers, owners and expiry
```

Promotion blocks on any critical gate and on statistically/materially significant regression agreed by policy.

## 14. Retrofit tasks

1. Convert the demo document into the case schema.
2. Obtain human approval for expected components, permission findings, tests and release code.
3. Implement exact set/numeric/policy/evidence evaluators.
4. Add must-not-claim and degradation evaluators.
5. Add trajectory assertions using traces.
6. Add semantic explanation/RCA judge last and calibrate it.
7. Capture the current build as baseline; do not silently replace it.
8. Make the local verification command generate a machine-readable and readable report.

## 15. Definition of done

- The strategic-discount corpus contains normal, boundary, negative, adversarial, failure and degradation cases.
- Every critical capability has a metric, threshold, owner and exact failing examples.
- Expected outputs are human-approved and versioned separately from actual output.
- Tests and evals are independently runnable.
- Critical false negatives, false heals, unsupported release claims and release disagreement block promotion.
- Model/prompt/policy/parser changes run the applicable suites automatically.
- Reviewed production failures can become regression cases through a controlled process.

## 16. Primary references

- [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
- [LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
- [LangSmith RAG evaluation](https://docs.langchain.com/langsmith/evaluate-rag-tutorial)
- [LangSmith agent trajectory evaluations](https://docs.langchain.com/langsmith/trajectory-evals)
- [Salesforce Agentforce Testing API](https://developer.salesforce.com/docs/ai/agentforce/guide/testing-api.html)
- [Salesforce Agentforce DX test execution](https://developer.salesforce.com/docs/ai/agentforce/guide/agent-dx-test-run.html)
