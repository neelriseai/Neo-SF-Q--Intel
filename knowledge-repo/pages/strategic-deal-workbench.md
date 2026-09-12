# Page: Strategic Deal Workbench

## What this page is for

The Deal Workbench is where a business operator creates or edits a synthetic Opportunity and asks the app to evaluate the current strategic-discount policy. It is the main action page for the demo.

Live-observed title: **Deal Workbench**  
Live-observed subtitle: **STRATEGIC DEAL ASSURANCE · SYNTHETIC DEMO**  
Live-observed instruction: **Save a deal to evaluate the current policy and capture an auditable input snapshot. All amounts are USD.**

## Primary object

Opportunity.

The user is not creating a separate custom object. The page writes standard Opportunity fields plus custom policy fields on Opportunity.

## Who should use it

| Persona | Expected use |
|---|---|
| Demo owner / admin operator | Can create or edit synthetic deals and submit eligible deals for approval. |
| Strategic deal user / UI runner | Intended business-user path for filling deal inputs. |
| Regional VP | Should be treated as decision persona, not create/edit persona. If VP can see the Workbench, failed save/edit is governance behavior, not locator-healing failure. |
| API/integration user | Reads evidence through API; should not be used as approval persona. |

## Live-observed page elements

The live page showed these controls:

| Element | What tester does | Quirk / note |
|---|---|---|
| Name | Enter a synthetic Opportunity name. | Must begin with `SYN-`; otherwise the page rejects it before save. |
| Account Name | Search/select the Account. | Lookup field; type enough of the synthetic account name and select the suggestion. Text that is typed but not selected may not stick. |
| Stage | Select Opportunity stage. | Live page showed Stage as required and initially `--None--`. |
| Close Date | Enter/select close date. | Live page showed date format hint: `Dec 31, 2024`. Demo data uses `Dec 31, 2026` / `2026-12-31`. |
| Amount | Enter USD amount. | Approval boundary is strictly greater than USD 50,000,000. |
| Strategic Deal | Checkbox. | Must be checked for strategic approval to be required. It is a checkbox, not a dropdown. |
| Discount | Enter discount percent. | Approval boundary is greater than or equal to 15% in current policy implementation; branch examples include exactly 15 and 15.01. |
| Regional VP Approver | Search/select active VP user. | Must be an active user and not the owner. Use lookup suggestion selection, not plain typed text. |
| Save and Evaluate | Saves the Opportunity and refreshes policy result. | A successful save shows `Saved successfully. The policy results are shown below.` |
| Start new deal | Appears after creating a record from the Workbench. | Clears the current edit context for a new synthetic record. |
| Strategic Deal Policy card | Shows status, reason, rule versions, and history. | Initial state without a record says `Create or select an Opportunity to view its policy results.` |
| Regional VP Approval panel | Appears after there is a record. | Native approval is separate from policy evaluation. |

## Field behavior in plain English

- The page accepts only synthetic Opportunity names beginning with `SYN-`.
- Amounts are USD. Do not interpret the legacy internal field name `Amount_INR` in older artifacts as INR.
- Save and Evaluate is not a force-recompute button for every edit. The important business expectation is that relevant policy inputs create/update policy evidence; unrelated stage-only changes should not create a new strategic evaluation.
- Approval outcome cannot be edited directly on this page.

## Main happy path

1. Log in as demo owner/admin operator.
2. Open Strategic Deal Assurance > Deal Workbench.
3. Fill:
   - Name: `SYN-SDA-Live-Approval-001`
   - Account Name: `SYN-SDA Demo Account`
   - Stage: `Prospecting`
   - Close Date: `Dec 31, 2026`
   - Amount: `50000000.01`
   - Strategic Deal: checked
   - Discount: `15.01`
   - Regional VP Approver: `Synthetic Regional VP`
4. Click Save and Evaluate.
5. Expected visible result:
   - Success message appears.
   - Policy card shows `Pending Regional VP`.
   - Reason says the deal exceeds the configured amount and discount boundaries.
   - Approval panel becomes relevant for submission.

## Branches and failure meanings

| Symptom | Meaning | Agent response |
|---|---|---|
| `Use a synthetic Opportunity name beginning with SYN-.` | User entered a non-synthetic name. | Correct the test data; do not bypass. |
| Save error after VP login | Likely expected persona restriction. | Classify as governance/security result if VP is not supposed to edit. |
| Policy status `Not Required` | One or more boundaries were not crossed. | Check amount, discount, strategic checkbox, and threshold semantics. |
| Policy status `Configuration Error` | Approval should be required but setup is invalid, commonly missing approver. | Do not submit; fix approver/config under authorized persona. |
| Lookup text disappears or save misses lookup | User typed but did not select lookup suggestion. | Re-open lookup, type, wait for suggestion, click exact synthetic row. |
| Policy card says no evaluations | No successful policy-input save yet, or record not selected. | Save valid inputs or open the correct record. |

## Dependencies

- Feeds the Opportunity record page.
- Feeds the Strategic Deal Policy card.
- Enables the Regional VP Approval panel only after a record exists and policy status is eligible.
- Depends on active strategic discount metadata and an active, separate Regional VP approver.

## What must never happen

- Do not use real customer names, real emails, or real opportunity data.
- Do not choose the deal owner as Regional VP approver.
- Do not hand-edit approval status to force the policy card.
- Do not treat a missing or failed VP save as a locator problem until persona permissions are checked.

