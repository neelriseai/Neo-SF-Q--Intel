# Module: demo data and examples

## Fixed baseline account

`SYN-SDA Demo Account`

Use this for strategic-deal examples unless a cross-module scenario names a different Account.

## Strategic-policy examples

| Case | Inputs | Expected policy status |
|---|---|---|
| Non-strategic high value | Amount 60,000,000; Discount 20%; Strategic Deal unchecked; approver present | Not Required |
| Exact amount boundary | Amount 50,000,000; Discount 20%; Strategic Deal checked | Not Required |
| Discount boundary | Amount 50,000,000.01; Discount 15%; Strategic Deal checked | Not Required in baseline demo data |
| Above discount boundary | Amount 50,000,000.01; Discount 15.01%; Strategic Deal checked; approver present | Pending Regional VP |
| Missing approver | Amount 50,000,000.01; Discount 15.01%; Strategic Deal checked; no approver | Configuration Error |
| Null discount | Amount 60,000,000; Discount blank | Not Required |
| Null amount | Amount blank; Discount 20% | Not Required |

## Good manual Workbench data

Use only synthetic names. Example:

| Field | Example |
|---|---|
| Name | `SYN-SDA-Live-Approval-001` |
| Account Name | `SYN-SDA Demo Account` |
| Stage | `Prospecting` |
| Close Date | `Dec 31, 2026` |
| Amount | `50000000.01` |
| Strategic Deal | checked |
| Discount | `15.01` |
| Regional VP Approver | `Synthetic Regional VP` |

## Cross-module examples

| Scenario | Main records |
|---|---|
| Customer 360 | `SYN-MM-A05 Customer`, contacts C01-C03, opportunities O01-O02, cases S01-S03 |
| Pipeline progression | `SYN-MM-O01 Deal` |
| VP approval/rejection | `SYN-MM-O02 Deal`, `Synthetic Regional VP` |
| Case triage | `SYN-MM-S01 Support` |
| Task completion | `SYN-MM-T41 Follow Up` |
| Campaign response | `SYN-MM-M01 Outreach`, `Synthetic SYN-MM-C01` |

## Repeatability notes

- Approval and evaluation histories intentionally accumulate.
- Lead conversion is normally not reversible.
- Some reset utilities restore input values but do not erase historical evidence.
- If a scenario leaves a pending approval, finish or recall it before reset or another approval test.

