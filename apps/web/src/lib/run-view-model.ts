import type {
  AgentActivity,
  AnalysisGap,
  AssuranceRun,
  DecisionCode,
  EvidenceRef,
  GovernanceMetric,
  ImpactFinding,
} from "./types";

export type EvidenceLane = "CONFIRMED" | "HUMAN_RECORDED" | "NEEDS_VERIFICATION" | "ADVISORY" | "CONFLICT";
export type ParticipationState = "RECORDED_COMPLETED" | "RECORDED_FAILED" | "RECORDED_ABSTAINED" | "RECORDED_MIXED" | "NOT_PARTICIPATING";

export interface EvidenceCitation {
  evidenceId: string;
  kind: string;
  label: string;
  state: EvidenceRef["state"];
  lane: EvidenceLane;
}

export interface ImpactView {
  finding: ImpactFinding;
  citations: EvidenceCitation[];
  missingEvidenceIds: string[];
  ambiguousEvidenceIds: string[];
}

export interface RunGapView {
  code: string;
  message: string;
  blocking: boolean | null;
  source: "ANALYSIS" | "ACTIVITY" | "VIEW_INTEGRITY";
}

export interface DashboardRunViewModel {
  decisionCode: DecisionCode | "AWAITING_RUN";
  decisionReason: string;
  releaseAuthority: "NOT_EXPOSED";
  evidencePathReceipt: "NOT_EXPOSED";
  citations: Record<EvidenceLane, EvidenceCitation[]>;
  impacts: ImpactView[];
  gaps: RunGapView[];
  hasConflict: boolean;
  semanticParticipation: ParticipationState;
  semanticDetail: string;
  specialistParticipation: ParticipationState;
  specialistDetail: string;
  advisoryCount: number;
  advisoryDetail: string;
  highRiskCount: number;
  confirmedCitationCount: number;
  unverifiedCitationCount: number;
}

const SEMANTIC_CAPABILITY = "reasoning.semantic-retrieval";
const SPECIALIST_CAPABILITY = "reasoning.graph-grounded-agent";

function evidenceLane(state: EvidenceRef["state"]): EvidenceLane {
  if (state === "CONFIRMED") return "CONFIRMED";
  if (state === "HUMAN_CONFIRMED") return "HUMAN_RECORDED";
  if (state === "INFERRED") return "ADVISORY";
  if (state === "CONTRADICTORY") return "CONFLICT";
  return "NEEDS_VERIFICATION";
}

function citation(item: EvidenceRef): EvidenceCitation {
  return {
    evidenceId: item.evidence_id,
    kind: item.kind,
    label: item.label,
    state: item.state,
    lane: evidenceLane(item.state),
  };
}

