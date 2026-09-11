import { expect, test } from "@playwright/test";
import {
  analyzeCurrentCandidate,
  decodeCandidateAssuranceView,
  getCandidateRun,
} from "../../../apps/web/src/lib/candidate-client.js";

const digest = "a".repeat(64);
const runId = "123e4567-e89b-42d3-a456-426614174000";
const traceId = "123e4567-e89b-42d3-b456-426614174001";

function identity() {
  return {
    project_id: "fixture-project",
    ontology_id: "canonical-ontology",
    ontology_version: "1.0.0",
    ontology_sha256: digest,
    source_profile_id: "source-profile",
    source_profile_version: "1.0.0",
    source_profile_sha256: digest,
    reasoning_policy_version: "1.0.0",
    reasoning_policy_sha256: digest,
    reasoning_eval_set_id: "retrieval-eval",
    reasoning_eval_set_sha256: digest,
  };
}

function view() {
  return {
    schema_version: "1.0.0",
    authority_scope: "ANALYSIS_ONLY",
    release_eligible: false,
    evidence_completeness: "INCOMPLETE",
    project_id: "fixture-project",
    verified_change_manifest_sha256: digest,
    graph_production_receipt_sha256: digest,
    operation_seed_artifact_sha256: digest,
    analysis_identity: identity(),
    analyses: [
      {
        side: "CANDIDATE",
        operation_scope: ["MODIFY"],
        changed_path_count: 1,
        verified_seed_count: 1,
        run_id: runId,
        trace_id: traceId,
        status: "COMPLETED",
        decision_code: "INCOMPLETE",
        source_snapshot: digest,
        source_graph_sha256: digest,
        normalized_graph_sha256: digest,
        graph_side_receipt_sha256: digest,
      },
    ],
    blocking_gap_codes: [
      "CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE",
      "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
    ],
    checkpoint_commit_scope: "EXCLUDED_FROM_BUNDLE_TRANSACTION",
    persistence_gap_codes: ["CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE"],
    bundle_sha256: digest,
    view_sha256: digest,
  };
}

function run() {
  return {
    schema_version: "2.0.0",
    run_id: runId,
    trace_id: traceId,
    created_at: "2026-09-10T00:00:00Z",
    status: "COMPLETED",
    source_snapshot: digest,
    source_graph_sha256: digest,
    normalized_graph_sha256: digest,
    ...identity(),
    request: {
      requirement: "Analyze candidate",
      changed_paths: ["relative/path.cls"],
      change_intent: "VERIFIED_CHANGE",
      project_id: "fixture-project",
      source_ref: "verified-candidate",
    },
    evidence: [], impacts: [], selected_tests: [], test_results: [], analysis_gaps: [],
    healing_proposals: [], activities: [], deliverables: [], claims: [], governance: null,
    decision: { code: "INCOMPLETE", reasons: ["Incomplete"], evidence_ids: [] },
    recorded_decision: null,
  };
}

test("candidate view decoder keeps bounded authority and identity boundaries", () => {
  const decoded = decodeCandidateAssuranceView(view());
  expect(decoded.authority_scope).toBe("ANALYSIS_ONLY");
  expect(decoded.release_eligible).toBe(false);
  expect(decoded.analyses[0].side).toBe("CANDIDATE");
  expect(decoded.analyses[0]).not.toHaveProperty("run");
});

test("candidate view decoder rejects authority, extra artifacts, and side ambiguity", () => {
  expect(() => decodeCandidateAssuranceView({ ...view(), release_eligible: true })).toThrow();
  expect(() => decodeCandidateAssuranceView({ ...view(), graph_production_artifact: {} })).toThrow();
  const duplicate = view();
  duplicate.analyses.push({ ...duplicate.analyses[0] });
  expect(() => decodeCandidateAssuranceView(duplicate)).toThrow();
});

test("candidate client sends an empty host-owned POST then fetches its bound run", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ input: string; body: BodyInit | null | undefined }> = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ input: String(input), body: init?.body });
    const payload = calls.length === 1 ? view() : run();
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  try {
    const result = await analyzeCurrentCandidate("http://local.invalid");
    expect(calls[0].body).toBeUndefined();
    expect(calls[1].input).toContain(runId);
    expect(result.view.analyses).toHaveLength(1);
    expect(result.run.run_id).toBe(runId);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("candidate run decoder rejects a run spliced from another identity", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ...run(), ontology_sha256: "b".repeat(64) }));
  try {
    const decoded = decodeCandidateAssuranceView(view());
    await expect(getCandidateRun("http://local.invalid", decoded, decoded.analyses[0]))
      .rejects.toThrow("INVALID_RUN_RESPONSE");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
