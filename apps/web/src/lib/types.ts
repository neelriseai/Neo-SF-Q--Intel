export type DecisionCode = "GO" | "CONDITIONAL_GO" | "NO_GO" | "INCOMPLETE";

export interface EvidenceRef {
  evidence_id: string;
  kind: string;
  label: string;
  source: string;
  confidence: number;
}

export interface ImpactFinding {
  entity_id: string;
  label: string;
  kind: string;
  relation: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  confidence: number;
  evidence_ids: string[];
}

export interface AgentActivity {
  agent: string;
  status: "COMPLETED" | "ABSTAINED" | "FAILED";
  summary: string;
  evidence_ids: string[];
}

export interface GovernanceMetric {
  metric: string;
  numerator: number;
  denominator: number;
  target: number;
}

export interface AssuranceRun {
  run_id: string;
  trace_id: string;
  created_at: string;
  status: string;
  request: { requirement: string; changed_paths: string[]; source_ref: string };
  evidence: EvidenceRef[];
  impacts: ImpactFinding[];
  selected_tests: Array<{ test_id: string; label: string; classification: string }>;
  healing_proposals: Array<{ target_id: string; strategy: string; confidence: number }>;
  activities: AgentActivity[];
  governance: { metrics: GovernanceMetric[]; violations: string[]; passed: boolean } | null;
  decision: { code: DecisionCode; reasons: string[]; evidence_ids: string[] } | null;
}
