import type {
  AssuranceRun,
  CandidateAnalysisIdentity,
  CandidateAssuranceView,
  CandidateSideAssuranceView,
  DecisionCode,
} from "./types";

const SHA256 = /^[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PROJECT_ID = /^[a-z0-9][a-z0-9._-]{0,199}$/;
const MAX_VIEW_BYTES = 32_768;
const MAX_RUN_BYTES = 2_000_000;

export class CandidateClientError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "CandidateClientError";
  }
}

function hasExactKeys(item: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(item).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && SHA256.test(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isIdentity(value: unknown): value is CandidateAnalysisIdentity {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return hasExactKeys(item, [
    "project_id", "ontology_id", "ontology_version", "ontology_sha256",
    "source_profile_id", "source_profile_version", "source_profile_sha256",
    "reasoning_policy_version", "reasoning_policy_sha256", "reasoning_eval_set_id",
    "reasoning_eval_set_sha256",
  ]) && typeof item.project_id === "string" && PROJECT_ID.test(item.project_id)
    && [item.ontology_id, item.ontology_version, item.source_profile_id,
      item.source_profile_version, item.reasoning_policy_version,
      item.reasoning_eval_set_id].every(isNonEmptyString)
    && [item.ontology_sha256, item.source_profile_sha256, item.reasoning_policy_sha256,
      item.reasoning_eval_set_sha256].every(isSha256);
}

function isDecision(value: unknown): value is DecisionCode | null {
  return value === null || value === "GO" || value === "CONDITIONAL_GO"
    || value === "NO_GO" || value === "INCOMPLETE";
}

function isSide(value: unknown): value is CandidateSideAssuranceView {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  const side = item.side;
  const operations = item.operation_scope;
  const allowed = side === "BASE" ? ["DELETE", "MODIFY"] : ["ADD", "MODIFY"];
  return hasExactKeys(item, [
    "side", "operation_scope", "changed_path_count", "verified_seed_count", "run_id",
    "trace_id", "status", "decision_code", "source_snapshot", "source_graph_sha256",
    "normalized_graph_sha256", "graph_side_receipt_sha256",
  ]) && (side === "BASE" || side === "CANDIDATE")
    && Array.isArray(operations) && operations.length >= 1 && operations.length <= 2
    && operations.every((operation) => allowed.includes(String(operation)))
    && new Set(operations).size === operations.length
    && allowed.filter((operation) => operations.includes(operation)).join(",") === operations.join(",")
    && Number.isInteger(item.changed_path_count) && Number(item.changed_path_count) >= 1
    && Number(item.changed_path_count) <= 500
    && Number.isInteger(item.verified_seed_count) && Number(item.verified_seed_count) >= 1
    && Number(item.verified_seed_count) <= 500
    && typeof item.run_id === "string" && UUID.test(item.run_id)
    && typeof item.trace_id === "string" && UUID.test(item.trace_id)
    && ["PENDING", "RUNNING", "WAITING", "COMPLETED", "FAILED"].includes(String(item.status))
    && isDecision(item.decision_code)
    && [item.source_snapshot, item.source_graph_sha256, item.normalized_graph_sha256,
      item.graph_side_receipt_sha256].every(isSha256);
}

export function decodeCandidateAssuranceView(value: unknown): CandidateAssuranceView {
  if (!value || typeof value !== "object") throw new CandidateClientError("INVALID_RESPONSE");
  const item = value as Record<string, unknown>;
  const analyses = item.analyses;
  const identity = item.analysis_identity;
  if (!hasExactKeys(item, [
    "schema_version", "authority_scope", "release_eligible", "evidence_completeness",
    "project_id", "verified_change_manifest_sha256", "graph_production_receipt_sha256",
    "operation_seed_artifact_sha256", "analysis_identity", "analyses", "blocking_gap_codes",
    "checkpoint_commit_scope", "persistence_gap_codes", "bundle_sha256", "view_sha256",
  ]) || item.schema_version !== "1.0.0" || item.authority_scope !== "ANALYSIS_ONLY"
    || item.release_eligible !== false || item.evidence_completeness !== "INCOMPLETE"
    || typeof item.project_id !== "string" || !PROJECT_ID.test(item.project_id)
    || !isIdentity(identity) || identity.project_id !== item.project_id
    || !Array.isArray(analyses) || analyses.length < 1 || analyses.length > 2
    || !analyses.every(isSide)
    || analyses.map((analysis) => analysis.side).join(",")
      !== [...analyses].map((analysis) => analysis.side).sort((a, b) => a === b ? 0 : a === "BASE" ? -1 : 1).join(",")
    || new Set(analyses.map((analysis) => analysis.side)).size !== analyses.length
    || !Array.isArray(item.blocking_gap_codes) || item.blocking_gap_codes.length === 0
    || !item.blocking_gap_codes.every(isNonEmptyString)
    || new Set(item.blocking_gap_codes).size !== item.blocking_gap_codes.length
    || item.checkpoint_commit_scope !== "EXCLUDED_FROM_BUNDLE_TRANSACTION"
    || !Array.isArray(item.persistence_gap_codes)
    || item.persistence_gap_codes.length !== 1
    || item.persistence_gap_codes[0] !== "CHECKPOINT_NOT_ATOMIC_WITH_CANDIDATE_BUNDLE"
    || ![item.verified_change_manifest_sha256, item.graph_production_receipt_sha256,
      item.operation_seed_artifact_sha256, item.bundle_sha256, item.view_sha256].every(isSha256)) {
    throw new CandidateClientError("INVALID_RESPONSE");
  }
  return value as CandidateAssuranceView;
}

function decodeCandidateRun(value: unknown, side: CandidateSideAssuranceView,
  identity: CandidateAnalysisIdentity): AssuranceRun {
  if (!value || typeof value !== "object") throw new CandidateClientError("INVALID_RUN_RESPONSE");
  const run = value as Record<string, unknown>;
  const request = run.request as Record<string, unknown> | undefined;
  const decision = run.decision as Record<string, unknown> | null | undefined;
  if (run.run_id !== side.run_id || run.trace_id !== side.trace_id || run.status !== side.status
    || run.source_snapshot !== side.source_snapshot
    || run.source_graph_sha256 !== side.source_graph_sha256
    || run.normalized_graph_sha256 !== side.normalized_graph_sha256
    || run.ontology_id !== identity.ontology_id || run.ontology_version !== identity.ontology_version
    || run.ontology_sha256 !== identity.ontology_sha256
    || run.source_profile_id !== identity.source_profile_id
    || run.source_profile_version !== identity.source_profile_version
    || run.source_profile_sha256 !== identity.source_profile_sha256
    || run.reasoning_policy_version !== identity.reasoning_policy_version
    || run.reasoning_policy_sha256 !== identity.reasoning_policy_sha256
    || run.reasoning_eval_set_id !== identity.reasoning_eval_set_id
    || run.reasoning_eval_set_sha256 !== identity.reasoning_eval_set_sha256
    || request?.project_id !== identity.project_id || request.change_intent !== "VERIFIED_CHANGE"
    || !Array.isArray(run.evidence) || !Array.isArray(run.impacts)
    || !Array.isArray(run.selected_tests) || !Array.isArray(run.test_results)
    || !Array.isArray(run.analysis_gaps) || !Array.isArray(run.activities)
    || !Array.isArray(run.deliverables) || !Array.isArray(run.claims)
    || (decision?.code ?? null) !== side.decision_code) {
    throw new CandidateClientError("INVALID_RUN_RESPONSE");
  }
  return value as AssuranceRun;
}

async function readBoundedJson(response: Response, maximumBytes: number): Promise<unknown> {
  const declared = response.headers.get("content-length");
  if (declared !== null && Number(declared) > maximumBytes) {
    throw new CandidateClientError("RESPONSE_TOO_LARGE");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > maximumBytes) {
    throw new CandidateClientError("RESPONSE_TOO_LARGE");
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new CandidateClientError("INVALID_RESPONSE");
  }
}

export async function getCandidateRun(apiBase: string, view: CandidateAssuranceView,
  side: CandidateSideAssuranceView): Promise<AssuranceRun> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}/api/v1/assurance-runs/${encodeURIComponent(side.run_id)}`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new CandidateClientError("CANDIDATE_RUN_API_UNAVAILABLE");
  }
  if (!response.ok) throw new CandidateClientError(`CANDIDATE_RUN_${response.status}`);
  return decodeCandidateRun(await readBoundedJson(response, MAX_RUN_BYTES), side, view.analysis_identity);
}

export async function analyzeCurrentCandidate(apiBase: string): Promise<{
  view: CandidateAssuranceView;
  run: AssuranceRun;
}> {
  let response: Response;
  try {
    response = await fetch(`${apiBase}/api/v1/assurance-runs/analyze-current-candidate`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new CandidateClientError("CANDIDATE_API_UNAVAILABLE");
  }
  if (!response.ok) throw new CandidateClientError(`CANDIDATE_ANALYSIS_${response.status}`);
  const view = decodeCandidateAssuranceView(await readBoundedJson(response, MAX_VIEW_BYTES));
  const preferred = view.analyses.find((item) => item.side === "CANDIDATE") ?? view.analyses[0];
  return { view, run: await getCandidateRun(apiBase, view, preferred) };
}
