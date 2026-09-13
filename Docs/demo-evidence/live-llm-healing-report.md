# Live LLM locator-healing showcase report

Published UTC: `2026-09-13T05:50:37+00:00`

## Suite result

| Metric | Value |
|---|---|
| Suite | `live-llm-business-healing` |
| Suite status | `PASSED` |
| Tests | `1/1 passed` |
| Diagnostic only | `True` |
| Release eligible | `False` |

## Executive result

| Metric | Value |
|---|---|
| Status | `PASSED` |
| Browser status | `PASSED` |
| Forced LLM fields | `3` |
| Model proposals | `3` |
| Model-applied fields | `3` |
| Healed fields | `3` |
| Abstained fields | `0` |
| Submitted | `True` |
| Success text matched | `True` |
| Salesforce persistence matched | `True` |
| Error code | `None` |
| Model rejection codes | `none` |

## What this proves

Live Salesforce browser/provider proof that forced stale locators for the Strategic Deal Workbench fields were healed by the LLM ordinal-ranking path and then verified by deterministic browser action and Salesforce persistence readback.

## What this does not claim

- full live campaign acceptance
- release eligibility
- proof that a deployed metadata/UI mutation caused the drift
- raw DOM or raw Salesforce record values

## Step log

| Step | Status | Detail |
|---|---|---|
| profile-and-session | PASSED | Trusted profile and one-shot session handoff completed before action. |
| heal-Name | PASSED | LLM_ORDINAL selected ordinal 0 from 1 candidate(s). |
| heal-Strategic_Deal__c | PASSED | LLM_ORDINAL selected ordinal 1 from 2 candidate(s). |
| heal-StageName | PASSED | LLM_ORDINAL selected ordinal 1 from 2 candidate(s). |
| submit-and-persist | PASSED | Configured success text and independent Salesforce persistence matched. |

## Field-level healing evidence

| Field | Tier | Ordinal | DOM candidates | Context plans | Intent cited | Intent fit | Missing context | Confidence | Signature found | Signature saved |
|---|---|---:|---:|---:|---|---|---|---:|---|---|
| Name | LLM_ORDINAL | 0 | 1 | 2 | True | SUFFICIENT | NONE | 865 | True | True |
| Strategic_Deal__c | LLM_ORDINAL | 1 | 2 | 2 | True | SUFFICIENT | NONE | 996 | True | True |
| StageName | LLM_ORDINAL | 1 | 2 | 2 | True | SUFFICIENT | NONE | 957 | True | True |

## Signature memory

Refreshed stripped signatures: `AccountId, Amount, CloseDate, Discount__c, Name, Regional_VP_Approver__c, StageName, Strategic_Deal__c`

## Sanitized evidence pointers

| Evidence | Value |
|---|---|
| Evidence phase | `LIVE_BUSINESS_ACTION_BROWSER_ACCEPTANCE` |
| Capability | `automation.browser-worker` |
| Input digest | `2380dc183db99a2eb06fb897234df705784244b88e81c76019858428005eaa25` |
| Execution digest | `d08840e01b510128163b4a86805fab6e970b5d22b79de46fbdb5ac2afaacf9cd` |
| Runtime receipt | `.runtime/live-llm-business-healing/live-business-action-llm.json` |
| Structured log | `Docs/demo-evidence/live-llm-healing-log.jsonl` |
| HTML report | `Docs/demo-evidence/live-llm-healing-report.html` |

## Sanitization boundary

Report includes only field API names, counts, booleans, statuses, confidence numbers and one-way digests from the sanitized receipt.
