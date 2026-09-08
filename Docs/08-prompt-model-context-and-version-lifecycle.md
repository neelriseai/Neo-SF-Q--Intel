# A08 — Prompt, Model, Context and Configuration Lifecycle

## 1. Objective

Treat prompts and model configuration as production code: typed, versioned, reviewed, evaluated, progressively promoted and instantly reversible. The same semantic task must be reproducible from prompt, model, context and evidence versions.

## 2. Semantic task registry

Register only bounded semantic tasks:

| Task | Input | Output | Allowed truth |
|---|---|---|---|
| Requirement normalization | requirement + evidence excerpts | `RequirementSpecProposal` | proposal/review |
| Candidate semantic mapping | canonical candidates + excerpts | ranked link proposals | inferred only |
| Automation repair/generation | exact source regions + IR/profile/obligations | isolated patch proposal | untrusted until validation |
| RCA synthesis | execution facts + graph/evidence/history | ranked hypotheses | hypothesis only |
| Explanation | approved deterministic facts/evidence | claim ledger + prose | cannot add new facts |

Any prompt calculating risk, mandatory tests or release code is a design defect.

## 3. Prompt-as-code structure

```text
prompts/runtime/
├── requirement_normalization/
│   ├── prompt.py
│   ├── schema.py
│   ├── examples/
│   └── CHANGELOG.md
├── semantic_mapping/
├── automation_patch/
├── rca_synthesis/
└── explanation/
```

Prompt builders accept typed values and produce separately structured instructions, task input and untrusted context. Do not concatenate arbitrary strings into a monolithic prompt.

## 4. Prompt manifest

```yaml
prompt_name: rca_synthesis
prompt_version: 1.2.0
content_hash: ""
owner: ""
input_schema: RCAEvidencePack@1
output_schema: RCAProposal@1
allowed_evidence_types: []
allowed_tools: []
external_knowledge: false
abstention_codes: []
token_budget: 8000
time_budget_ms: 30000
attempt_budget: 1
tested_models: []
eval_dataset_versions: []
approved_environments: [local, integration]
```

## 5. Context compiler

The compiler, not the model, selects context according to task policy:

1. validate project/source authorization;
2. pin source/graph/policy/framework snapshots;
3. collect exact required facts and strongest paths;
4. add relevant semantic candidates/history within trust limits;
5. preserve contradictions, exclusions and missing context;
6. redact/classify;
7. apply deterministic token budget;
8. emit context pack ID/hash and item manifest.

Critical confirmed evidence is retained before narrative material. If it cannot fit safely, split the task or require review; never silently truncate the release blocker.

## 6. Model routing

Route by task quality, risk, latency, data policy and cost—not by a single default model.

| Task class | Default approach |
|---|---|
| Deterministic domain truth | No model |
| Simple extraction/classification | Small approved model + strict schema |
| Ambiguous mapping/RCA/code proposal | Stronger approved model within evidence/tool bounds |
| Explanation | Cost-efficient approved model after facts validated |
| High-risk insufficient evidence | Abstain/review rather than escalate endlessly |

Every route has an approved fallback. Fallback cannot broaden tools, data access, autonomy or certainty.

## 7. Model configuration record

```text
provider and endpoint
requested model and resolved model snapshot/version
temperature/top-p/reasoning/maximum output
strict/structured output settings
tool definitions and versions
timeout/retry/fallback policy
data residency/retention mode
cost/token budget
configuration hash
```

Record both requested and resolved model because provider aliases may change.

## 8. Change and promotion process

```text
change proposal + hypothesis
→ prompt/config diff and ADR if boundary changes
→ targeted unit/schema/security/evals
→ compare quality, latency, cost and trajectory to baseline
→ integration shadow
→ canary by project/traffic/config flag
→ promote or rollback
```

Breaking schema, evidence policy, tools or deterministic/semantic ownership requires contract/architecture review, not only a prompt version bump.

## 9. Version semantics

- Patch: wording/example correction with same behavior/contract; still evaluate.
- Minor: intended behavior expansion within same schemas/tool/action class.
- Major: schema, tool, evidence, safety, task or ownership change.
- Model/config change is separately versioned even when prompt text is unchanged.
- Context compiler/retrieval policy changes trigger RAG and downstream evaluations.

## 10. Retry and fallback

- Retry transient transport/rate-limit failures using adapter policy.
- One constrained repair attempt may fix malformed output without adding new evidence.
- Do not repeat a reasoning failure with identical prompt/context and call it resilience.
- After budget exhaustion, use deterministic/manual path and record termination reason.
- Circuit breakers prevent cascading model outages/cost.
- Never fall back from an approved enterprise endpoint to an unapproved public model.

## 11. Caching and cost

- Cache immutable prompt prefix/schema/tool definitions when supported.
- Cache semantic outputs only by full input/context/prompt/model/config hash and policy-approved TTL.
- Do not cache authorization, approval, freshness or live execution outcomes across state changes.
- Track cost/tokens/latency per task and accepted result.
- Context reduction is evidence-aware and measured against quality, not just token count.

## 12. Prompt injection and instruction hierarchy

- System/developer policy and task contract are separate from source/document/tool data.
- Untrusted content is labelled and structurally encoded, preferably as tool results/data objects.
- Retrieved instructions cannot alter tools, policy, evidence class or task goal.
- Prompt content is not a secret/security boundary; protect capabilities and data outside the prompt.
- External knowledge is off for evidence-restricted tasks.
- Model output never directly executes code/actions.

## 13. Required telemetry/evals

Capture task, prompt/model/context versions, hashes, evidence pack, tokens, latency, cost, schema validity, abstention, claims, tool trajectory and result. Compare:

- task accuracy/recall;
- unsupported claim/evidence entailment;
- injection and forbidden-tool behavior;
- abstention/calibration;
- trajectory/tool arguments;
- latency/cost and retry waste;
- human correction/acceptance.

## 14. Retrofit tasks

1. Inventory embedded/duplicated prompts and direct model calls.
2. Create task registry and prompt builders near owning feature.
3. Add prompt/model/context/config manifests and hashes.
4. Separate instructions, typed input and untrusted evidence.
5. Route all calls through `SemanticReasonerPort` and model adapter.
6. Implement schema/claim validation, budgets and safe fallback.
7. Capture current behavior as baseline before editing prompts.
8. Gate changes with task-specific evals and feature flags.

## 15. Tests

- Prompt builder snapshot/schema and deterministic hash.
- Malicious/ambiguous context cannot alter task/tools/policy.
- Model alias/config change is observable and evaluated.
- Malformed output, timeout, rate limit and endpoint outage.
- Fallback preserves schema, data policy, tools and certainty.
- Cache invalidates on source/prompt/model/policy/access change.
- Budget termination and deterministic/manual degradation.
- Canary/rollback returns prior approved version.

## 16. Definition of done

- No production semantic call is an unversioned inline prompt/SDK call.
- Every task has typed I/O, evidence/tool scope, abstention and budgets.
- Prompt/model/context/config versions reproduce the semantic input path.
- Changes are compared to an approved baseline on quality, safety, cost and latency.
- Failed/unavailable model cannot corrupt deterministic state or block the whole usable flow.
- Rollback can restore the prior approved prompt/model route without code surgery.

## 17. Primary references

- [OpenAI prompt engineering and prompt-as-code guidance](https://developers.openai.com/api/docs/guides/prompt-engineering)
- [OpenAI model optimization](https://developers.openai.com/api/docs/guides/model-optimization)
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
- [LangSmith prompt management/version comparison](https://docs.langchain.com/langsmith/manage-prompts)