function gapViews(run: AssuranceRun, duplicateEvidenceIds: string[]): RunGapView[] {
  const analysis = run.analysis_gaps.map((gap: AnalysisGap) => ({
    code: gap.code,
    message: gap.message,
    blocking: gap.blocking,
    source: "ANALYSIS" as const,
  }));
  const activity = run.activities.flatMap((item) =>
    item.gap_codes.map((code) => ({
      code,
      message: `${item.agent} reported this gap during ${item.stage.replaceAll("_", " ")}.`,
      blocking: null,
      source: "ACTIVITY" as const,
    })),
  );
  const integrity = duplicateEvidenceIds.map((evidenceId) => ({
    code: "DUPLICATE_EVIDENCE_ID",
    message: `Citation ${evidenceId} is ambiguous because the run contains that evidence ID more than once.`,
    blocking: null,
    source: "VIEW_INTEGRITY" as const,
  }));
  if (run.status === "COMPLETED" && !run.decision) {
    integrity.push({
      code: "EFFECTIVE_DECISION_MISSING",
      message: "The completed run does not contain an effective release decision.",
      blocking: null,
      source: "VIEW_INTEGRITY",
    });
  }
  const seen = new Set<string>();
  return [...analysis, ...activity, ...integrity].filter((gap) => {
    const key = `${gap.source}:${gap.code}:${gap.message}:${gap.blocking}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function recordedParticipation(activities: AgentActivity[], capability: string, label: string): { state: ParticipationState; detail: string } {
  const matching = activities.filter((item) => item.capability_ids.includes(capability));
  if (!matching.length) {
    return {
      state: "NOT_PARTICIPATING",
      detail: `No ${label} activity is attached to this assurance run.`,
    };
  }
  const statuses = new Set(matching.map((item) => item.status));
  if (statuses.size > 1) {
    return {
      state: "RECORDED_MIXED",
      detail: `The run records mixed ${label} activity outcomes; no current dependency availability is inferred.`,
    };
  }
  const status = matching[0].status;
  return {
    state: `RECORDED_${status}` as ParticipationState,
    detail: `The run records ${status.toLowerCase()} ${label} activity; no current dependency availability is inferred.`,
  };
}

function semanticState(activities: AgentActivity[]): Pick<DashboardRunViewModel, "semanticParticipation" | "semanticDetail"> {
  const recorded = recordedParticipation(activities, SEMANTIC_CAPABILITY, "semantic-retrieval");
  return {
    semanticParticipation: recorded.state,
    semanticDetail: recorded.detail,
  };
}

function specialistState(activities: AgentActivity[]): Pick<DashboardRunViewModel, "specialistParticipation" | "specialistDetail"> {
  const recorded = recordedParticipation(activities, SPECIALIST_CAPABILITY, "graph-grounded specialist");
  return {
    specialistParticipation: recorded.state,
    specialistDetail: recorded.state === "NOT_PARTICIPATING"
      ? "No graph-grounded specialist activity or A6 advisory result is attached to this run."
      : `${recorded.detail} Only proposals present in this run are displayed.`,
  };
}

export function metricValue(metric: GovernanceMetric): number | null {
  return metric.denominator ? metric.numerator / metric.denominator : null;
}

export function buildRunViewModel(run: AssuranceRun | null): DashboardRunViewModel {
  if (!run) {
    return {
      decisionCode: "AWAITING_RUN",
      decisionReason: "Analyze a change to collect an evidence-backed posture.",
      releaseAuthority: "NOT_EXPOSED",
      evidencePathReceipt: "NOT_EXPOSED",
      citations: { CONFIRMED: [], HUMAN_RECORDED: [], NEEDS_VERIFICATION: [], ADVISORY: [], CONFLICT: [] },
      impacts: [],
      gaps: [],
      hasConflict: false,
      semanticParticipation: "NOT_PARTICIPATING",
      semanticDetail: "Semantic retrieval has not participated in a run.",
      specialistParticipation: "NOT_PARTICIPATING",
      specialistDetail: "No specialist activity or advisory result is attached.",
      advisoryCount: 0,
      advisoryDetail: "No inferred evidence or healing proposal is attached.",
      highRiskCount: 0,
      confirmedCitationCount: 0,
      unverifiedCitationCount: 0,
    };
  }

  const citations: DashboardRunViewModel["citations"] = {
    CONFIRMED: [],
    HUMAN_RECORDED: [],
    NEEDS_VERIFICATION: [],
    ADVISORY: [],
    CONFLICT: [],
  };
  const evidenceById = new Map<string, EvidenceCitation>();
  const evidenceIdCounts = new Map<string, number>();
  for (const item of run.evidence) {
    const itemCitation = citation(item);
    citations[itemCitation.lane].push(itemCitation);
    evidenceIdCounts.set(item.evidence_id, (evidenceIdCounts.get(item.evidence_id) ?? 0) + 1);
    if (!evidenceById.has(item.evidence_id)) evidenceById.set(item.evidence_id, itemCitation);
  }
  const duplicateEvidenceIds = [...evidenceIdCounts.entries()].filter(([, count]) => count > 1).map(([id]) => id);
  const duplicateEvidenceIdSet = new Set(duplicateEvidenceIds);

  const impacts = run.impacts.map((finding) => {
    const linked = finding.evidence_ids
      .filter((id) => !duplicateEvidenceIdSet.has(id))
      .map((id) => evidenceById.get(id))
      .filter((item): item is EvidenceCitation => item !== undefined);
    return {
      finding,
      citations: linked,
      missingEvidenceIds: finding.evidence_ids.filter((id) => !evidenceById.has(id)),
      ambiguousEvidenceIds: finding.evidence_ids.filter((id) => duplicateEvidenceIdSet.has(id)),
    };
  });
  const gaps = gapViews(run, duplicateEvidenceIds);
  const semantic = semanticState(run.activities);
  const specialist = specialistState(run.activities);
  const advisoryCount = citations.ADVISORY.length + run.healing_proposals.length;

  return {
    decisionCode: run.decision?.code ?? (run.status === "PENDING" || run.status === "RUNNING" || run.status === "WAITING" ? "AWAITING_RUN" : "INCOMPLETE"),
    decisionReason:
      run.decision?.reasons[0] ??
      (run.status === "FAILED"
        ? "A workflow stage failed before trusted output was available."
        : run.status === "COMPLETED"
          ? "The completed run does not include an effective release decision."
          : "The run has not produced a deterministic release posture."),
    releaseAuthority: "NOT_EXPOSED",
    evidencePathReceipt: "NOT_EXPOSED",
    citations,
    impacts,
    gaps,
    hasConflict: citations.CONFLICT.length > 0,
    ...semantic,
    ...specialist,
    advisoryCount,
    advisoryDetail: advisoryCount
      ? `${advisoryCount} non-authorizing proposal or inferred evidence item${advisoryCount === 1 ? "" : "s"} attached.`
      : "No inferred evidence or healing proposal is attached; no specialist advisory result is exposed by this run contract.",
    highRiskCount: run.impacts.filter((impact) => impact.severity === "HIGH").length,
    confirmedCitationCount: citations.CONFIRMED.length,
    unverifiedCitationCount: citations.NEEDS_VERIFICATION.length,
  };
}
