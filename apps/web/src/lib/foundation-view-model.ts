import type {
  CandidateFoundationEvidence,
  FoundationReceiptReference,
  FoundationStageEvidence,
} from "./foundation-contract";

export interface FoundationStageView {
  stage: FoundationStageEvidence;
  label: string;
  evaluatedLabel: string;
  validityLabel: string;
  inputReceipts: FoundationReceiptReference[];
}

export interface FoundationViewModel {
  projectId: string;
  outcome: CandidateFoundationEvidence["outcome"];
  stages: FoundationStageView[];
  blockingGaps: string[];
  earliestExpiry: number | null;
  chainSha256: string;
}

function words(value: string): string {
  return value.replaceAll("_", " ").toLowerCase();
}

function timestamp(value: string): string {
  if (value === "unavailable") return "Not reported";
  return new Date(value).toLocaleString();
}

export function foundationWords(value: string): string {
  return words(value);
}

export function buildFoundationViewModel(evidence: CandidateFoundationEvidence): FoundationViewModel {
  const expiries = evidence.stages
    .map((stage) => stage.valid_until ? Date.parse(stage.valid_until) : Number.NaN)
    .filter(Number.isFinite);
  return {
    projectId: evidence.project_id,
    outcome: evidence.outcome,
    stages: evidence.stages.map((stage) => ({
      stage,
      label: words(stage.stage),
      evaluatedLabel: stage.evaluated_at === "unavailable"
        ? "Capture time not reported"
        : `Evaluated at ${timestamp(stage.evaluated_at)}`,
      validityLabel: stage.valid_until
        ? `Valid until ${timestamp(stage.valid_until)}`
        : "Validity window not reported",
      inputReceipts: stage.input_receipts,
    })),
    blockingGaps: evidence.blocking_gap_codes,
    earliestExpiry: expiries.length ? Math.min(...expiries) : null,
    chainSha256: evidence.chain_sha256,
  };
}
