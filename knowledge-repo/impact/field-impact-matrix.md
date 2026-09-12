# Field impact matrix

## Opportunity fields

| Field / input | Business role | If changed, inspect |
|---|---|---|
| Name | Synthetic scope and approval entry condition. | Workbench validation, native approval eligibility, tester search. |
| Account Name | Customer relationship. | Account related Opportunities, customer 360 scenario, converted lead scenario. |
| Stage | Sales pipeline state. | Pipeline scenario, optional Opportunity minimum/primary-contact rules. Should not by itself create strategic evaluation. |
| Close Date | Sales timing. | Workbench save validation and demo repeatability. |
| Amount | Strategic approval boundary and opportunity value. | Policy status, reason, evaluation history, Account related-list totals. |
| Strategic Deal | Enables strategic approval branch. | Policy status and Regional VP approval eligibility. |
| Discount | Strategic approval boundary. | Policy status, reason, evaluation history. |
| Regional VP Approver | Native routing. | Configuration Error branch, Submit action availability, assigned VP Approve/Reject visibility. |
| Approval Status | Stored policy/native outcome indicator. | Policy card, approval process final action, release/governance evidence. Do not hand-edit. |
| Approval Reason | Explanation for policy state. | Policy card and agent advisory. |
| Policy Rule Version | Which rule version produced current policy. | Rule-version mismatch warning and stale-evidence analysis. |
| Policy Evaluated At | When policy last evaluated. | Whether a save created a new evaluation. |

## Related records

| Relationship | Impact |
|---|---|
| Opportunity -> Account | Drives customer 360 and Account related-list evidence. |
| Opportunity -> Regional VP Approver | Drives native approval route. |
| Opportunity -> Evaluation history | Shows policy evidence; should accumulate for relevant policy input changes. |
| Opportunity -> Contact Roles | Used by buying-team and primary-contact scenarios; not the strategic approval route. |
| Case -> Contact/Account | Contact reparenting should not automatically rewrite existing Case Account. |
| Task -> Case/Contact/Account | Activity completion evidence; does not close Case automatically. |
| Campaign Member -> Contact/Lead | Response tracking evidence; must not send external communication. |

## High-risk changes

- Changing approval thresholds affects test expected results and policy-card explanation.
- Changing Regional VP Approver affects both configuration validity and who can decide.
- Changing Account relationships can invalidate cross-module scenario counts.
- Changing optional demo-rule settings can make normal saves fail by design.

