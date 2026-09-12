# Module: configurable cross-module rules

## What these rules are

The app includes optional demo business rules for standard CRM modules. They are separate from the strategic discount approval policy.

These rules let an administrator demonstrate that a business-rule change in Salesforce can affect multiple user journeys without changing application code.

## Rule catalog

| Rule | Business meaning |
|---|---|
| Account reparent | Blocks changing an Account's parent when enabled. |
| Contact reparent | Blocks changing a Contact's Account when enabled. |
| Lead qualification | Blocks conversion unless Lead was saved to the required status. |
| Opportunity minimum | Blocks Closed Won when Amount is below configured minimum or blank. |
| Opportunity primary contact | Requires a primary Contact Role before Closed Won. |
| Case resolution | Requires Demo Resolution before closing a Case. |
| Task due date | Requires Due Date before completing a Task. |
| Campaign active | Blocks member response changes on inactive Campaigns. |

## Important distinction

The strategic discount rule answers: "Does this high-value strategic deal need Regional VP approval?"

The configurable cross-module rules answer: "Should this CRM action be blocked under the current demo guard settings?"

Do not mix their thresholds or statuses.

## Failure meanings

| Error type | Meaning |
|---|---|
| `DEMO_RULE <key>` | Business restriction intentionally blocked the action. Negative test may be passing. |
| `DEMO_CONFIG` | Rule setup is missing or invalid. Fix authorized configuration, not the user journey. |
| Permission/sharing/locking error | Persona or data-state problem. Do not bypass with admin unless scenario permits it. |

## Agent guidance

- If a rule is intentionally enabled, a blocked save may be the correct result.
- If a rule is unintentionally enabled, restore baseline before running strategic approval tests.
- Optional rules should not create strategic policy evaluations unless Opportunity policy inputs actually changed.

