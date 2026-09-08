# Governance and evaluation

## Initial metrics

| Metric | Demonstration gate |
|---|---:|
| Material claims with valid evidence | 100% |
| Unsupported release-blocking claims | 0 |
| Critical impacted nodes recalled | 100% of golden obligations |
| Mandatory tests selected | 100% |
| Retrieval Recall@5 | at least 80% with numerator/denominator shown |
| Unauthorized tool calls executed | 0 |
| Tool calls with complete audit event | 100% |
| Ambiguous locator cases abstained | 100% |
| False locator heals | 0 in the approved corpus |
| Deterministic decision reproducibility | 100% |
| Persisted credential/session URL findings | 0 |

Small-corpus results always show raw counts. These are demonstration gates, not universal accuracy claims.

## Required evaluation sets

- Business-rule change impact.
- Permission/security change impact.
- LWC presentation change and locator healing.
- Stale graph/contract rejection.
- Missing live Salesforce degradation.
- Model outage deterministic fallback.
- Prompt injection inside documents and tool outputs.
- Unauthorized write/approval attempts.
- Unsupported evidence ID and fabricated entity rejection.

## Release policy

An LLM may explain but cannot choose the code. The deterministic engine returns `INCOMPLETE` for stale evidence, missing mandatory results or unresolved critical claims; `NO_GO` for failed blocking controls; `CONDITIONAL_GO` for explicit review items; and `GO` only when every mandatory gate passes.
