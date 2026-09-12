# Page dependency map

## Strategic-deal path

```text
Deal Workbench
  creates/updates Opportunity
  -> Opportunity Record Page
       -> Strategic Deal Policy card
       -> Regional VP Approval panel
            -> Native approval history
```

## Cross-module path

```text
Accounts Home
  -> Account record
       -> Contacts
       -> Opportunities
       -> Cases
       -> Tasks / activities
       -> Campaign membership through people records
```

## Approval persona path

```text
Owner session
  -> Save and Evaluate
  -> Submit for Approval
  -> Pending native request

Regional VP session
  -> Open same Opportunity
  -> Approve or Reject
  -> Owner refreshes evidence
```

## Places a tester must look after a change

| Change location | Check these pages |
|---|---|
| Workbench input changed | Workbench message, Policy card, Opportunity record, Evaluation History. |
| Owner submits approval | Approval panel in owner session, Approval panel in VP session, record lock state. |
| VP approves/rejects | VP approval panel, owner approval panel after refresh, Opportunity approval status. |
| Account reparented | Account hierarchy, child counts, Contacts and Opportunities still linked correctly. |
| Contact reparented | Contact record, old/new Account related lists, existing Case Account. |
| Campaign member status changed | Campaign member list, Campaign response stats, person Campaign History. |

