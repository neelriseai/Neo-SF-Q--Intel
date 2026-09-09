# A8 dashboard evidence-view review

Date: 2026-09-09

Capability: `ui.assurance-dashboard`

Status: APPROVED as `FOUNDATION`

## Acceptance boundary

The dashboard is a generic, read-only projection of `AssuranceRun`. It must not derive graph-path
order, release authority, live dependency availability, execution, healing application or evidence
conflicts from labels or narrative text. A first-source demo is test evidence, not runtime scope.

## Independent review lanes

- Genericity/architecture/scope: `a8_genericity_review` — APPROVED, no P0/P1 findings.
- Governance/evidence/safety: `a8_governance_review` — APPROVED, no P0/P1 findings.

The reviews required removal of demo-prefilled inputs and source-path placeholders; exact typed
evidence lanes; duplicate-citation withholding; no free-text conflict detection; no activity-to-live
availability inference; fail-closed terminal missing decisions; stale-result clearing; selected-test
versus execution-receipt separation; proposal-versus-applied separation; guardrail and violation
visibility; sensitive source/attribute suppression; audit-only historical decisions; and keyboard
tab behavior.

## Verified behavior

- Generic empty inputs and arbitrary renamed fixtures prove the view is not tied to the demo data.
- Source-confirmed, human-recorded, inferred, contradictory and unresolved evidence are separate.
- Missing and duplicate citations are visibly withheld from impact linkage.
- Semantic and specialist states describe only activity recorded on the run, including mixed state.
- A completed run without a current decision displays `INCOMPLETE` and an integrity signal.
- Dynamic text is rendered as text; the XSS fixture creates no executable image element.
- Input edits and failed follow-up submissions cannot leave stale evidence visible.
- Stable tab panels support Left/Right/Home/End keyboard navigation and a mobile viewport.

Focused verification independently passed TypeScript checks, seven dashboard Playwright scenarios,
the Next.js production build and `git diff --check`.

## Retained foundation gaps

- Ordered evidence-path receipts and A6 advisory payloads are not present in the run API.
- `/health` run/outcome durability and degradation are not displayed, and no persistence claim is made.
- The client currently uses a compile-time response cast rather than runtime schema validation.
- Dedicated zero-sample/insufficient-sample fixtures and automated axe/contrast auditing remain P2.

These gaps do not weaken a current claim; they prevent promotion beyond `FOUNDATION`.
