# E2E scenario matrix

## Scenario 1: create a strategic deal that needs approval

| Step | Persona | Action | Expected observable |
|---|---|---|---|
| 1 | Owner/admin | Open Deal Workbench. | Workbench shows deal input fields. |
| 2 | Owner/admin | Fill synthetic deal above boundaries with active separate VP. | Fields retain selected values. |
| 3 | Owner/admin | Save and Evaluate. | Success message and policy card says Pending Regional VP. |
| 4 | Owner/admin | Open/refresh record page. | Opportunity stores inputs and evaluation history. |
| 5 | Owner/admin | Submit for Approval. | Native status Pending; owner can Recall. |
| 6 | Regional VP | Open same Opportunity. | Approve/Reject visible. |
| 7 | Regional VP | Approve. | Native request approved; owner sees final approval after refresh. |

## Scenario 2: missing approver branch

| Step | Persona | Action | Expected observable |
|---|---|---|---|
| 1 | Owner/admin | Create strategic deal above boundaries without VP approver. | Save may succeed. |
| 2 | Owner/admin | Inspect policy card. | Configuration Error explaining missing approver. |
| 3 | Owner/admin | Look for Submit. | Submit is unavailable or action fails. |

## Scenario 3: boundary no-approval branch

| Step | Persona | Action | Expected observable |
|---|---|---|---|
| 1 | Owner/admin | Use exact amount or non-strategic/high-value data. | Save succeeds. |
| 2 | Owner/admin | Inspect policy card. | Not Required. |
| 3 | Owner/admin | Inspect approval panel. | No valid submit path for VP approval. |

## Scenario 4: VP denied edit path

| Step | Persona | Action | Expected observable |
|---|---|---|---|
| 1 | Regional VP | Open eligible Opportunity. | VP can inspect approval request. |
| 2 | Regional VP | Attempt input edit only if negative test requires it. | Save/edit refused or unavailable. |
| 3 | Regional VP | Approve/Reject pending request. | Decision succeeds only if VP is assigned actor. |

## Scenario 5: cross-module account impact

| Step | Persona | Action | Expected observable |
|---|---|---|---|
| 1 | Admin/operator | Open `SYN-MM-A05 Customer`. | Related Contacts, Opportunities, Cases visible. |
| 2 | Admin/operator | Trace O01/O02, S01-S03, C01-C03. | All point to same synthetic customer. |
| 3 | Admin/operator | Change a scoped relationship in a specific test. | Only documented dependencies change. |

## Scenario 6: optional business-rule restriction

| Step | Persona | Action | Expected observable |
|---|---|---|---|
| 1 | Admin/operator | Enable one demo rule intentionally. | Configuration clearly shows enabled rule/version. |
| 2 | Business operator | Try affected action. | `DEMO_RULE` block is expected if rule applies. |
| 3 | Admin/operator | Restore baseline. | Normal flow works again. |

## Evidence checklist

- Actor/persona used.
- Exact synthetic record.
- Input values before action.
- Action taken.
- Visible result.
- Independent readback page or history.
- Whether the outcome is pass, fail, or expected negative.

