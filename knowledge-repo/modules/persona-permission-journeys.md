# Module: persona and permission journeys

## Personas

| Persona | What they are for | What they must not prove |
|---|---|---|
| Demo administrator / owner | Setup, synthetic data operation, Workbench create/edit, owner submission. | Least-privilege business-user proof. |
| Strategic deal user / UI runner | Normal Workbench operation in UI-oriented tests. | Native VP approval decision. |
| Synthetic Regional VP | Approve/reject assigned native approval work item. | Creating or editing the deal input as the owner. |
| Change assurance integration / API access | Read policy/evidence through agent-facing interfaces. | Human approval authority. |
| Demo rule operator | Operate optional cross-module rule demos. | Strategic VP approval or broad admin access. |

## Journey: create and evaluate as owner

1. Owner opens Deal Workbench.
2. Owner fills valid synthetic Opportunity fields.
3. Owner clicks Save and Evaluate.
4. Expected: success message; policy card refresh; record exists.

## Journey: submit as owner

1. Owner opens eligible Opportunity.
2. Policy card says Pending Regional VP.
3. Regional VP Approver is active and not owner.
4. Owner clicks Submit for Approval.
5. Expected: Latest request Pending; owner can Recall; owner cannot Approve/Reject.

## Journey: decide as Regional VP

1. Regional VP logs in separately.
2. VP opens the same Opportunity.
3. VP refreshes approval panel if needed.
4. VP sees Approve/Reject.
5. VP chooses Approve or Reject.
6. Expected: native request records the decision; owner sees final status after refresh.

## Journey: VP denied from editing

1. VP opens Workbench or Opportunity.
2. VP attempts to edit business inputs only if this is the explicit negative test.
3. Expected: the app or Salesforce refuses the save, or the editable create form is not available.
4. This is a security/governance pass when the record remains unchanged.

## Failure classification

| Visible result | Classification |
|---|---|
| VP cannot edit input | Governance/security expected behavior. |
| Owner cannot see Submit despite eligible-looking deal | Setup/state issue; inspect approver, owner, name prefix, and policy status. |
| VP cannot see Approve/Reject on pending request | Actor mismatch or session mismatch. |
| Automation cannot locate a visible control | Locator/automation issue, not business failure. |
| Salesforce reports insufficient access | Permission or sharing issue; do not grant admin as a shortcut unless the scenario is explicitly admin. |

