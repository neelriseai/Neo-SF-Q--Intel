export type DecisionCode = "GO" | "CONDITIONAL_GO" | "NO_GO" | "INCOMPLETE";

export interface EvidenceRef {
  evidence_id: string;
  kind: string;
  label: string;
  source: string;
  state: "CONFIRMED" | "UNVERIFIED" | "HUMAN_CONFIRMED" | "INFERRED" | "CONTRADICTORY" | "STALE" | "REJECTED";
  attributes: Record<string, unknown>;
}

export interface ImpactFinding {
  entity_id: string;
  label: string;
  kind: string;
  relation: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  evidence_strength: number;
  strength_basis: string;
  evidence_ids: string[];
}

export interface AnalysisGap {
  code: string;
  message: string;
  entity_id: string | null;
  relation: string | null;
  blocking: boolean;
}

export interface AgentActivity {
  activity_id: string;
  capability_ids: string[];
  agent: string;
  stage: string;
  status: "COMPLETED" | "ABSTAINED" | "FAILED";
  started_at: string;
  completed_at: string;
  duration_ms: number;
  summary: string;
  input_evidence_ids: string[];
  output_artifact_ids: string[];
  policy_refs: string[];
  gap_codes: string[];
  error_class: string | null;
}

export interface GovernanceMetric {
  metric: string;
  numerator: number;
  denominator: number;
  target: number;
  comparator: "AT_LEAST" | "AT_MOST";
  minimum_sample_size: number;
  status: "PASSED" | "FAILED" | "NOT_APPLICABLE" | "INSUFFICIENT_SAMPLE";
  blocking: boolean;
}

export interface GuardrailDecision {
  control_id: string;
  control_version: string;
  stage: "INPUT" | "POST_ANALYSIS" | "PRE_TOOL" | "POST_TOOL" | "RELEASE";
  outcome: "ALLOW" | "DENY" | "REQUIRE_APPROVAL" | "ABSTAIN" | "REVIEW_REQUIRED" | "NOT_APPLICABLE";
  reason_code: string;
  evidence_ids: string[];
  observations: string[];
  blocking: boolean;
}

export interface ReleaseDecision {
  code: DecisionCode;
  reasons: string[];
  evidence_ids: string[];
}

export interface AssuranceRun {
  schema_version: "1.0.0" | "2.0.0";
  run_id: string;
  trace_id: string;
  created_at: string;
  reasoning_policy_version: string;
  reasoning_policy_sha256: string;
  reasoning_eval_set_id: string;
  reasoning_eval_set_sha256: string;
  source_snapshot: string | null;
  source_graph_sha256: string | null;
  ontology_id: string | null;
  ontology_version: string | null;
  ontology_sha256: string | null;
  source_profile_id: string | null;
  source_profile_version: string | null;
  source_profile_sha256: string | null;
  normalized_graph_sha256: string | null;
  status: "PENDING" | "RUNNING" | "WAITING" | "COMPLETED" | "FAILED";
  request: { requirement: string; changed_paths: string[]; change_intent: "INFORMATIONAL" | "PLANNED_CHANGE" | "OBSERVED_CHANGE"; source_ref: string };
  evidence: EvidenceRef[];
  impacts: ImpactFinding[];
  selected_tests: Array<{ test_id: string; label: string; classification: "MANDATORY" | "RECOMMENDED"; reason: string; evidence_ids: string[] }>;
  test_results: Array<{ test_id: string; outcome: "PASSED" | "FAILED" | "INCONCLUSIVE"; runner_id: string; result_sha256: string; source_snapshot: string; executed_at: string; valid_until: string; evidence_ids: string[] }>;
  analysis_gaps: AnalysisGap[];
  healing_proposals: Array<{ target_id: string; strategy: string; rationale: string; ranking_score: number; score_basis: string; evidence_ids: string[]; requires_human_approval: boolean }>;
  activities: AgentActivity[];
  deliverables: Array<{ agent: string; capability_ids: string[]; status: string; conclusion: string; evidence_ids: string[]; assumptions: string[]; blocking_gaps: string[]; nonblocking_gaps: string[]; measurements: Record<string, string | number>; next_permitted_action: string }>;
  claims: Array<{ claim_id: string; text: string; material: boolean; evidence_ids: string[]; supported: boolean }>;
  governance: { policy_version: string; policy_sha256: string; analysis_input_sha256: string; metrics: GovernanceMetric[]; guardrails: GuardrailDecision[]; violations: string[]; passed: boolean } | null;
  decision: ReleaseDecision | null;
  recorded_decision: ReleaseDecision | null;
}
