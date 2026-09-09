import { expect, test, type Page } from "@playwright/test";
import type { AssuranceRun } from "../../../apps/web/src/lib/types.js";

const sha = (character: string) => character.repeat(64);

type MockRun = AssuranceRun & { governance: NonNullable<AssuranceRun["governance"]> };

function baseRun(): MockRun {
  return {
    schema_version: "2.0.0",
    run_id: "11111111-1111-4111-8111-111111111111",
    trace_id: "22222222-2222-4222-8222-222222222222",
    created_at: "2026-09-09T00:00:00Z",
    reasoning_policy_version: "1.1.0",
    reasoning_policy_sha256: sha("a"),
    reasoning_eval_set_id: "retrieval-boundaries-v1",
    reasoning_eval_set_sha256: sha("b"),
    source_snapshot: "fixture-snapshot",
    source_graph_sha256: sha("f"),
    ontology_id: "change-evidence-core",
    ontology_version: "1.0.0",
    ontology_sha256: sha("c"),
    source_profile_id: "generic-source-profile",
    source_profile_version: "1.0.0",
    source_profile_sha256: sha("d"),
    normalized_graph_sha256: sha("e"),
    status: "COMPLETED",
    request: {
      requirement: "Assess a planned change to a renamed module",
      changed_paths: [],
      change_intent: "PLANNED_CHANGE",
      source_ref: "working-tree",
    },
    evidence: [],
    impacts: [],
    selected_tests: [],
    test_results: [],
    analysis_gaps: [],
    healing_proposals: [],
    activities: [],
    deliverables: [],
    claims: [],
    governance: {
      policy_version: "1.0.0",
      policy_sha256: sha("9"),
      analysis_input_sha256: sha("8"),
      metrics: [],
      guardrails: [],
      violations: [],
      passed: false,
    },
    decision: { code: "INCOMPLETE", reasons: ["Release evidence is incomplete"], evidence_ids: [] },
    recorded_decision: null,
  };
}

async function mockRun(page: Page, run: MockRun) {
  await page.route("**/api/v1/assurance-runs", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(run) });
  });
}

async function submit(page: Page) {
  await page.getByLabel("Requirement or observed change").fill("Assess an arbitrary renamed component");
  await page.getByRole("button", { name: "Analyze change" }).click();
}

test("dashboard opens as a clean, truthful assurance workspace", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Trace impact/ })).toBeVisible();
  await expect(page.getByLabel("Requirement or observed change")).toHaveValue("");
  await expect(page.getByLabel("Changed paths optional")).toHaveAttribute("placeholder", "relative/path/to/source");
  await expect(page.getByRole("button", { name: "Analyze change" })).toBeDisabled();
  await expect(page.getByText("CITATIONS ONLY", { exact: true })).toBeVisible();
  await expect(page.getByTestId("semantic-state")).toContainText("NOT PARTICIPATING");
  await expect(page.getByTestId("specialist-state")).toContainText("NOT PARTICIPATING");
  await expect(page.getByLabel("Release posture")).toContainText("NOT EXPOSED");
  await expect(page.getByText("graph connected")).toHaveCount(0);
  await expect(page.getByText("snapshot grounded")).toHaveCount(0);
});

