# Page: Opportunity record page

## What this page is for

The Opportunity record page is the evidence page. It is where a tester confirms stored values, policy result, evaluation history, and native approval state after actions taken in the Workbench or standard Salesforce UI.

## Primary object

Opportunity.

## Embedded business components

| Component | What it tells the tester |
|---|---|
| Opportunity details | Stored input values such as Amount, Discount, Strategic Deal, Stage, Close Date, Account, and Regional VP Approver. |
| Strategic Deal Policy | Current policy status, reason, amount boundary, discount boundary, active/applied rule version, last evaluated timestamp, and evaluation history. |
| Regional VP Approval | Native approval request status, lock status, action buttons, and latest request history. |

## What success looks like

After a valid save from the Workbench:

- The record exists as a synthetic Opportunity.
- The policy card has a status, reason, rule version, and latest evaluation time.
- If the scenario needs approval, policy status is `Pending Regional VP`.
- If owner submits for approval, the native approval panel shows `Latest request: Pending` and the record is locked while pending.
- If the assigned VP approves, native request status and Opportunity approval status become approved.

## Policy status versus native approval status

These are intentionally separate.

| Status source | Example value | Meaning |
|---|---|---|
| Policy card / Opportunity policy fields | `Pending Regional VP` | The deal qualifies for approval. |
| Native approval panel | `Not submitted` | No approval request has been submitted yet. |
| Native approval panel | `Pending` | Owner submitted the request and VP decision is outstanding. |
| Native approval panel | `Approved`, `Rejected`, `Removed` | Native process reached a human decision or recall result. |

## Approval panel behavior by persona

| Persona | Expected buttons |
|---|---|
| Owner, eligible deal, no pending request | `Submit for Approval` |
| Owner, pending request | `Recall Approval`; not Approve/Reject |
| Assigned Regional VP, pending request | `Approve` and `Reject` |
| Same person as owner and approver | Should not be allowed as valid approval path |
| Unassigned user | No decision action; may see unavailable/error state |

## Journey: owner submits and VP approves

1. Owner opens a synthetic Opportunity whose policy card says `Pending Regional VP`.
2. Owner confirms Regional VP Approver is populated with an active user different from owner.
3. Owner clicks Submit for Approval.
4. Expected owner-visible result: latest request is Pending; owner can recall; owner cannot approve/reject through the app.
5. Regional VP opens the same record in a separate session.
6. VP sees Approve and Reject.
7. VP clicks Approve.
8. Owner refreshes record and sees approval status updated to Approved.
9. Evaluation history should not grow merely because a VP approved.

## Journey: owner recall

1. Owner submits an eligible deal.
2. Owner clicks Recall Approval while request is pending.
3. Expected result: native status becomes Removed/recalled, policy eligibility remains Pending Regional VP, and no fake approval is recorded.

## Failure meanings

| Symptom | Meaning |
|---|---|
| Submit for Approval missing for owner | Deal may not be eligible, approver may be blank/same owner/inactive, name may not start `SYN-`, or status is not Pending Regional VP. |
| Approve/Reject missing for VP | Current user is not the assigned work-item actor, request is not pending, or session is not actually VP. |
| Approval action unavailable message | Governance or current-state refusal; do not retry blindly. Verify role, assigned approver, current policy, and approval state. |
| Record locked while approval is pending | Expected native approval behavior. |
| Policy card stale after metadata change | Expected until relevant policy input changes; Save and Evaluate is not a bulk re-evaluation command. |

## Dependencies

- Created/updated by Deal Workbench.
- Reads strategic policy results from Opportunity and evaluation history.
- Native approval process writes final approval/rejection/recall status.
- Cross-module scenarios may open Opportunity from Account related lists or Opportunities Home.

