import { expect, test } from "@playwright/test";

test("dashboard presents a clean assurance workspace", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Know the blast radius/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Analyze change/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Specialist workflow stages" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Governance signals" })).toBeVisible();
});

test("dashboard renders a governed incomplete decision without false confidence", async ({ page }) => {
  await page.route("**/api/v1/assurance-runs", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "2.0.0",
        run_id: "11111111-1111-4111-8111-111111111111",
        trace_id: "22222222-2222-4222-8222-222222222222",
        created_at: "2026-09-09T00:00:00Z",
        reasoning_policy_version: "1.1.0",
        reasoning_policy_sha256: "a".repeat(64),
        reasoning_eval_set_id: "retrieval-boundaries-v1",
        reasoning_eval_set_sha256: "b".repeat(64),
        source_snapshot: "fixture-snapshot",
        source_graph_sha256: "f".repeat(64),
        ontology_id: "change-evidence-core",
        ontology_version: "1.0.0",
        ontology_sha256: "c".repeat(64),
        source_profile_id: "salesforce-application-graph",
        source_profile_version: "1.0.0",
        source_profile_sha256: "d".repeat(64),
        normalized_graph_sha256: "e".repeat(64),
        status: "COMPLETED",
        request: {
          requirement: "Assess a planned metadata change",
          changed_paths: [],
          change_intent: "PLANNED_CHANGE",
          source_ref: "working-tree",
        },
        evidence: [{ evidence_id: "evidence:1", kind: "field", label: "Configured field", source: "fixture.json", state: "CONFIRMED" }],
        impacts: [{ entity_id: "field:Example", label: "Configured field", kind: "field", relation: "direct:declared", severity: "MEDIUM", evidence_strength: 0.8, strength_basis: "DECLARED_CHANGE_MATCH", evidence_ids: ["evidence:1"] }],
        selected_tests: [{ test_id: "test:Example", label: "Configured test", classification: "RECOMMENDED" }],
        test_results: [],
        healing_proposals: [],
        activities: [{ activity_id: "activity:governance", capability_ids: ["governance.release-decision"], agent: "Governance Review", stage: "release_governance", status: "COMPLETED", started_at: "2026-09-09T00:00:00Z", completed_at: "2026-09-09T00:00:00Z", duration_ms: 4, summary: "Applied deterministic gates", input_evidence_ids: [], output_artifact_ids: ["decision:INCOMPLETE"], policy_refs: ["governance:fixture"], gap_codes: ["TEST_EXECUTION_MISSING"], error_class: null }],
        deliverables: [],
        governance: { metrics: [{ metric: "material_claim_evidence_coverage", numerator: 1, denominator: 1, target: 1, comparator: "AT_LEAST", minimum_sample_size: 1, status: "PASSED", blocking: true }], violations: [], passed: true },
        decision: { code: "INCOMPLETE", reasons: ["Selected validation lacks an execution receipt"], evidence_ids: [] },
        recorded_decision: null,
      }),
    });
  });
  await page.goto("/");
  await page.locator("select").selectOption("PLANNED_CHANGE");
  await page.getByRole("button", { name: /Analyze change/ }).click();
  await expect(page.getByText("INCOMPLETE", { exact: true })).toBeVisible();
  await expect(page.getByText("Governance Review", { exact: true })).toBeVisible();
  await expect(page.getByText("material claim evidence coverage")).toBeVisible();
  await expect(page.getByText("DECLARED CHANGE MATCH")).toBeVisible();
  await expect(page.locator("tbody tr").first()).toBeVisible();
});
