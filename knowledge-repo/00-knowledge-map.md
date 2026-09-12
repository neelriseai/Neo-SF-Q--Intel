# Knowledge map

## Business module

Strategic Deal Assurance demonstrates a governed Salesforce business-rule flow:

1. A user creates or edits a synthetic Opportunity.
2. The app evaluates whether the Opportunity needs Regional VP approval.
3. The policy card explains the result and shows evaluation history.
4. If approval is required, the owner submits the Opportunity to native Salesforce approval.
5. Only the assigned Regional VP can approve or reject.
6. The final decision is reflected on the Opportunity and in native approval history.

## Primary pages and what each is for

| Page | User job | Primary object | Main assertion |
|---|---|---|---|
| [Deal Workbench](pages/strategic-deal-workbench.md) | Create/edit synthetic strategic Opportunities and trigger policy evaluation | Opportunity | Save creates/updates the deal and refreshes policy evidence. |
| [Opportunity Record Page](pages/opportunity-record-page.md) | Read record state, policy card, and native approval panel | Opportunity | Policy status and native approval status are both visible and must not be confused. |
| [Opportunities Home](pages/opportunities-home.md) | Find existing demo Opportunities and open records | Opportunity | Tester can select the exact synthetic case. |
| [Accounts Home](pages/accounts-home.md) | Trace customer/account relationships for cross-module scenarios | Account | Customer relationships are not inferred from names alone. |
| [CRM Native Pages](pages/crm-native-pages.md) | Run cross-module demo flows for Contacts, Leads, Cases, Tasks, Campaigns, Reports | Multiple standard objects | Native page changes can impact scenario evidence without changing strategic policy. |

## Embedded business modules

| Module | Purpose |
|---|---|
| [Strategic deal policy and approval](modules/strategic-deal-policy-and-approval.md) | Explains the discount rule, boundary behavior, status meanings, and native approval handoff. |
| [Persona and permission journeys](modules/persona-permission-journeys.md) | Explains owner/admin, Regional VP, integration/API, and demo operator responsibilities. |
| [Demo data and examples](modules/demo-data-and-examples.md) | Provides known-good synthetic examples and branch cases. |
| [Configurable cross-module rules](modules/configurable-cross-module-rules.md) | Explains the optional CRM rule switches and how they relate to the strategic policy. |

## Dependency flow

```text
Opportunity fields
  -> Strategic discount policy evaluation
  -> Opportunity approval status and reason
  -> Strategic Deal Policy card
  -> Regional VP Approval panel eligibility
  -> Native Salesforce approval request
  -> Approve / Reject / Recall history
```

## Key distinction agents must preserve

Policy status is not the same as native approval status.

- `Pending Regional VP` on the policy card means the Opportunity is eligible for VP approval.
- A native request exists only after the owner clicks Submit for Approval.
- Approval/rejection is a human decision on the native request; it should not create a fresh policy evaluation by itself.

