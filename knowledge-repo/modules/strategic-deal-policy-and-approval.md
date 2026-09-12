# Module: strategic deal policy and approval

## Rule in plain English

A synthetic strategic Opportunity needs Regional VP approval when all of the following are true:

1. The Opportunity name starts with `SYN-`.
2. Strategic Deal is checked.
3. Amount is greater than USD 50,000,000.
4. Discount meets the configured approval boundary.
5. An active Regional VP Approver is selected.
6. The approver is not the same person as the owner.

If approval is required but no valid approver is configured, the correct business result is Configuration Error, not automatic approval and not approval bypass.

## Boundaries

| Input | Boundary | Important edge |
|---|---|---|
| Amount | Strictly greater than USD 50,000,000 | Exactly 50,000,000 is Not Required. |
| Discount | Current baseline threshold is 15 | Exactly 15 is an important boundary case in the baseline examples; 15.01 is approval-eligible. |
| Strategic Deal | Must be checked | High amount/high discount without this checkbox is Not Required. |
| Regional VP Approver | Must be active and separate from owner | Missing, inactive, or owner-as-approver invalidates the route. |

## Status meanings

| Status | Meaning |
|---|---|
| Not Required | The Opportunity does not cross every required boundary. |
| Pending Regional VP | The policy says VP approval is required and route is configured. This is not yet a native request. |
| Configuration Error | Policy or route setup is missing/invalid. This is a blocking result, not an approval. |
| Approved | Native process final decision approved the request. |
| Rejected | Native process final decision rejected the request. |
| Removed/Recalled | Owner recalled a pending request. |

## Policy card facts a tester should verify

- Approval Status.
- Reason text.
- Amount boundary.
- Discount boundary.
- Active Rule Version.
- Applied Rule Version.
- Last evaluated timestamp.
- Evaluation History rows.

## Native approval facts a tester should verify

- Latest request status.
- Whether record is locked while pending.
- Which persona sees which actions.
- Latest Request History after submit/approve/reject/recall.

## What should not happen

- Admin clicking approve must not be counted as VP approval unless admin is the assigned VP work-item actor.
- Hand-editing Approval Status must not be counted as a native approval decision.
- A final VP decision must not silently create a new policy evaluation unless policy inputs changed.
- Save and Evaluate must not be used to hide stale rule-version evidence after a metadata change.

## Ambiguity rule

If policy and native approval appear inconsistent, do not guess. First answer these:

1. Has Save and Evaluate created a policy status?
2. Has Submit for Approval created a native request?
3. Is the current user the owner, assigned VP, or unrelated user?
4. Did any policy input change after the last evaluation?

