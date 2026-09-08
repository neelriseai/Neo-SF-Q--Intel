# UI automation and healing

## Worker boundary

The first TypeScript Playwright package implements and tests the metadata-aware locator healer.
The next integration slice wraps it in a typed standard-input/standard-output worker that owns
browser processes, traces, screenshots and DOM/accessibility capture; Python owns intent,
governance and persisted evidence.

## Healing algorithm

1. Capture current visible candidates inside the scoped page/record context.
2. Match exact Salesforce metadata and stable semantic attributes.
3. Score label, role, control type, section context, visibility and enabled state.
4. Require uniqueness and negative-state checks.
5. Attempt a non-destructive probe where possible.
6. Accept only above policy threshold; otherwise abstain.
7. Persist candidates, features, score, selected locator and before/after evidence.

The engine accepts generic `HealingIntent`; it contains no page-specific `if StrategicDiscount` behavior.

## Initial framework scope

- Playwright TypeScript.
- Chromium or installed Edge only.
- One Salesforce session URL consumed in memory.
- Assisted Salesforce metadata/DOM healing.
- No universal cross-framework repair claim.
