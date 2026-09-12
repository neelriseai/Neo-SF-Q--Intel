# Strategic Deal Assurance knowledge repo

This package is for agents and testers who need business understanding of the Salesforce demo app. It intentionally avoids git-level implementation notes. Treat it as the day-one briefing a competent QA hire would need before testing or reasoning over the app.

Use this repo when an agent must answer:

- What is this page for?
- Which persona should perform this step?
- Which fields matter to the business rule?
- What should visibly change after a user action?
- If a field changes, where can the effect cascade?
- Which failures are expected governance behavior versus product or automation defects?

## Start here

1. [Knowledge map](00-knowledge-map.md)
2. [Strategic Deal Workbench page](pages/strategic-deal-workbench.md)
3. [Opportunity record page](pages/opportunity-record-page.md)
4. [Strategic deal policy and approval rules](modules/strategic-deal-policy-and-approval.md)
5. [Persona and permission journeys](modules/persona-permission-journeys.md)
6. [Field impact matrix](impact/field-impact-matrix.md)
7. [E2E scenario matrix](impact/e2e-scenario-matrix.md)

## Live observation status

The Deal Workbench page was inspected live in Salesforce on 2026-09-12. The observed app navigation included Deal Workbench, Opportunities, Accounts, Contacts, Leads, Cases, Tasks, Campaigns, and Reports. The observed Workbench body included Name, Account Name, Stage, Close Date, Amount, Strategic Deal, Discount, Regional VP Approver, Save and Evaluate, and the initial Strategic Deal Policy card.

Some persona-specific outcomes are described from the app's approval rules and prior observed behavior. If a tester has both owner/admin and Regional VP sessions open, re-check the persona-specific action visibility before recording final evidence.

## Hard safety rules

- Use synthetic data only.
- Do not hand-edit the approval status to make a demo pass.
- Do not approve as the same person who submitted.
- Do not use administrator override as proof that a Regional VP decision is valid.
- Do not treat a UI toast alone as proof; verify the record, policy card, approval panel, or history.
- If the screen is ambiguous or two similarly named synthetic records appear, stop and identify the exact record instead of guessing.

