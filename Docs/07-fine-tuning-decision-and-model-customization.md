# A07 — Fine-Tuning Decision and Model Customization

## 1. Current decision: `HOLD`

Do not fine-tune a model for the current 70% implementation. The reported problems are primarily architecture, grounding, persistence, integration, evaluation and enforcement problems. Fine-tuning cannot make fake graph data real, connect the database, enforce authorization, produce deterministic release truth or prove execution.

Reconsider only after the entry gates in Section 6 pass and repeated eval evidence isolates a behavior/consistency failure that other levers cannot solve economically.

## 2. Choose the correction by failure type

| Failure | Correct first lever | Fine-tuning? |
|---|---|---|
| Missing/current/proprietary Salesforce knowledge | Evidence graph, retrieval, source/API integration | No |
| Wrong impacted components/security facts | Parser, graph, deterministic propagation, labels | No |
| Risk/coverage/test/release error | Deterministic code/policy | Never |
| Invalid JSON/enums/arguments | Structured/strict output, schema, tool contract | Usually no |
| Prompt ignores task format/style inconsistently | Prompt/examples/model choice and eval | Candidate later |
| Consistent domain classification failure with strong labelled data | Prompt/RAG baseline, then SFT experiment | Possible |
| Preferred explanation tone/structure | Prompt/templates; DPO/SFT only at scale | Possible but low priority |
| Hallucinated org truth | Better context/evidence/claim validation/abstention | No |
| Tool misuse/unauthorized action | Tool design, least privilege, policy, approval | Never |
| High latency/cost for stable semantic task | Routing/caching/smaller model; distillation/tuning if supported | Possible later |
| RCA ranking inconsistent despite complete evidence | Features/rubric/prompt/model; labelled tuning experiment | Possible later |

## 3. Fine-tuning may improve

- consistency of a narrow, stable task;
- adherence to a specialized output behavior when schemas/prompts are insufficient;
- classification/ranking patterns demonstrated by many high-quality examples;
- latency/cost by enabling a smaller model to match an approved larger-model baseline;
- style/tone preferences at sufficient volume.

It does not reliably provide current external knowledge, citations, authorization, deterministic calculation, data integration or safety guarantees.

## 4. Candidate tasks for this platform

Possible future experiments, in descending plausibility:

1. requirement-to-`RequirementSpec` extraction for a stable Salesforce vocabulary;
2. semantic candidate link ranking, never confirmation;
3. automation-impact classification or repair-plan selection after deterministic features;
4. RCA hypothesis ranking from a fixed evidence-pack schema;
5. concise evidence-grounded explanation style.

Never fine-tune to decide confirmed impact/security truth, risk, mandatory coverage, test selection, approval or release code.

## 5. Preconditions and data quality

Training data must:

- be human-approved and representative of actual task inputs;
- use the same system/prompt, RAG/evidence-pack and tool/output format expected in production;
- contain correct abstention, ambiguity, negative and adversarial examples;
- exclude secrets, unapproved client data and unreviewed model-generated labels;
- include provenance, consent/usage rights, classification and deletion policy;
- be split by logical case/source so near-duplicates do not leak across train/validation/test;
- preserve a locked holdout never used for prompt or training decisions;
- version label guidelines and adjudicate expert disagreement.

Practical entry minimum: at least 100 high-quality labelled examples for the exact narrow task, with sufficient failure/negative/abstention diversity, plus an independent holdout. More examples may be required; dataset quality and distribution matter more than reaching a number.

## 6. Entry gates

Fine-tuning is eligible for an experiment only when all are true:

- current architecture conformance P0/P1 issues are resolved for the task;
- real input, context, persistence and output-validation paths exist;
- a task-specific offline corpus and baseline are approved;
- at least three prompt/context/model-routing experiments have been measured;
- the failure persists with correct and sufficient retrieved evidence;
- errors are predominantly consistent behavior/format/ranking errors, not missing knowledge or bad deterministic logic;
- provider/model tuning availability, data terms, cost, lifecycle and rollback are approved;
- expected improvement justifies training/hosting/maintenance cost;
- owner, monitoring, retraining/decommissioning and incident responsibilities are assigned.

