# Pages: CRM native module pages

This app also exposes standard Salesforce CRM pages for broader business-flow evidence. These pages support agent reasoning about impact beyond the custom Workbench.

## Contacts

Purpose: Verify people associated with Accounts, Cases, Campaigns, and Opportunity contact roles.

Key scenario:

- Reassign `Synthetic SYN-MM-C04` from one customer Account to another.
- Expected: Contact Account changes; existing Case Account does not automatically rewrite.

Agent caution: changing Contact Account is not the same as moving existing Cases or Opportunities.

## Leads

Purpose: Verify qualification and conversion into Account, Contact, and Opportunity.

Key scenario:

- Update `Synthetic SYN-MM-L30` to qualified status.
- Convert into existing synthetic Account and new Contact/Opportunity.
- Run this late because conversion is not automatically undone.

Agent caution: do not convert real leads or retry conversion blindly after a timeout.

## Cases

Purpose: Verify service triage and closure.

Key scenario:

- Open `SYN-MM-S01 Support`.
- Move from New/Medium to Working/High.
- Close after required resolution behavior is satisfied if the optional rule is enabled.

Agent caution: Case closure does not prove strategic policy behavior. It proves service workflow and optional demo-rule behavior.

## Tasks

Purpose: Verify follow-up activity relationships and completion.

Key scenario:

- Open `SYN-MM-T41 Follow Up`.
- Confirm Related To and Name links.
- Move to In Progress and Completed.

Agent caution: completing a Task does not close its Case.

## Campaigns

Purpose: Verify campaign member status changes without external communication.

Key scenario:

- Open `SYN-MM-M01 Outreach`.
- Change one member from Sent to Responded.
- Confirm membership count remains unchanged.

Agent caution: do not click send-email or messaging actions. Status changes are internal evidence only.

## Reports

Purpose: Optional readback of synthetic records. Reports are not the source of truth for approval behavior.

Agent caution: if a report contradicts the record page, trust the record page and investigate the report filter.

