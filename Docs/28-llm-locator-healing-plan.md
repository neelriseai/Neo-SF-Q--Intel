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

## 7. Built state (as of K-chunk, commit fbdb551)

```
[DONE] NEO_HEAL_TIERS switch      default metadata · llm tier · abstention · resolvedByTier in projection
[DONE] LOCATOR_PROBE worker mode  staged BASELINE / STALE_AND_DISCOVER / RERUN against a live session
[DONE] live:healing CLI           headed + slow-mo, diagnosticOnly projection
[DONE] T-GRAPH                    graph_neighborhood(entity, hops, cap) -> edge list · MCP-exposed
[DONE] T-INTENT                   knowledge_index + knowledge_section(page|module|impact, section) · MCP-exposed
[DONE] knowledge repo             14 files · pages / modules / impact · operator-authored
[DONE] deterministic heal proven  live drift -> stale -> metadata rediscovery -> rerun -> exact restore
[TODO] T-SIG                      D1
[TODO] T-META                     D2
[TODO] T-DOM push                 D3
[TODO] prompt + provider + wiring D4
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
