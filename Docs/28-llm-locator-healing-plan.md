# LLM-first locator healing — plan (D-series, 2.5h)

Priority: ahead of the assurance-report agent (`Docs/27`). Rationale: higher capability gain and
smaller surface. Healing today works only because the AUT is instrumented with `data-field-api`
hooks; most customer orgs are not. This slice is what makes healing survive an org we did not build.

Reuses the shipped `NEO_HEAL_TIERS` switch: default `metadata`, `llm` fires only when the
deterministic tier abstains.

## 1. Tool surface (4 pull + 1 push)

```
[T-DOM]    push  worker -> {failedLocator, structureSkeleton, candidates:[{tag,role,name,attrNames[],attrHashes{}}]}
[T-GRAPH]  pull  graph_lookup(entityId)            -> edge list  node -> rel -> node
[T-INTENT] pull  intent_lookup(obligationId)       -> {gherkin, page, rule, expectedState}
[T-META]   pull  metadata_lookup(object, field)    -> {type, label, required, picklist?}
[T-SIG]    pull  signature_lookup(pageKey, elKey)  -> {structure, nearby[], attrsPresent[], attrHashes{}}
```

`[ARCH]`: DOM is **pushed, never pulled**. The authenticated page lives in the worker's ephemeral
session; a pull tool would need a live session broker, which breaks the timebox and `SF-L06`
session containment.

## 2. Signature store — values stripped

```
element_signature(pageKey, elementKey, snapshotRoot)
  attrsPresent : ["data-field-api","title","role"]              // NAMES only
  attrHashes   : {"data-field-api":"<sha8>"}                    // values hashed, never raw
  structure    : "lightning-input-field>input[role=combobox]"
  nearby       : ["label","lightning-icon","button"]             // tags only, no text
  capturedOn   : first SUCCESSFUL interaction
```

`[WHY_STRIP]`: `data-value` is a record id and `title` carries person names. Structure plus
attribute names is sufficient for similarity and keeps org data out of Neo's store.
`[PROMPT_RULE]`: similarity over structure and attribute presence; never exact value match.

## 3. Flow

```
State_Fail: locator(data-testid=deal-baseline-Amount) -> NOT_FOUND
 -> Pull: T-GRAPH + T-INTENT + T-META + T-SIG
 -> Reason: rank T-DOM candidates by structural + attr-name similarity, scoped by metadata identity
 -> Emit: {candidateRef, confidence, rationale, citedRefs[]}
State_Verify: unique + visible + enabled + metadata-scoped + full assertion set
 -> PASS: heal, resolvedByTier=llm    FAIL: abstain, record rejected proposal
```

## 4. Chunks (150m, revised after K-chunk delivery)

```
D1 000-045  T-SIG store + capture-on-success, tests first          opus     [BLOCKED on §8]
D2 045-060  T-META lookup only (T-GRAPH + T-INTENT already built)  opus
D3 060-090  T-DOM push payload from worker at failure              opus
D4 090-115  prompt + provider call + proposal -> verifier wiring   opus
D5 115-135  live run on the real drift scenario, HEAL_TIERS=llm    me + opus
D6 135-145  triage all issues -> severity -> priority -> fix       me/opus5
D7 145-150  docs, knowledge, compliance log, commit                sonnet
```

`[SAVED]` ~40m against the original estimate: the K-chunk delivered T-GRAPH and T-INTENT early.
`[KNOWLEDGE]` operator-supplied (14 files, 38KB) — the +45m authoring risk is retired.

## 5. Pollution guards

```
[G1] tier-gated   default metadata; llm only on deterministic abstention
[G2] verifier     unchanged; model proposes, deterministic checks decide, wrong guess abstains
[G3] storage      new table only; receipts, campaigns and graph untouched
```

## 6. Evaluated: third-party Playwright healer MCP

`[VERDICT]`: not in the product path; useful at development time only.

