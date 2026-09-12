# Assurance report agent — frozen contracts (C0)

Scope: runtime agent-produced assurance report. Tools compute; the agent reasons; a
deterministic renderer emits markup. Frozen at C0; C1-C4 implement against this file only.

`[DSL]` handles used below: `AR` = AssuranceReport · `ER` = evidenceRef · `GATE` = required live gate ·
`DIAG` = diagnostic, no acceptance credit.

## 1. Tool surface (7) — MCP, bounded, <2KB each

```
[T1] assurance.gate_status()        -> {accepted:int, denominator:int, perGate:[{gateId,receipts:int,state}], gapCodes:[str]}
[T2] assurance.coverage_apex()      -> {orgWide:int, testRun:int, tests:{ran,passing,failing}, classes:[{name,pct,covered,total}], diagnosticOnly:true}
[T3] assurance.impact_trace()       -> {changedFiles:[{locator,disposition,category}], impactedEntities:[str], derivations:[{partition,state,reason}], localValidations:int}
[T4] assurance.evidence_counts()    -> {receipts,artifacts,campaigns,runs,bundles}
[T5] assurance.capability_status()  -> {implemented,foundation,next, byId:[{id,status}]}
[T6] assurance.defect_ledger()      -> {open,fixed, bySeverity:{}, items:[{id,status,severity,capabilityIds}]}
[T7] assurance.regression_status()  -> {python:{passed,failed,skipped}, browser:{passed,failed}, knowledgeCurrent:bool}
```

`[SIDE_EFFECT]`: T2 only. Operator decision: T2 executes live Apex on EVERY report run (no cached mode); budget ~90s per run. All others read-only. T2 emits `diagnosticOnly:true` and
may never be cited as `SF-L08` evidence.
`[CALLER_SCOPE]`: none. No tool accepts an alias, path, query, campaign or record id except
T1's optional `campaignId`, which is validated against the ledger.

## 2. Report schema (AR)

```
AR {
  executiveRead: str(<=600)
  sections: [{title, narrative, metrics:[{label, value, evidenceRefs:[ER]}]}]
  findings: [{severity:P1|P2|P3, statement, evidenceRefs:[ER], recommendation}]
  notClaimed: [{claim, reason, missingEvidence:[str]}]
  abstentions: [{question, reason}]
}
ER = "tool:<T1..T7>#<jsonpath>" | "receipt:<receiptId>" | "digest:<sha256>"
```

## 3. Verifier rules (hard gate between reason and render)

```
[V1] metric.value MUST equal the value at ER jsonpath in the captured tool payload      -> else REJECT_METRIC
[V2] every findings[].statement MUST carry >=1 resolvable ER                            -> else REJECT_FINDING
[V3] no percentage without a declared population                                        -> else REJECT_METRIC
[V4] locally-valid gates MUST NOT be reported as accepted                               -> else REJECT_REPORT
[V5] T2 figures MUST NOT appear under any SF-L08 claim                                  -> else REJECT_METRIC
[V6] agent-authored markup                                                              -> REJECT_REPORT
[V7] tool payloads are captured once and frozen before reasoning; replay compares        -> else REJECT_REPORT
```

Rejected items are recorded, not silently dropped. A rejected report yields the gathered
facts plus the rejection list, never a fabricated narrative.

## 4. Agent prompt contract

```
[ROLE]: assurance reporter
[IN]: frozen tool payloads (T1..T7) + graph edge list
[BAN]: compute/estimate/invent numbers · author markup · resolve contradictions · claim acceptance from local validity
[DO]: interpret pattern -> findings ranked by consequence -> derive notClaimed by diffing asserted capability vs existing receipts
[ABSTAIN]: missing population, contradictory payloads, absent evidence
[OUT]: AR json only
```

## 5. Orchestrator placement

```
gather(T1..T7) -> freeze payloads -> compile GraphContextPack(edge-list) -> reason(specialist+ProviderInvocationReceipt)
  -> verify(V1..V7) -> render(AR -> html) -> persist(artifact + receipt)
```

Checkpointed LangGraph subgraph beside the four existing stages. The model call is itself
evidenced through the existing `ProviderInvocationReceipt`; failure isolates to the reason node
and degrades to facts-only output.

## 6. Agent value at the evidence-graph stage

The graph stays the authority plane. Permitted agent contribution: propose `INFERRED` edges for
separate promotion, narrate why a traced path matters in business terms, and flag suspicious
absences (an Apex class with no derived test obligation). Forbidden: creating confirmed edges,
granting permissions, or satisfying gates.
