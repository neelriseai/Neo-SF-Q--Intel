# R0 Spike Brief

```
[REPO] C:\Users\neela\Documents\ChatGPT\Neo SF Q- Intel  [CHUNK] R0  [TYPE] SPIKE  [TIMEBOX] 20m HARD STOP
[ROLE] investigate + recommend. PRODUCE NO FEATURE CODE. No edits to src/, tests/, packages/.
[RULES] CRLF forbidden on any write. No git add/commit/push.

[GOAL] Determine the cheapest CORRECT access path for a healing-time MCP tool to answer:
  change_delta(entity_id) -> {operation: ADD|MODIFY|DELETE|none, entityType, baseOwnerPaths[], candidateOwnerPaths[]}
  over the platform's OWN evidence graph (git-diff derived, BASE vs CANDIDATE sides).
  NOT the Salesforce app knowledge graph (knowledge/application-graph.json). That source is being retired
  for this purpose; the evidence graph supersedes it.

[KNOWN_SIGNATURES] src/neo_sf_q_intel/graph_production.py
  class TreeSide(StrEnum): BASE | CANDIDATE
  class GraphDeltaOperation(StrEnum): ADD | MODIFY | DELETE
  class GraphDeltaEntry: operation · entity_type("NODE"|"EDGE") · entity_id · base_entity_sha256? ·
       candidate_entity_sha256? · base_owner_paths[] · candidate_owner_paths[] · delta_sha256
  class ProducedNode: node_id · raw_kind · label · evidence_state · owners[] · attributes · semantic_sha256 · element_sha256
  class ProducedEdge: edge_id - source_id - raw_relation - target_id - ...
  class ProducedGraphSide · class GraphProductionArtifact · class GraphTombstone
  Consumers found: candidate_assurance.py:219,431 · foundation_pipeline.py:370,661,682,781

[OBSERVED] no repository accessor and no persisted standalone artifact were found by a grep of
  repository.py / service.py. The artifact appears to be produced in-memory per run. VERIFY OR REFUTE.

[ANSWER_THESE] exactly, with file:line evidence for each:
 Q1 Is GraphProductionArtifact persisted anywhere today (disk path, DB table, evidence ledger,
    .runtime/**, receipts)? If yes: exact location + how it is keyed + whether it is current.
 Q2 What inputs does producing it require (git refs, source roots, policies, digests)? Is producing
    it side-effect free and read-only w.r.t. the org and the repo?
 Q3 Wall-clock cost to produce it once. MEASURE IT if a runnable entry point exists; if you cannot
    run it, say NOT MEASURED and give the reason. Do not estimate a number and present it as measured.
 Q4 Can a single entity_id delta be answered WITHOUT producing the whole artifact?
 Q5 Node id grammar: is "field:Opportunity.Regional_VP_Approver__c" the actual node_id shape the
    Salesforce adapter emits? Quote the id-construction code. If the grammar differs, give the real one.
 Q6 Is there an existing digest/authority gate that a new read-only tool over this artifact must
    honour (ownership marker, contract pin, policy digest, capability scope)?

[OPTIONS] evaluate exactly these, with cost + risk each:
 A produce-on-demand inside the tool call
 B produce once per healing run, cache in-process, serve many entity lookups
 C persist the artifact during the existing pipeline run, tool reads the persisted copy
 D new repository table keyed by (project, run, entity_id)
[RECOMMEND] one option. Justify against: correctness, latency inside a browser healing window,
 blast radius on existing pipelines, and the repo's no-silent-degradation convention.

[R1_SIZING] given your recommendation, give a defensible minute estimate for implementing
 change_delta + change_neighborhood as MCP tools with unit AND functional tests. Flag anything that
 makes 30m unrealistic. An honest "R1 is larger than 30m" is a valid and useful answer.

[RETENTION] write two files, nothing else:
 quality/reviews/agent-exchanges/R0-spike-brief.md   = this brief verbatim in a fenced block
 quality/reviews/agent-exchanges/R0-spike-return.md  = your return JSON in a fenced block
 Create the directory if absent. LF only.

[RETURN] minified JSON only, no prose:
 {"q1":{"persisted":bool,"where":"","evidence":"file:line"},
  "q2":{"inputs":[],"readOnly":bool,"evidence":"file:line"},
  "q3":{"measured":bool,"ms":N|null,"why":""},
  "q4":{"singleEntityPossible":bool,"why":""},
  "q5":{"nodeIdGrammar":"","evidence":"file:line"},
  "q6":{"gates":[],"evidence":"file:line"},
  "options":[{"opt":"A|B|C|D","cost":"","risk":""}],
  "recommend":{"opt":"","why":""},
  "r1EstimateMinutes":N,"r1Risks":[],
  "blockers":[]}
```
