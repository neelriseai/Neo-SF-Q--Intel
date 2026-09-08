from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceState(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNVERIFIED = "UNVERIFIED"
    INFERRED = "INFERRED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    CONTRADICTORY = "CONTRADICTORY"
    STALE = "STALE"
    REJECTED = "REJECTED"


class DecisionCode(StrEnum):
    GO = "GO"
    CONDITIONAL_GO = "CONDITIONAL_GO"
    NO_GO = "NO_GO"
    INCOMPLETE = "INCOMPLETE"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    ABSTAINED = "ABSTAINED"
    FAILED = "FAILED"


class MetricComparator(StrEnum):
    AT_LEAST = "AT_LEAST"
    AT_MOST = "AT_MOST"


class MetricStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class GuardrailOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    ABSTAIN = "ABSTAIN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GuardrailStage(StrEnum):
    INPUT = "INPUT"
    POST_ANALYSIS = "POST_ANALYSIS"
    PRE_TOOL = "PRE_TOOL"
    POST_TOOL = "POST_TOOL"
    RELEASE = "RELEASE"


class ChangeIntent(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    PLANNED_CHANGE = "PLANNED_CHANGE"
    OBSERVED_CHANGE = "OBSERVED_CHANGE"


class TestOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class TestClassification(StrEnum):
    MANDATORY = "MANDATORY"
    RECOMMENDED = "RECOMMENDED"


class RiskSeverity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ChangeRequest(StrictModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    changed_paths: list[str] = Field(default_factory=list, max_length=500)
    change_intent: ChangeIntent = ChangeIntent.INFORMATIONAL
    project_id: str | None = Field(default=None, min_length=1, max_length=200)
    source_ref: str = "working-tree"


class EvidenceRef(StrictModel):
    evidence_id: str
    kind: str
    label: str
    source: str
    state: EvidenceState
    attributes: dict[str, Any] = Field(default_factory=dict)


class ImpactFinding(StrictModel):
    entity_id: str
    label: str
    kind: str
    relation: str
    severity: RiskSeverity
    evidence_strength: float = Field(ge=0.0, le=1.0)
    strength_basis: str = Field(min_length=3)
    evidence_ids: list[str] = Field(min_length=1)


class TestSelection(StrictModel):
    test_id: str
    label: str
    classification: TestClassification
    reason: str
    evidence_ids: list[str] = Field(min_length=1)


class TestExecution(StrictModel):
    test_id: str
    outcome: TestOutcome
    runner_id: str = Field(min_length=3, max_length=200)
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot: str = Field(min_length=1, max_length=500)
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    valid_until: datetime
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_times(self) -> TestExecution:
        if self.executed_at.tzinfo is None:
            raise ValueError("executed_at must include a timezone")
        if self.valid_until.tzinfo is None:
            raise ValueError("valid_until must include a timezone")
        if self.valid_until <= self.executed_at:
            raise ValueError("valid_until must be later than executed_at")
        return self


class AnalysisGap(StrictModel):
    code: str
    message: str
    entity_id: str | None = None
    relation: str | None = None
    blocking: bool = True


class HealingProposal(StrictModel):
    target_id: str
    strategy: str
    rationale: str
    ranking_score: float = Field(ge=0.0, le=1.0)
    score_basis: str = Field(min_length=3)
    evidence_ids: list[str] = Field(min_length=1)
    requires_human_approval: bool = True


BoundedAuditText = Annotated[str, Field(min_length=1, max_length=500)]


class AgentActivity(StrictModel):
    activity_id: UUID = Field(default_factory=uuid4)
    capability_ids: list[BoundedAuditText] = Field(min_length=1, max_length=20)
    agent: BoundedAuditText
    stage: BoundedAuditText
    status: AgentStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=2_000)
    input_evidence_ids: list[BoundedAuditText] = Field(default_factory=list, max_length=100)
    output_artifact_ids: list[BoundedAuditText] = Field(default_factory=list, max_length=100)
    policy_refs: list[BoundedAuditText] = Field(default_factory=list, max_length=20)
    gap_codes: list[BoundedAuditText] = Field(default_factory=list, max_length=100)
    error_class: BoundedAuditText | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> AgentActivity:
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("activity timestamps must include a timezone")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self


class AgentDeliverable(StrictModel):
    agent: str
    capability_ids: list[str] = Field(min_length=1)
    status: AgentStatus
    conclusion: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    blocking_gaps: list[str] = Field(default_factory=list)
    nonblocking_gaps: list[str] = Field(default_factory=list)
    measurements: dict[str, int | float | str] = Field(default_factory=dict)
    next_permitted_action: str = Field(min_length=1, max_length=1_000)


class Claim(StrictModel):
    claim_id: str
    text: str
    material: bool = True
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool = False


class GovernanceMetric(StrictModel):
    metric: str
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    target: float = Field(ge=0.0, le=1.0)
    comparator: MetricComparator
    minimum_sample_size: int = Field(ge=1)
    status: MetricStatus
    blocking: bool

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def passed(self) -> bool:
        return self.status is MetricStatus.PASSED


class GuardrailDecision(StrictModel):
    control_id: str
    control_version: str
    stage: GuardrailStage
    outcome: GuardrailOutcome
    reason_code: str
    evidence_ids: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    blocking: bool


class GovernanceAssessment(StrictModel):
    policy_version: str
    policy_sha256: str
    analysis_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    metrics: list[GovernanceMetric]
    guardrails: list[GuardrailDecision]
    violations: list[str] = Field(default_factory=list)
    passed: bool


class ReleaseDecision(StrictModel):
    code: DecisionCode
    reasons: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class AssuranceRun(StrictModel):
    schema_version: Literal["1.0.0", "2.0.0"] = "2.0.0"
    run_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reasoning_policy_version: str
    reasoning_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_eval_set_id: str
    reasoning_eval_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot: str | None = None
    source_graph_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    ontology_id: str | None = None
    ontology_version: str | None = None
    ontology_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_profile_id: str | None = None
    source_profile_version: str | None = None
    source_profile_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: RunStatus = RunStatus.PENDING
    request: ChangeRequest
    evidence: list[EvidenceRef] = Field(default_factory=list)
    impacts: list[ImpactFinding] = Field(default_factory=list)
    selected_tests: list[TestSelection] = Field(default_factory=list)
    test_results: list[TestExecution] = Field(default_factory=list)
    analysis_gaps: list[AnalysisGap] = Field(default_factory=list)
    healing_proposals: list[HealingProposal] = Field(default_factory=list)
    activities: list[AgentActivity] = Field(default_factory=list)
    deliverables: list[AgentDeliverable] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    governance: GovernanceAssessment | None = None
    decision: ReleaseDecision | None = None
    recorded_decision: ReleaseDecision | None = None

    @model_validator(mode="after")
    def require_current_graph_identity(self) -> AssuranceRun:
        if self.schema_version == "1.0.0":
            return self
        required = {
            "source_snapshot": self.source_snapshot,
            "source_graph_sha256": self.source_graph_sha256,
            "ontology_id": self.ontology_id,
            "ontology_version": self.ontology_version,
            "ontology_sha256": self.ontology_sha256,
            "source_profile_id": self.source_profile_id,
            "source_profile_version": self.source_profile_version,
            "source_profile_sha256": self.source_profile_sha256,
            "normalized_graph_sha256": self.normalized_graph_sha256,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("Current assurance run lacks graph identity: " + ", ".join(missing))
        return self