```
[REJECT_PRODUCT]
 session    server owns its own browser; conflicts with one-shot in-memory frontdoor handoff (SF-L06)
 authority  exposes click/type to the model; inverts "model proposes, deterministic code acts"
 evidence   output not bound to campaign/org/source roots; yields no ProviderInvocationReceipt
 sanitize   returns page content incl. record data into the prompt; Neo requires digest-only
[ACCEPT_DEVTIME]
 inspection faster DOM/accessibility inspection during development (would have saved the
            five blind iterations on the lookup selector)
 shape      its accessibility-snapshot representation (role + name + ref) is a good model for
            T-DOM's candidate list; adopt the shape, not the server
```

## 7. Built state (updated 2026-09-12, LLM bridge slice)

```
[DONE] NEO_HEAL_TIERS switch      default metadata · llm tier · abstention · resolvedByTier in projection
[DONE] LOCATOR_PROBE worker mode  staged BASELINE / STALE_AND_DISCOVER / RERUN against a live session
[DONE] live:healing CLI           headed + slow-mo, diagnosticOnly projection
[DONE] T-GRAPH                    graph_neighborhood(entity, hops, cap) -> edge list · MCP-exposed
[DONE] T-INTENT                   knowledge_index + knowledge_section(page|module|impact, section) · MCP-exposed
[DONE] bounded intent resolver    locator-healing CLI accepts caller-bounded intentSection or
          intentLookup(page|module|impact, section); missing sections add no context instead of
          inventing one, and malformed/ambiguous selectors fail before the LLM call
[DONE] MCP-style context planner  before final ranking, the LLM may emit one strict JSON
          knowledge_section tool plan over the compact knowledge index; Neo executes the bounded
          section fetch locally, ignores broad whole-document requests, and falls back to base
          metadata+DOM ranking when planning fails
[DONE] incremental context loop   the planner starts with one named field/page heading; if the
          final ranking is accepted but weak or does not cite the fetched intent, Neo passes a
          compact prior-context summary plus previous ranking back to the planner and permits one
          additional named-section fetch. Repeated sections, whole documents and malformed plans
          are ignored; the model still chooses only an existing candidate ordinal.
[DONE] intent-fit gate            final ranking output must include contextAssessment:
          intentFit, missingContext, and reason. Neo does not stop after the first context fetch
          unless the model explicitly says the fetched intent is SUFFICIENT, cites intent, and
          meets the confidence floor; PARTIAL/INSUFFICIENT/NOT_PROVIDED forces the second bounded
          fetch when available.
[DONE] knowledge repo             14 files · pages / modules / impact · operator-authored
[DONE] deterministic heal proven  live drift -> stale -> metadata rediscovery -> rerun -> exact restore
[DONE] T-SIG                      element signature store + tests
[DONE] T-META                     field metadata lookup + tests; live CLI enriches from the
          configured source root and degrades without blocking when source metadata is unavailable
[DONE] T-DOM push                 digest-only candidate payload on deterministic abstention
[DONE] prompt + provider + wiring provider schema hook + locator-healing Python bridge + browser projection
[DONE] live provider acceptance   real OpenAI locator-proposal smoke accepted candidateOrdinal=0
          with confidenceMilli=986 after fixing sealed-envelope rendering, explicit allowedRefs
          and non-fatal T-META enrichment
[DONE] evidence isolation         projection refuses to attach modelProposal or advertise
          modelDiscoveryAvailable for deterministic metadata PASS observations
[DONE] context comparison tests   base vs enriched prompts keep identical candidate payloads while
          adding only citable intent/edge refs, so context can improve ranking without widening the
          model's candidate choice surface
[DONE] real context comparison    base metadata+DOM ranking and planned MCP-style ranking both
          selected the correct lookup candidate; the first planner draft chose noisy persona
          context, so instructions and execution guard were tightened to require a named
          page/impact field-behavior section before intent context is admitted
[BLOCKED] incremental provider smoke  attempted on 2026-09-13, but provider dispatch stopped
          before a model call with PROVIDER_CREDENTIAL_SOURCE_CONFLICT. Unit and browser-path
          contract tests cover the loop; this is not claimed as new live LLM evidence until
          credential source policy is clean.
[DONE] repo-backed functional test  uses real knowledge-repo section fetches from the Strategic
          Deal Workbench page: Field behavior first, then Main happy path when the first ranking
          reports PARTIAL/PAGE_FLOW. This proves the incremental design improves grounding without
          widening selector authority or stuffing a whole document.
```