test("populated run keeps evidence, selection, execution, and proposals distinct", async ({ page }) => {
  const run = baseRun();
  run.evidence = [
    { evidence_id: "evidence:confirmed", kind: "component", label: "Module Kappa", source: "not-rendered", state: "CONFIRMED", attributes: {} },
    { evidence_id: "evidence:human", kind: "review", label: "Scoped review note", source: "not-rendered", state: "HUMAN_CONFIRMED", attributes: {} },
    { evidence_id: "evidence:inferred", kind: "candidate", label: "Related candidate", source: "not-rendered", state: "INFERRED", attributes: {} },
  ];
  run.impacts = [{ entity_id: "entity:kappa", label: "Module Kappa", kind: "component", relation: "DEPENDS_ON", severity: "MEDIUM", evidence_strength: 0.8, strength_basis: "DECLARED_CHANGE_MATCH", evidence_ids: ["evidence:confirmed"] }];
  run.selected_tests = [
    { test_id: "test:selected", label: "Renamed module validation", classification: "MANDATORY", reason: "Graph-linked obligation", evidence_ids: ["evidence:confirmed"] },
    { test_id: "test:receipt", label: "Receipt-bearing validation", classification: "RECOMMENDED", reason: "Additional coverage", evidence_ids: ["evidence:confirmed"] },
  ];
  run.test_results = [{ test_id: "test:receipt", outcome: "PASSED", runner_id: "runner-local", result_sha256: sha("7"), source_snapshot: "fixture-snapshot", executed_at: "2026-09-09T00:00:00Z", valid_until: "2026-09-10T00:00:00Z", evidence_ids: ["evidence:confirmed"] }];
  run.healing_proposals = [{ target_id: "view:kappa", strategy: "Prefer a stable role-based locator", rationale: "Current selector is presentation-coupled", ranking_score: 0.72, score_basis: "METADATA_CANDIDATE", evidence_ids: ["evidence:inferred"], requires_human_approval: true }];
  run.activities = [{ activity_id: "33333333-3333-4333-8333-333333333333", capability_ids: ["reasoning.graph-impact"], agent: "Change Analyst", stage: "change_analysis", status: "COMPLETED", started_at: "2026-09-09T00:00:00Z", completed_at: "2026-09-09T00:00:00Z", duration_ms: 6, summary: "Compiled typed impact findings", input_evidence_ids: ["evidence:confirmed"], output_artifact_ids: ["impact:kappa"], policy_refs: ["analysis:1.0.0"], gap_codes: [], error_class: null }];
  run.governance.metrics = [{ metric: "material_claim_evidence_coverage", numerator: 1, denominator: 1, target: 1, comparator: "AT_LEAST", minimum_sample_size: 1, status: "PASSED", blocking: true }];
  run.governance.guardrails = [{ control_id: "release.evidence-model", control_version: "1.0.0", stage: "RELEASE", outcome: "ABSTAIN", reason_code: "RELEASE_EVIDENCE_MODEL_INCOMPLETE", evidence_ids: [], observations: [], blocking: true }];
  await mockRun(page, run);
  await page.goto("/");
  await submit(page);

  await expect(page.getByText("Module Kappa", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("evidence:confirmed", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("not-rendered")).toHaveCount(0);
  await expect(page.getByTestId("semantic-state")).toContainText("NOT PARTICIPATING");
  await expect(page.getByTestId("specialist-state")).toContainText("NOT PARTICIPATING");

  await page.getByRole("tab", { name: "tests" }).click();
  await expect(page.getByText("NO EXECUTION RECEIPT", { exact: true })).toBeVisible();
  await expect(page.getByText("RECEIPT REPORTED", { exact: true })).toBeVisible();
  await expect(page.getByText(/Governance, not this view/)).toBeVisible();

  await page.getByRole("tab", { name: "healing" }).click();
  await expect(page.getByText("NOT APPLIED", { exact: true })).toBeVisible();
  await expect(page.getByText(/browser execution is not recorded/)).toBeVisible();

  await page.getByRole("tab", { name: "governance" }).click();
  await expect(page.getByText("release.evidence-model", { exact: true })).toBeVisible();
  await expect(page.getByText("RELEASE_EVIDENCE_MODEL_INCOMPLETE", { exact: true })).toBeVisible();
});

test("degraded run exposes contradictory, stale, rejected, unverified, and audit-only state", async ({ page }) => {
  const run = baseRun();
  run.evidence = [
    { evidence_id: "evidence:contradictory", kind: "fact", label: "Conflicting fact", source: "hidden", state: "CONTRADICTORY", attributes: {} },
    { evidence_id: "evidence:stale", kind: "fact", label: "Expired observation", source: "hidden", state: "STALE", attributes: {} },
    { evidence_id: "evidence:unverified", kind: "fact", label: "Unverified observation", source: "hidden", state: "UNVERIFIED", attributes: {} },
    { evidence_id: "evidence:rejected", kind: "fact", label: "Rejected observation", source: "hidden", state: "REJECTED", attributes: {} },
  ];
  run.analysis_gaps = [{ code: "EVIDENCE_CONTRADICTION", message: "Authoritative inputs disagree", entity_id: null, relation: null, blocking: true }];
  run.activities = [
    { activity_id: "44444444-4444-4444-8444-444444444444", capability_ids: ["reasoning.semantic-retrieval"], agent: "Candidate Retrieval", stage: "semantic_retrieval", status: "FAILED", started_at: "2026-09-09T00:00:00Z", completed_at: "2026-09-09T00:00:00Z", duration_ms: 2, summary: "Semantic dependency unavailable", input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: ["SEMANTIC_PROVIDER_UNAVAILABLE"], error_class: "DependencyUnavailable" },
    { activity_id: "66666666-6666-4666-8666-666666666666", capability_ids: ["reasoning.semantic-retrieval"], agent: "Candidate Retrieval", stage: "semantic_retrieval", status: "COMPLETED", started_at: "2026-09-08T00:00:00Z", completed_at: "2026-09-08T00:00:00Z", duration_ms: 2, summary: "Earlier retrieval activity completed", input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: [], error_class: null },
    { activity_id: "55555555-5555-4555-8555-555555555555", capability_ids: ["reasoning.graph-grounded-agent"], agent: "Graph Specialist", stage: "advisory_reasoning", status: "ABSTAINED", started_at: "2026-09-09T00:00:00Z", completed_at: "2026-09-09T00:00:00Z", duration_ms: 1, summary: "No advisory artifact emitted", input_evidence_ids: [], output_artifact_ids: [], policy_refs: [], gap_codes: ["CONTEXT_UNAVAILABLE"], error_class: null },
  ];
  run.governance.violations = ["Conflicting evidence remains unresolved"];
  run.recorded_decision = { code: "GO", reasons: ["Historical record only"], evidence_ids: [] };
  await mockRun(page, run);
  await page.goto("/");
  await submit(page);

  await expect(page.getByTestId("semantic-state")).toContainText("RECORDED MIXED");
  await expect(page.getByTestId("specialist-state")).toContainText("RECORDED ABSTAINED");
  await expect(page.getByText("Explicit contradiction recorded")).toBeVisible();
  await expect(page.locator(".evidenceLane.conflict")).toContainText("CONTRADICTORY");
  await expect(page.locator(".evidenceLane.needs_verification")).toContainText("STALE");
  await expect(page.locator(".evidenceLane.needs_verification")).toContainText("UNVERIFIED");
  await expect(page.locator(".evidenceLane.needs_verification")).toContainText("REJECTED");

  await page.getByRole("tab", { name: "governance" }).click();
  await expect(page.getByText("Recorded decision · audit only")).toBeVisible();
  await expect(page.getByText("GO", { exact: true })).toBeVisible();
  await expect(page.getByText("Conflicting evidence remains unresolved")).toBeVisible();
  await expect(page.getByText("SEVERITY NOT EXPOSED", { exact: true })).toHaveCount(2);
});

test("duplicate and unknown citations are withheld and dynamic text is escaped", async ({ page }) => {
  const run = baseRun();
  run.evidence = [
    { evidence_id: "evidence:duplicate", kind: "fact", label: "<img src=x onerror=window.__unsafe=true>", source: "hidden", state: "CONFIRMED", attributes: {} },
    { evidence_id: "evidence:duplicate", kind: "fact", label: "Second copy", source: "hidden", state: "UNVERIFIED", attributes: {} },
  ];
  run.impacts = [{ entity_id: "entity:renamed", label: "Arbitrary renamed entity", kind: "module", relation: "RELATES_TO", severity: "LOW", evidence_strength: 0.4, strength_basis: "SOURCE_LINK", evidence_ids: ["evidence:duplicate", "evidence:missing"] }];
  await mockRun(page, run);
  await page.goto("/");
  await submit(page);

  await expect(page.getByText("Ambiguous duplicate evidence IDs withheld: evidence:duplicate")).toBeVisible();
  await expect(page.getByText("Missing from run evidence: evidence:missing")).toBeVisible();
  await expect(page.getByText("<img src=x onerror=window.__unsafe=true>", { exact: true })).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
  await page.getByRole("tab", { name: "governance" }).click();
  await expect(page.getByText("DUPLICATE_EVIDENCE_ID", { exact: true })).toBeVisible();
});

test("editing inputs or a failed follow-up cannot leave stale run evidence on screen", async ({ page }) => {
  const run = baseRun();
  run.evidence = [{ evidence_id: "evidence:old", kind: "fact", label: "Prior run evidence", source: "hidden", state: "CONFIRMED", attributes: {} }];
  let requestCount = 0;
  await page.route("**/api/v1/assurance-runs", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(run) });
      return;
    }
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "temporarily unavailable" }) });
  });
  await page.goto("/");
  await submit(page);
  await expect(page.getByText("Prior run evidence", { exact: true })).toBeVisible();

  await page.getByLabel("Requirement or observed change").fill("Assess a different arbitrary component");
  await expect(page.getByText("Prior run evidence", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Analyze change" }).click();
  await expect(page.getByText(/Analysis failed \(503\)/)).toBeVisible();
  await expect(page.getByText("Prior run evidence", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Release posture")).toContainText("AWAITING RUN");
});

test("completed run without an effective decision fails closed", async ({ page }) => {
  const run = baseRun();
  run.decision = null;
  await mockRun(page, run);
  await page.goto("/");
  await submit(page);
  await expect(page.getByLabel("Release posture")).toContainText("INCOMPLETE");
  await expect(page.getByLabel("Release posture")).toContainText("completed run does not include an effective release decision");
  await page.getByRole("tab", { name: "governance" }).click();
  await expect(page.getByText("EFFECTIVE_DECISION_MISSING", { exact: true })).toBeVisible();
});

test("tabs support keyboard navigation and the dashboard remains usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const evidenceTab = page.getByRole("tab", { name: "evidence" });
  await evidenceTab.focus();
  await evidenceTab.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "tests" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "tests" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Start an analysis" })).toBeVisible();
  await expect(page.getByLabel("Requirement or observed change")).toBeVisible();
});
