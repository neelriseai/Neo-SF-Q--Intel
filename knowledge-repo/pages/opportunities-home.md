# Page: Opportunities home

## What this page is for

Opportunities Home is the search and selection page for existing demo deals. Testers use it to open the exact synthetic Opportunity before verifying policy, approval, or cross-module behavior.

## Primary object

Opportunity.

## How a tester uses it

1. Open Strategic Deal Assurance.
2. Open Opportunities.
3. Switch away from Recently Viewed if the expected records do not appear.
4. Search the exact synthetic name such as `SYN-SDA-04 Above 15 Percent` or `SYN-MM-O02 Deal`.
5. Open exactly one matching record.

## Known demo Opportunities

| Record | Meaning |
|---|---|
| `SYN-SDA-01 Non Strategic` | Amount and discount high, but Strategic Deal is false; should be Not Required. |
| `SYN-SDA-02 Exact Amount` | Exactly USD 50,000,000; does not exceed amount boundary. |
| `SYN-SDA-03 Exact 15 Percent` | Exactly 15% boundary example. |
| `SYN-SDA-04 Above 15 Percent` | Main approval-eligible baseline example. |
| `SYN-SDA-05 Missing Approver` | Configuration Error branch. |
| `SYN-SDA-06 Null Discount` | Null input branch. |
| `SYN-SDA-07 Null Amount` | Null input branch. |
| `SYN-MM-O01 Deal` | Cross-module pipeline progression example. |
| `SYN-MM-O02 Deal` | Cross-module VP approval/rejection example. |

## What can go wrong

- Recently Viewed may hide records created by scripts.
- Similar synthetic names can be misleading; open the record and inspect Account, Amount, Discount, Strategic Deal, and approver before acting.
- Lead conversion scenarios can create additional synthetic Opportunities; do not confuse converted deals with fixed baseline deals.

## Dependencies

Opportunities opened from this page feed:

- Opportunity record page.
- Strategic Deal Policy card.
- Regional VP Approval panel.
- Account related-list relationship checks.
- Cross-module Contact Role scenarios.

