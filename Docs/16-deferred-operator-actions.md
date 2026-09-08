# Deferred operator actions

This is the non-secret queue for work that requires an authenticated session, environment-specific
credentials, an external service, or a deliberate human decision. A deferred action does not block
unrelated implementation. Runtime code must expose the affected capability as unavailable or
degraded until the recorded acceptance evidence exists.

Do not place credentials, tokens, frontdoor URLs, raw org exports, connection strings, machine
paths, record identifiers, or customer data in this file.

## Queue

| ID | Capability | Trigger | Operator action | Acceptance evidence | Current state |
|---|---|---|---|---|---|
| OA-001 | Provider-backed specialist agents | Before claiming an OpenAI or Azure OpenAI provider profile is verified | Run the provider preflight and bounded chat/embedding contract tests with the selected `.env` profile on the target machine | Redacted provider/profile identity, model/deployment identity, contract-test result and timestamped run artifact | Deferred; local development may use the configured OpenAI profile |
| OA-002 | PostgreSQL durable memory | Before claiming PostgreSQL is the active verified durability tier | Run schema bootstrap, save/resume and fallback-recovery tests against the configured target database | Redacted durability-mode event, migration identity and passing persistence receipts | Deferred; SQLite/JSON/cache degradation remains explicit |
| OA-003 | Salesforce live evidence | When a non-production alias is available for the live read-only integration slice, or when its authorization expires | Privately authenticate the configured alias on the target machine and run the bounded read-only smoke suite | Redacted alias fingerprint, org-production refusal result, CLI version and bounded smoke receipts; no auth material | Deferred until the live-evidence slice; an open browser session is not CLI authorization |
| OA-004 | Browser worker and locator healing | When the typed browser worker is ready for live validation | Use an already authenticated, explicitly selected non-production browser session to run the bounded capture/healing test | Redacted session-handoff receipt, page/snapshot identity, locator decision and failure-path result; no session URL | Deferred until the browser-worker slice |
| OA-005 | ChromaDB semantic adapter | Before promoting `retrieval.chromadb` beyond `NEXT` | Verify the Python 3.14-compatible Chroma installation on the target machine and run isolation, rebuild, corruption and adapter-parity tests | Package/runtime identity, project/snapshot isolation receipts, rebuild result and parity metrics | Deferred; deterministic exact/graph/lexical and in-process semantic paths continue |

## Operating rules

- Continue with the highest-priority independent work whenever an action is deferred.
- A queue item is completed only by its acceptance evidence, not by the presence of a key,
  connection string, browser tab or login claim.
- Environment failure must create a typed gap and preserve fail-closed governance behavior.
- Review and update this queue at each phase boundary; never convert it into a secret inventory.