## 7. Experiment design

```text
freeze dataset and label guide
→ establish base-model + prompt/RAG benchmark
→ isolate train/validation/locked test splits
→ train smallest justified candidate
→ evaluate quality, hallucination, abstention, safety, latency and cost
→ red-team and trajectory/tool tests
→ shadow/canary against champion
→ human review of disagreements
→ promote, reject or revise
```

Compare against the best non-tuned baseline, not the original weak implementation.

## 8. Promotion gates

- no regression in critical false negatives, unsupported claims, injection/tool safety or abstention;
- statistically/materially meaningful gain in target metric on locked test;
- performance holds across source/repository/org variants;
- latency/cost objective met if that is the purpose;
- no memorization/privacy leakage in red-team checks;
- champion remains available for instant rollback;
- model/provider/base snapshot and training dataset remain within lifecycle support.

## 9. Model registry record

```yaml
model_artifact_id: ""
provider: ""
base_model: ""
customization_method: SFT
task: requirement_normalization
training_dataset_version: ""
validation_dataset_version: ""
locked_test_version: ""
training_config_hash: ""
prompt_context_contract_version: ""
quality_report_id: ""
security_report_id: ""
approved_by: []
status: CANDIDATE
rollback_model: ""
decommission_by: ""
```

## 10. Continuous monitoring

- target-task quality and slice metrics;
- unsupported claim, evidence validity and abstention;
- input/label/distribution drift;
- latency, tokens, cost and availability;
- safety/injection/tool-policy failures;
- correction/override/disagreement rate;
- provider/base-model deprecation and endpoint changes.

Fine-tuned behavior never bypasses runtime guardrails or claim validation.

## 11. Provider portability

Treat fine-tuning as an optional adapter capability. Provider offerings and model lifecycles change. Store vendor-neutral dataset/label/eval artifacts; keep canonical behavior in contracts, policies and tests. A tuned model must be replaceable without changing deterministic engines or the Evidence Graph.

Current vendor availability must be verified at experiment time. For example, platform documentation can deprecate or restrict fine-tuning surfaces, and older Claude fine-tuning examples may apply only to specific Bedrock models/regions.

## 12. Data and security risks

- training-set poisoning or incorrect labels;
- memorization/sensitive information disclosure;
- hidden bias toward one repository/org/framework;
- catastrophic regression on abstention/negative cases;
- base-model lifecycle/deprecation dependency;
- inability to delete a derived model when source data must be removed;
- false confidence because average score improved.

Require AI bill of materials, provenance, red teaming, deletion/decommissioning plan and independent holdout.

## 13. Retrofit tasks now

- Mark fine-tuning backlog item `HOLD — prerequisite eval evidence missing`.
- Build prompt/model/context registry and golden corpus.
- Classify every failure as context, deterministic logic, integration, schema, behavior, safety or cost.
- Add a “fine-tuning candidate” flag only to reviewed persistent behavior failures.
- Do not accumulate unreviewed traces as a training set.

## 14. Definition of done

- Team can explain why each failure maps to prompt/RAG/code/tool/tuning.
- No current correctness/safety gap is assigned to fine-tuning without entry-gate evidence.
- Candidate data is reviewed, provenance-bearing, versioned and split correctly.
- Any tuning experiment has an approved baseline, locked test, safety suite, champion/challenger rollout and rollback.
- Fine-tuned output remains subject to the same evidence, schema, policy and action controls.

## 15. Primary references

- [OpenAI: Optimizing LLM accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy)
- [OpenAI: Model optimization](https://developers.openai.com/api/docs/guides/model-optimization)
- [OpenAI: Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
- [Amazon Bedrock fine-tuning](https://docs.aws.amazon.com/bedrock/latest/userguide/custom-model-fine-tuning.html)
- [Anthropic Claude Cookbook](https://platform.claude.com/cookbook/)
