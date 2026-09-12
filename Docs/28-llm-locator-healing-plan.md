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

## 4. Chunks (150m)

```
D0 000-020  contracts: 4 tool sigs + signature schema + prompt          me/opus5
D1 020-070  T-SIG store + capture-on-success, tests first               opus
D2 070-110  T-GRAPH / T-INTENT / T-META lookups (data already exists)   opus
D3 110-140  T-DOM push payload from worker at failure                   opus
D4 140-170  prompt + provider call + proposal -> verifier wiring        opus
D5 170-200  live run on the real drift scenario, NEO_HEAL_TIERS=llm     me + opus
D6 200-230  triage all issues -> severity -> priority -> fix            me/opus5
D7 230-250  docs, knowledge, compliance log, commit                     sonnet
```

`[RISK]`: the static knowledge repo behind `T-INTENT` (pages, rules, expected state) is content,
not code. Operator-supplied keeps the window; authored here adds ~45m.

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
