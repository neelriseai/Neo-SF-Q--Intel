# Initial genericity audit

- Review target: commit `2666f5d`
- Reviewer: independent read-only development sub-agent
- Policy: `config/quality-policy.json` version 1.0.0

## P0/P1 findings and disposition

| Finding | Disposition |
|---|---|
| Vague common-token inputs returned broad confident impact | Resolved in this slice with low-information filtering and metamorphic abstention tests |
| Test specifications were emitted as impacted business entities | Resolved in this slice; tests remain separate selections/evidence |
| Governance assigned `supported=true` while constructing claims | Resolved in this slice through independent evidence/entity/state validation |
| Scenario-specific project, aliases, required capability and REST route leaked into core | Resolved in this slice; moved to profile configuration or generic route validation |
| Repository did not maintain its own project index and graph | Resolved in this slice with deterministic generator and freshness gate |
| Specialist agents are still thinner than the target reasoning architecture | Open; capability remains bounded deterministic foundation until typed specialist ports and model contracts land |
| PostgreSQL graph/chunk/audit persistence is schema-only | Open; `memory.postgresql` remains `FOUNDATION` |
| Browser worker/session handoff is not integrated | Open; `automation.browser-worker` remains `NEXT` |
| MCP authorization and audit policy is incomplete | Open; tool set remains read-only and must not expand before ToolRegistry/policy receipts |

Open items are deliberately visible in `config/capability-scope.json`; they are not represented
as completed production capability.

## Re-review hardening

The same reviewer audited the first remediation. The follow-up findings were handled as follows:

| Finding | Disposition |
|---|---|
| Native commands could fail without stopping `check.ps1` | Resolved with explicit exit-code checks after every native command |
| Repository hook existed but was not active | Resolved locally with `core.hooksPath=.githooks`; preflight verifies it |
| High-overlap generic phrases could still select many nodes | Resolved with ambiguity abstention and an adversarial many-match test |
| Claim grounding did not prove the asserted graph relationship | Resolved with direction, relation and endpoint receipts plus a tampering test |
| Capability inventory could be weakened by deletion or optimistic status | Resolved with required IDs and conservative `IMPLEMENTED`/`FOUNDATION`/`NEXT` states |
| Generated graph lacked authority and capability-to-code/test relationships | Resolved with authority classes and ownership, implementation and verification edges |
| A matching declared snapshot could hide changed source files | Resolved by verifying normalized content hashes and recomputing the inventory snapshot before evidence is admitted |
| A hidden default project ID remained | Resolved by deriving identity from the validated source contract unless the caller supplies one |
| Graph content was not cryptographically bound to the verified inventory | Resolved with a separately configured trusted graph digest and valid-shape tamper test |
| Caller project identity could disagree with the loaded evidence source | Resolved by rejecting cross-project requests in a single-source service |
| Node roles, risk and test kinds remained in runtime code and omitted valid source kinds | Resolved with a versioned ontology policy; unknown kinds abstain, and graph impact remains `FOUNDATION` |
| Capability verification and coordinated policy edits were weakly guarded | Resolved with positive/negative/failure case requirements, HEAD regression checks and mandatory review-ledger changes |
| Disallowed relationships could still bridge to a second-hop allowed edge | Resolved by enforcing relationship policy before a neighbor is admitted or queued, with a two-hop bypass test |
| Unknown kinds and relationships were silently omitted | Resolved with typed analysis gaps that fail governance and force an `INCOMPLETE` decision |
| Result and traversal caps could silently omit mandatory validations or high-risk impacts | Resolved by risk/obligation ranking, unconditional mandatory-test preservation and explicit truncation/capacity gaps |
| Exact-cap linear traversal could exit before discovering a queued omitted hop | Resolved by inspecting queued in-depth nodes for the next eligible neighbor and a linear-chain boundary regression test |
