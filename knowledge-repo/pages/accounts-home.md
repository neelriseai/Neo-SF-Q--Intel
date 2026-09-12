# Page: Accounts home

## What this page is for

Accounts Home is used for customer relationship tracing. It is not the primary strategic-policy page, but it proves that Opportunities, Contacts, Cases, Tasks, and Campaign relationships all point back to the intended synthetic customer.

## Primary object

Account.

## Main tester jobs

- Find `SYN-SDA Demo Account` for the strategic-deal baseline.
- Find `SYN-MM-A..` customer/group accounts for cross-module scenarios.
- Verify parent/child Account hierarchy.
- Verify related Contacts, Opportunities, Cases, and Tasks.

## Important relationship rule

Do not infer relationships from record names. Open the lookup links and related lists.

For example, `SYN-MM-A05 Customer` is expected to connect to:

- Contacts `Synthetic SYN-MM-C01` through `Synthetic SYN-MM-C03`.
- Opportunities `SYN-MM-O01 Deal` and `SYN-MM-O02 Deal`.
- Cases `SYN-MM-S01` through `SYN-MM-S03`.
- Tasks directly or indirectly related to the account, opportunities, or cases.

## Impact of changes

| Change | Expected impact |
|---|---|
| Change Account parent | Account hierarchy changes; existing Contacts and Opportunities should stay on the same customer account unless explicitly reparented. |
| Change Contact Account | Contact moves; existing Case Account is not automatically rewritten. |
| Change Opportunity Account | Deal moves to another Account and impacts related-list evidence. |
| Rename synthetic Account | Breaks tester search and can cause wrong-record selection. Avoid unless it is the tested change. |

## Failure meanings

- Missing records in Recently Viewed usually means list-view filtering, not missing data.
- Related-list count mismatch may be a real relationship defect or residue from a previous manual scenario. Check cleanup status before filing product defect.
- A correct aggregate count is not sufficient if any lookup points to the wrong synthetic record.