`[MEAS]` graph full 228,591 ch vs 1-hop 946 ch (99.6% cut) · knowledge heading block 517 ch vs whole page 5,824 ch (91% cut).

## 8. Open decisions blocking D1

```
[D-KEY]  element key must survive a locator rename, so it cannot be the locator.
         proposed: semantic identity (object + field API name + page key), obligationId as secondary index
[D-TIER] storage ladder. repo convention degrades postgres -> sqlite -> json -> cache.
         proposed for MVP: postgres only, explicit unavailable, no silent degradation
```

## 9. Agent evidence (carried from discussion, scheduled D4)

```
reuse ProviderInvocationReceipt (digest-only: model, prompt/response sha, tokens, timing)
add   observationDigest · proposedCandidateDigest · verifierVerdict · resolvedByTier
RULE  rejected proposals are recorded too — the verifier catching a wrong guess is the
      strongest evidence the architecture produces, and today it is invisible
```

## 10. Deferred, explicitly not in this slice

```
screenshots / SikuliX / OpenCV   attribute envelope was the decisive signal, not vision
dashboard "healed by model" panel ~2h: artifact ingest -> campaign status -> panel
assurance report agent (Docs/27)  parked behind this slice
```

## 11. Development-time tooling

```
Playwright MCP: dev-time inspector only, never in the product path (§6).
Value: would have replaced five blind iterations on the lookup selector.
Install as a local dev dependency; no product code may import or depend on it.
```

## 12. Rev 3 — evidence graph corrects the healing input model (R0 spike, 2026-09-12)

Rev 2 (§§1-11 above) is not deleted. Where Rev 3 conflicts with it, Rev 2 is superseded and the
conflict is called out explicitly below; everything else in §§1-11 stands.

```
[INPUT_MODEL] corrected. The healing agent receives:
  · knowledge repo (intent)                        — unchanged from §7 T-INTENT
  · failed-step log (step + element + action)       — unchanged
  · change delta from the EVIDENCE graph (git-diff derived) — NEW, replaces §1 T-GRAPH's source
  · app metadata                                    — unchanged from §2/§7 T-META
  · signature store (if history exists)             — unchanged from §7 T-SIG
  · live DOM candidates fetched on the fly          — unchanged from §1/§3 T-DOM
  · sf CLI/API as optional escalation                — NEW, not in Rev 2
```

```
[RETIRED] the Salesforce app knowledge graph (knowledge/application-graph.json) as a healing
input. SUPERSEDES §7's `[DONE] T-GRAPH  graph_neighborhood(entity, hops, cap) -> edge list` line:
that tool's rendering mechanism (bounded edge-list neighborhood, sorted, deduplicated,
truncation-aware — src/neo_sf_q_intel/context_feeds.py::graph_neighborhood) is reusable, but the
graph it was pointed at (knowledge/application-graph.json) is not the healing-time source going
forward. The evidence graph supersedes it: the evidence graph is already derived from git diff
over the app, so it does not need a second, separately-maintained knowledge graph to tell healing
what changed.
```

```
[TRUTH_GATE] change_delta(entity) DELETE -> DO NOT HEAL, the failure is correct — the element is
             supposed to be gone; healing over that would hide a real regression.
             MODIFY     -> heal, but declare the result as an intended-change heal, not a drift heal.
             none       -> no delta for this entity -> this is drift -> heal as today.
```

