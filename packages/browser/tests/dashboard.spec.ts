import { expect, test, type Page } from "@playwright/test";
import { decodeCandidateFoundationEvidence, type CandidateFoundationEvidence } from "../../../apps/web/src/lib/foundation-contract.js";
import { captureCandidateFoundation, resolveFoundationCaptureTimeout } from "../../../apps/web/src/lib/foundation-client.js";
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

const changeFoundationGaps = [
  "CANDIDATE_BUILD_NOT_VERIFIED",
  "CHANGE_SEED_SCOPE_NOT_ATTESTED",
  "CONFLICT_SCOPE_NOT_ATTESTED",
  "DEPLOYMENT_NOT_ATTESTED",
  "GIT_COMMIT_SIGNATURE_NOT_ATTESTED",
  "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
  "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
  "REPOSITORY_ORIGIN_NOT_ATTESTED",
  "RISK_FACTORS_NOT_ATTESTED",
  "TEST_EXECUTION_SCOPE_NOT_ATTESTED",
  "TEST_OBLIGATION_SCOPE_NOT_ATTESTED",
  "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
];
const graphFoundationGaps = [...changeFoundationGaps, "SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE"].sort();
const seedFoundationGaps = [...graphFoundationGaps, "GRAPH_INPUT_TREE_NOT_ATTESTED"].sort();

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const item = value as Record<string, unknown>;
  return `{${Object.keys(item).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(item[key])}`).join(",")}}`;
}

async function stableDigest(value: unknown): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson(value)));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function rehashFoundationDocument(document: Record<string, unknown>): Promise<Record<string, unknown>> {
  for (const stage of document.stages as Array<Record<string, unknown>>) {
    const body = { ...stage };
    delete body.evidence_sha256;
    stage.evidence_sha256 = await stableDigest(body);
  }
  const body = { ...document };
  delete body.chain_sha256;
  document.chain_sha256 = await stableDigest(body);
  return document;
}

async function baseFoundation(projectId = "project-renamed-alpha"): Promise<CandidateFoundationEvidence> {
  const common = {
    schema_version: "1.0.0" as const,
    authority_scope: "ANALYSIS_ONLY" as const,
    release_eligible: false as const,
    local_foundation_stage_complete: true,
    state: "EXECUTED" as const,
    policy_id: "foundation-stage-policy",
    policy_version: "1.0.0",
    policy_sha256: sha("a"),
    evaluated_at: "2026-09-10T08:00:00Z",
    valid_until: "2099-09-10T08:00:00Z",
  };
  const stages: CandidateFoundationEvidence["stages"] = [
    {
      ...common,
      stage: "VERIFIED_CHANGE_CAPTURE",
      sequence: 1,
      capability_id: "source.verified-change-set",
      input_receipts: [],
      output_receipt: { role: "verified-change", sha256: sha("1") },
      gap_codes: changeFoundationGaps,
      measurements: [{ name: "changed_file_count", value: 0 }],
      evidence_sha256: sha("4"),
    },
    {
      ...common,
      stage: "TREE_GRAPH_PRODUCTION",
      sequence: 2,
      capability_id: "source.tree-graph-production",
      input_receipts: [{ role: "verified-change", sha256: sha("1") }],
      output_receipt: { role: "graph-production", sha256: sha("2") },
      gap_codes: graphFoundationGaps,
      measurements: [],
      evidence_sha256: sha("5"),
    },
    {
      ...common,
      stage: "OPERATION_SEED_MAPPING",
      sequence: 3,
      capability_id: "source.change-seed-mapping",
      input_receipts: [
        { role: "graph-production", sha256: sha("2") },
        { role: "verified-change", sha256: sha("1") },
      ],
      output_receipt: { role: "operation-seeds", sha256: sha("3") },
      gap_codes: seedFoundationGaps,
      measurements: [{ name: "operation_seed_count", value: 2 }],
      evidence_sha256: sha("6"),
    },
  ];
  const evidence: CandidateFoundationEvidence = {
    schema_version: "1.0.0",
    authority_scope: "ANALYSIS_ONLY",
    release_eligible: false,
    project_id: projectId,
    stages,
    outcome: "EXECUTED",
    foundation_execution_complete: true,
    evidence_completeness: "INCOMPLETE",
    non_authoritative_projection: true,
    pipeline_policy_id: "candidate-foundation-pipeline",
    pipeline_policy_version: "1.0.1",
    pipeline_policy_sha256: sha("7"),
    pipeline_implementation_sha256: sha("8"),
    blocking_gap_codes: seedFoundationGaps,
    chain_sha256: sha("9"),
  };
  return await rehashFoundationDocument(evidence as unknown as Record<string, unknown>) as unknown as CandidateFoundationEvidence;
}

async function abstainedFoundation(): Promise<CandidateFoundationEvidence> {
  const evidence = await baseFoundation("arbitrary-project-beta");
  evidence.stages[1] = {
    ...evidence.stages[1],
    state: "FAILED",
    local_foundation_stage_complete: false,
    output_receipt: null,
    evaluated_at: "unavailable",
    valid_until: null,
    gap_codes: [...graphFoundationGaps, "GRAPH_CAPTURE_FAILED"].sort(),
    measurements: [],
  };
  evidence.stages[2] = {
    ...evidence.stages[2],
    state: "NOT_RUN",
    local_foundation_stage_complete: false,
    input_receipts: [],
    output_receipt: null,
    evaluated_at: "unavailable",
    valid_until: null,
    gap_codes: ["UPSTREAM_STAGE_INCOMPLETE"],
    measurements: [],
  };
  evidence.outcome = "ABSTAINED";
  evidence.foundation_execution_complete = false;
  evidence.blocking_gap_codes = [...new Set([
    ...changeFoundationGaps,
    ...graphFoundationGaps,
    "GRAPH_CAPTURE_FAILED",
    "UPSTREAM_STAGE_INCOMPLETE",
  ])].sort();
  return await rehashFoundationDocument(evidence as unknown as Record<string, unknown>) as unknown as CandidateFoundationEvidence;
}

async function mockFoundation(page: Page, evidence: CandidateFoundationEvidence) {
  await page.route("**/api/v1/foundation/candidate-evidence", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(evidence) });
  });
}

test("dashboard opens as a clean, truthful assurance workspace", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Change assurance" })).toBeVisible();
  await expect(page.getByLabel("Workspace navigation")).toBeVisible();
  await expect(page.getByRole("region", { name: "Specialist views" })).toBeVisible();
  await expect(page.locator(".capabilityCard")).toHaveCount(6);
  await expect(page.locator(".stageRailEmpty")).toContainText("only when they are recorded");
  await expect(page.locator(".stageState.completed")).toHaveCount(0);
  await expect(page.getByLabel("Requirement or observed change")).toHaveValue("");
  await expect(page.getByLabel("Changed paths optional")).toHaveAttribute("placeholder", "relative/path/to/source");
  await expect(page.getByRole("button", { name: "Analyze change" })).toBeDisabled();
  await expect(page.getByText("CITATIONS ONLY", { exact: true })).toBeVisible();
  await expect(page.getByTestId("semantic-state")).toContainText("NOT PARTICIPATING");
  await expect(page.getByTestId("specialist-state")).toContainText("NOT PARTICIPATING");
  await expect(page.getByLabel("Release posture")).toContainText("NOT EXPOSED");
  await expect(page.getByText("graph connected")).toHaveCount(0);
  await expect(page.getByText("snapshot grounded")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("live campaign panel replays durable status without implying execution or release", async ({ page }) => {
  const gateIds = [
    "SF-L01", "SF-L02", "SF-L03", "SF-L04", "SF-L05", "SF-L06", "SF-L07", "SF-L08", "SF-L09",
    "SF-C01", "SF-C02", "SF-C03", "SF-C04", "SF-C05", "SF-C06",
  ];
  await page.route("**/api/v1/live-campaigns/campaign-browser/status", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({
      campaign_id: "campaign-browser",
      replay_state: "EMPTY",
      validator_replayed: false,
      evidence_state: "INCOMPLETE",
      requirements_satisfied: false,
      release_eligible: false,
      accepted_completion_numerator: 0,
      completion_denominator: 15,
      receipt_count: 0,
      acceptance_profile_sha256: sha("a"),
      ledger_mode: "SQLITE",
      ledger_degradation_code: "POSTGRESQL_UNAVAILABLE",
      locally_valid_gate_ids: [],
      gates: gateIds.map((gate_id) => ({ gate_id, state: "NOT_CURRENT" })),
      quarantined: false,
      stored_quarantine_reasons: [],
      gap_codes: ["CAMPAIGN_RECEIPTS_NOT_FOUND"],
      evaluation_sha256: null,
    }) });
  });
  await page.goto("/");
  const panel = page.getByRole("region", { name: "Salesforce campaign evidence" });
  await panel.getByLabel("Campaign ID").fill("campaign-browser");
  await panel.getByRole("button", { name: "Replay evidence" }).click();
  await expect(panel).toContainText("0/15");
  await expect(panel).toContainText("NOT ELIGIBLE");
  await expect(panel).toContainText("Replays durable receipts only");
});

test("workspace view controls have distinct names and move focus to their destination", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const sourceControl = page.getByRole("button", { name: "Open Source Evidence view" });
  const governanceControl = page.getByRole("button", { name: "Open Governance Review view" });
  await expect(sourceControl).toBeVisible();
  await expect(governanceControl).toBeVisible();
  await sourceControl.click();
  await expect(page.getByRole("heading", { name: "Local candidate foundation" })).toBeFocused();
  await governanceControl.click();
  await expect(page.getByRole("heading", { name: "Assurance evidence" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "governance" })).toHaveAttribute("aria-selected", "true");
});

for (const defect of ["unknown-gate", "inconsistent-validity"] as const) {
  test(`live campaign panel rejects ${defect} without displaying a replay summary`, async ({ page }) => {
    const gates = [
      ...Array.from({ length: 9 }, (_, index) => `SF-L0${index + 1}`),
      ...Array.from({ length: 6 }, (_, index) => `SF-C0${index + 1}`),
    ].map((gate_id) => ({ gate_id, state: "NOT_CURRENT" }));
    if (defect === "unknown-gate") gates[14].gate_id = "SF-L10";
    else gates[0].state = "LOCALLY_VALID";
    await page.route("**/api/v1/live-campaigns/campaign-browser/status", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({
        campaign_id: "campaign-browser", replay_state: "EMPTY", validator_replayed: false,
        evidence_state: "INCOMPLETE", requirements_satisfied: false, release_eligible: false,
        accepted_completion_numerator: 0, completion_denominator: 15, receipt_count: 0,
        acceptance_profile_sha256: sha("a"), ledger_mode: "SQLITE", ledger_degradation_code: null,
        locally_valid_gate_ids: [], gates, quarantined: false, stored_quarantine_reasons: [],
        gap_codes: ["CAMPAIGN_RECEIPTS_NOT_FOUND"], evaluation_sha256: null,
      }) });
    });
    await page.goto("/");
    const panel = page.getByRole("region", { name: "Salesforce campaign evidence" });
    await panel.getByLabel("Campaign ID").fill("campaign-browser");
    await panel.getByRole("button", { name: "Replay evidence" }).click();
    await expect(panel).toContainText("LIVE_STATUS_INVALID");
    await expect(panel.locator(".liveCampaignSummary")).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "Replay evidence" })).toBeEnabled();
  });
}

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

  const recordedStages = page.locator(".stageRail > li");
  await expect(recordedStages).toHaveCount(1);
  await expect(recordedStages.first()).toContainText("Change Analyst");
  await expect(recordedStages.first()).toContainText("change analysis");
  await expect(recordedStages.first()).toContainText("COMPLETED");

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
  await expect(page.getByRole("heading", { name: "Analyze a change" })).toBeVisible();
  await expect(page.getByLabel("Requirement or observed change")).toBeVisible();
  await expect(page.getByLabel("Workspace navigation")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("foundation capture sends an unscoped empty POST and renders only local capture evidence", async ({ page }) => {
  const evidence = await baseFoundation();
  let captures = 0;
  await page.route("**/api/v1/foundation/candidate-evidence", async (route) => {
    captures += 1;
    const request = route.request();
    expect(request.method()).toBe("POST");
    expect(new URL(request.url()).search).toBe("");
    expect(request.postData()).toBeNull();
    const headers = request.headers();
    expect(headers["content-type"]).toBeUndefined();
    expect(headers["x-repository-root"]).toBeUndefined();
    expect(headers["x-change-paths"]).toBeUndefined();
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(evidence) });
  });
  await page.goto("/");
  await expect(page.getByTestId("foundation-interlock")).toContainText("Analysis only");
  await expect(page.getByTestId("foundation-interlock")).toContainText("Evidence incomplete");
  await expect(page.getByTestId("foundation-interlock")).toContainText("Release ineligible");
  await page.getByRole("button", { name: "Capture candidate" }).click();

  const panel = page.getByTestId("foundation-evidence");
  await expect(panel).toContainText("project-renamed-alpha");
  await expect(panel.getByText("EXECUTED", { exact: true })).toHaveCount(4);
  await expect(panel).toContainText("0");
  await expect(panel).toContainText("Not reported");
  await expect(panel).toContainText("Input · graph-production");
  await expect(panel).toContainText("Output · operation-seeds");
  await expect(panel).toContainText("RELEASE_EVIDENCE_MODEL_INCOMPLETE");
  await expect(panel).toContainText("does not evidence a live Salesforce org, deployment, build, test execution, approval, or release readiness");
  await expect(page.getByLabel("Release posture")).toContainText("AWAITING RUN");
  expect(captures).toBe(1);
});

test("valid abstention preserves failed and not-run stages without becoming a transport error", async ({ page }) => {
  await mockFoundation(page, await abstainedFoundation());
  await page.goto("/");
  await page.getByRole("button", { name: "Capture candidate" }).click();

  const panel = page.getByTestId("foundation-evidence");
  await expect(panel).toContainText("ABSTAINED");
  await expect(panel.getByText("FAILED", { exact: true })).toBeVisible();
  await expect(panel.getByText("NOT RUN", { exact: true })).toBeVisible();
  await expect(panel).toContainText("GRAPH_CAPTURE_FAILED");
  await expect(panel).toContainText("UPSTREAM_STAGE_INCOMPLETE");
  await expect(page.getByText("Foundation capture is unavailable.")).toHaveCount(0);
  await expect(page.getByTestId("foundation-interlock")).toContainText("Release ineligible");
});

test("malformed authority response is rejected as a whole and never leaves stale evidence", async ({ page }) => {
  let requestCount = 0;
  const first = await baseFoundation("first-valid-project");
  const second = { ...await baseFoundation("must-not-render"), release_eligible: true, private_path: "must-not-render-path" };
  await page.route("**/api/v1/foundation/candidate-evidence", async (route) => {
    requestCount += 1;
    const payload = requestCount === 1
      ? first
      : second;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Capture candidate" }).click();
  await expect(page.getByText("first-valid-project", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Capture again" }).click();
  await expect(page.getByText("first-valid-project", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Foundation response failed runtime validation.")).toBeVisible();
  await expect(page.getByText("must-not-render", { exact: true })).toHaveCount(0);
  await expect(page.getByText("must-not-render-path", { exact: true })).toHaveCount(0);
});

test("typed unavailability clears prior receipts and cannot disturb an assurance run", async ({ page }) => {
  const run = baseRun();
  run.evidence = [{ evidence_id: "run-evidence-stays", kind: "fact", label: "Independent run evidence", source: "hidden", state: "CONFIRMED", attributes: {} }];
  await mockRun(page, run);
  let requestCount = 0;
  const temporary = await baseFoundation("temporary-foundation");
  await page.route("**/api/v1/foundation/candidate-evidence", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(temporary) });
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 80));
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        type: "FOUNDATION_CAPTURE_PROBLEM",
        code: "FOUNDATION_PIPELINE_UNAVAILABLE",
        authority_scope: "ANALYSIS_ONLY",
        evidence_completeness: "INCOMPLETE",
        release_eligible: false,
        retryable: false,
      }),
    });
  });
  await page.goto("/");
  await submit(page);
  await expect(page.getByText("Independent run evidence", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Capture candidate" }).click();
  await expect(page.getByText("temporary-foundation", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Capture again" }).click();
  await expect(page.getByText("temporary-foundation", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Foundation capture is unavailable.")).toBeVisible();
  await expect(page.getByText("Independent run evidence", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Release posture")).toContainText("INCOMPLETE");
});

test("foundation capture remains keyboard-operable and contained on a mobile viewport", async ({ page }) => {
  await mockFoundation(page, await baseFoundation("mobile-project-gamma"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const button = page.getByRole("button", { name: "Capture candidate" });
  await page.locator("body").click({ position: { x: 1, y: 1 } });
  for (let index = 0; index < 24; index += 1) {
    await page.keyboard.press("Tab");
    if (await button.evaluate((element) => element === document.activeElement)) break;
  }
  await expect(button).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByText("mobile-project-gamma", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Three local foundation stages executed");
  await expect(page.locator(".foundationStage")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("foundation decoder fails closed across authority, ordering, lineage, and bounded-shape mutations", async () => {
  type Document = Record<string, unknown>;
  type StageDocument = Record<string, unknown>;
  const stages = (document: Document) => document.stages as StageDocument[];
  const mutations: Array<[string, (document: Document) => void, boolean?]> = [
    ["stale pipeline policy version", (document) => { document.pipeline_policy_version = "1.0.0"; }],
    ["release authority", (document) => { document.release_eligible = true; }],
    ["unknown field", (document) => { document.private_path = "must-not-be-accepted"; }],
    ["missing global interlock", (document) => {
      for (const stage of stages(document)) {
        stage.gap_codes = (stage.gap_codes as string[]).filter((gap) => gap !== "RELEASE_EVIDENCE_MODEL_INCOMPLETE");
      }
      document.blocking_gap_codes = (document.blocking_gap_codes as string[]).filter((gap) => gap !== "RELEASE_EVIDENCE_MODEL_INCOMPLETE");
    }],
    ["gap union mismatch", (document) => { document.blocking_gap_codes = ["RELEASE_EVIDENCE_MODEL_INCOMPLETE"]; }],
    ["stage order", (document) => { [stages(document)[0], stages(document)[1]] = [stages(document)[1], stages(document)[0]]; }],
    ["outcome mismatch", (document) => { document.outcome = "ABSTAINED"; }],
    ["stage authority", (document) => { stages(document)[1].authority_scope = "RELEASE"; }],
    ["capability substitution", (document) => { stages(document)[1].capability_id = "source.arbitrary-well-formed"; }],
    ["required gap omission", (document) => {
      for (const stage of stages(document)) {
        stage.gap_codes = (stage.gap_codes as string[]).filter((gap) => gap !== "DEPLOYMENT_NOT_ATTESTED");
      }
      document.blocking_gap_codes = (document.blocking_gap_codes as string[]).filter((gap) => gap !== "DEPLOYMENT_NOT_ATTESTED");
    }],
    ["upstream failure followed by execution", (document) => {
      const first = stages(document)[0];
      first.state = "FAILED";
      first.local_foundation_stage_complete = false;
      first.output_receipt = null;
      first.valid_until = null;
      first.measurements = [];
      document.outcome = "ABSTAINED";
      document.foundation_execution_complete = false;
    }],
    ["first stage skipped", (document) => {
      const first = stages(document)[0];
      first.state = "NOT_RUN";
      first.local_foundation_stage_complete = false;
      first.input_receipts = [];
      first.output_receipt = null;
      first.evaluated_at = "unavailable";
      first.valid_until = null;
      first.gap_codes = ["UPSTREAM_STAGE_INCOMPLETE"];
      first.measurements = [];
      for (const later of stages(document).slice(1)) {
        later.state = "NOT_RUN";
        later.local_foundation_stage_complete = false;
        later.input_receipts = [];
        later.output_receipt = null;
        later.evaluated_at = "unavailable";
        later.valid_until = null;
        later.gap_codes = ["UPSTREAM_STAGE_INCOMPLETE"];
        later.measurements = [];
      }
      document.outcome = "ABSTAINED";
      document.foundation_execution_complete = false;
      document.blocking_gap_codes = ["UPSTREAM_STAGE_INCOMPLETE"];
    }],
    ["downstream stage skipped after successful upstream", (document) => {
      for (const later of stages(document).slice(1)) {
        later.state = "NOT_RUN";
        later.local_foundation_stage_complete = false;
        later.input_receipts = [];
        later.output_receipt = null;
        later.evaluated_at = "unavailable";
        later.valid_until = null;
        later.gap_codes = ["UPSTREAM_STAGE_INCOMPLETE"];
        later.measurements = [];
      }
      document.outcome = "ABSTAINED";
      document.foundation_execution_complete = false;
      document.blocking_gap_codes = [...new Set(stages(document).flatMap((stage) => stage.gap_codes as string[]))].sort();
    }],
    ["receipt role", (document) => {
      (stages(document)[1].input_receipts as StageDocument[])[0].role = "arbitrary-input";
    }],
    ["receipt lineage", (document) => {
      (stages(document)[2].input_receipts as StageDocument[])[1].sha256 = sha("0");
    }],
    ["duplicate measurement", (document) => {
      const existing = stages(document)[0].measurements as StageDocument[];
      stages(document)[0].measurements = [...existing, { ...existing[0] }];
    }],
    ["invalid timestamp", (document) => { stages(document)[0].evaluated_at = "not-a-time"; }],
    ["impossible timestamp", (document) => { stages(document)[0].evaluated_at = "2026-02-30T08:00:00Z"; }],
    ["not-run measurement", (document) => {
      const third = stages(document)[2];
      third.state = "NOT_RUN";
      third.local_foundation_stage_complete = false;
      third.input_receipts = [];
      third.output_receipt = null;
      third.evaluated_at = "unavailable";
      third.valid_until = null;
      third.gap_codes = ["UPSTREAM_STAGE_INCOMPLETE"];
      third.measurements = [{ name: "fabricated_count", value: 1 }];
      document.outcome = "ABSTAINED";
      document.foundation_execution_complete = false;
      document.blocking_gap_codes = [...new Set(stages(document).flatMap((stage) => stage.gap_codes as string[]))].sort();
    }],
    ["path-like gap", (document) => {
      stages(document)[0].gap_codes = [...stages(document)[0].gap_codes as string[], "C:/private/path"].sort();
      document.blocking_gap_codes = [...new Set(stages(document).flatMap((stage) => stage.gap_codes as string[]))].sort();
    }],
    ["invalid digest", (document) => { document.chain_sha256 = "not-a-digest"; }, false],
  ];

  await expect(decodeCandidateFoundationEvidence(await baseFoundation())).resolves.toMatchObject({
    authority_scope: "ANALYSIS_ONLY",
    evidence_completeness: "INCOMPLETE",
    release_eligible: false,
  });
  for (const [, mutate, shouldRehash = true] of mutations) {
    const document = structuredClone(await baseFoundation()) as unknown as Document;
    mutate(document);
    if (shouldRehash) await rehashFoundationDocument(document);
    await expect(decodeCandidateFoundationEvidence(document)).rejects.toThrow("FOUNDATION_RESPONSE_INVALID");
  }
});

test("oversized valid JSON is rejected before it can replace prior foundation evidence", async ({ page }) => {
  let requestCount = 0;
  const evidence = await baseFoundation("bounded-response-project");
  await page.route("**/api/v1/foundation/candidate-evidence", async (route) => {
    requestCount += 1;
    await route.fulfill({
      contentType: "application/json",
      body: requestCount === 1 ? JSON.stringify(evidence) : `${JSON.stringify(evidence)}${" ".repeat(33_000)}`,
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Capture candidate" }).click();
  await expect(page.getByText("bounded-response-project", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Capture again" }).click();
  await expect(page.getByText("bounded-response-project", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Foundation response failed runtime validation.")).toBeVisible();
});

test("a bounded client timeout becomes sanitized unavailability", async () => {
  expect(resolveFoundationCaptureTimeout(undefined)).toBe(300_000);
  expect(resolveFoundationCaptureTimeout("120000")).toBe(120_000);
  expect(resolveFoundationCaptureTimeout("7999")).toBe(300_000);
  expect(resolveFoundationCaptureTimeout("not-a-number")).toBe(300_000);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_input, init) => await new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
  });
  try {
    await expect(captureCandidateFoundation("http://unused.invalid", new AbortController().signal, 10))
      .rejects.toMatchObject({ code: "FOUNDATION_CAPTURE_UNAVAILABLE" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("network failure clears a prior projection without exposing transport detail", async ({ page }) => {
  let requestCount = 0;
  const evidence = await baseFoundation("network-stale-project");
  await page.route("**/api/v1/foundation/candidate-evidence", async (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(evidence) });
      return;
    }
    await route.abort("failed");
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Capture candidate" }).click();
  await expect(page.getByText("network-stale-project", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Capture again" }).click();
  await expect(page.getByText("network-stale-project", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Foundation capture is unavailable.")).toBeVisible();
});

test("client-clock expiry only downgrades the projection and requires recapture", async ({ page }) => {
  const browserNow = new Date("2030-01-01T00:00:00.000Z");
  await page.clock.install({ time: browserNow });
  const evidence = await baseFoundation("expiring-project");
  const validUntilMillis = browserNow.getTime() + 60_000;
  const evaluatedMillis = validUntilMillis - 60_000;
  for (const stage of evidence.stages) {
    stage.evaluated_at = new Date(evaluatedMillis).toISOString().replace(".000Z", "Z");
    stage.valid_until = new Date(validUntilMillis).toISOString().replace(".000Z", "Z");
  }
  await rehashFoundationDocument(evidence as unknown as Record<string, unknown>);
  await mockFoundation(page, evidence);
  await page.goto("/");
  await page.getByRole("button", { name: "Capture candidate" }).click();
  await expect(page.getByText("expiring-project", { exact: true })).toBeVisible();
  await page.clock.fastForward(60_001);
  await expect(page.getByText("Captured evidence has expired. Capture again before using it for analysis.")).toBeVisible();
  await expect(page.getByText("expiring-project", { exact: true })).toHaveCount(0);
});