```
[R0_MEASURED] (source: R0 spike, quality/reviews/agent-exchanges/R0-spike-return.md)
  Persistence today: the evidence graph (GraphProductionArtifact) is persisted ONLY nested inside
    candidate_assurance_bundles.bundle_document, keyed by bundle_sha256 (a digest of the whole
    bundle) — there is no per-entity index. The live host SQLite store is missing the
    candidate_assurance_bundles table entirely, so zero persisted copies exist on this host today.
  Cost:      capture (cold) 18,535 ms · verify_current is a FULL re-capture, so capture+verify is
             ~38 s total. Two warm in-process repeats: 18,427 ms and 19,139 ms — cold vs warm is
             not the lever here, tree size is.
  Shape:     388 BASE nodes · 402 CANDIDATE nodes · 17 delta entries.
  Node id grammar CONFIRMED: field:{ObjectApiName}.{FieldApiName} (e.g.
             field:Opportunity.Regional_VP_Approver__c), object:{Name}, apex:{ClassName}, and
             other kind:{identity} forms. Edge ids are edge:{32 hex}, NOT human-readable — an
             edge cannot be recognised or diffed by eye, only by id.
  Per-entity lookup impossible without a full tree walk: a node's owner paths and digests are
             only well-defined after merging across every file that owns it (attributes and
             owner_paths accumulate, evidence_state can be promoted INFERRED -> CONFIRMED across
             that merge), so there is no cheap way to ask about one entity in isolation without
             having already produced the whole artifact.
```

```
[DECISION] Access path = option B: produce the evidence-graph artifact once per healing run,
  cache it in-process keyed on (repository_identity_sha256, verified_change_manifest_sha256),
  hard-expire at the artifact's valid_until, and fail closed with an explicit reason code on
  expiry or a key mismatch — never serve a stale answer silently.
  Options C (persist to candidate_assurance_bundles and read that) and D (a new indexed table)
  were REJECTED: the live SQLite store lacks candidate_assurance_bundles outright (would need a
  migration before working at all), and both add a second authority for derived evidence that can
  drift from delta_sha256/receipt_sha256 with no re-validation on read.
  REFINEMENT: produce the graph BEFORE the browser session is acquired, never lazily inside it. A
  ~19 s stall inside a one-shot ephemeral frontdoor session (SF-L06 session containment, §1 ARCH)
  eats directly into that session's window — the production cost must be paid outside the clock
  that the browser session is running against.
```

```
[DECISION] change_neighborhood defaults to side=CANDIDATE (the live org reflects the candidate
  tree, i.e. what the current source project actually declares now); BASE is selectable
  explicitly for callers that need the pre-change side.
```

```
[IMPLEMENTED_GUARD] The locator-healing CLI now has an evidenceGraphLookup hook but intentionally
  does not call the retired Salesforce app knowledge/application-graph.json. Until R1 implements
  the evidence/change-delta accessor, graph context must be supplied as already-bounded graphEdges
  or by an injected evidence lookup in tests/host orchestration. This prevents a stale static graph
  from creating false confidence or candidate-ranking ambiguity.
```

```
[OPEN] Does the healing accessor need the full verify_current path (~38 s, capture + re-capture
  verification) or may it serve off a capture whose digests self-validate on construction alone
  (~19 s, capture only)? UNRESOLVED. The existing convention on the analyze path
  (service.analyze_current_candidate) is capture+verify; choosing capture-only for the healing
  accessor would be a deliberate, documented relaxation of that convention, not a silent one.
  Must be decided before R1 writes the accessor, since it changes both the cache key contract and
  the per-run cost the [DECISION] above is paying for.
```

```
[RESLOT] R1 (the change_delta / change_neighborhood accessor and its two MCP tools) was
  estimated 30m by opus-5, before the access path was known. R0 measured the real access path at
  90m (range 75-110m): a synthetic fixture git repo for functional tests (20-30m of that), bounded
  hop traversal with deterministic ordering and a new response model, ~10 test cases across two
  tools to satisfy AGENTS.md's five verification-case classes, and a fail-closed test per cache
  invalidation path (expiry, manifest-digest change, pipeline unavailable). Recorded explicitly as
  an estimation lesson: a chunk estimated without knowing its access path can miss by 3x, and the
  fix is not "pad the estimate" but "spike the access path before sizing the chunk" — which is
  what R0 was for.
```
