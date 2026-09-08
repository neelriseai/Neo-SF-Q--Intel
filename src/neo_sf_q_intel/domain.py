from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceState(StrEnum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
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


class ChangeRequest(StrictModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    changed_paths: list[str] = Field(default_factory=list, max_length=500)
    project_id: str = "strategic-deal-assurance"
    source_ref: str = "working-tree"


class EvidenceRef(StrictModel):
    evidence_id: str
    kind: str
    label: str
    source: str
    state: EvidenceState = EvidenceState.CONFIRMED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class ImpactFinding(StrictModel):
    entity_id: str
    label: str
    kind: str
    relation: str
    severity: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)


class TestSelection(StrictModel):
    test_id: str
    label: str
    classification: str
    reason: str
    evidence_ids: list[str] = Field(min_length=1)


class HealingProposal(StrictModel):
    target_id: str
    strategy: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(min_length=1)
    requires_human_approval: bool = True


class AgentActivity(StrictModel):
    agent: str
    status: AgentStatus
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)


class Claim(StrictModel):
    claim_id: str
    text: str
    material: bool = True
    evidence_ids: list[str] = Field(default_factory=list)
    supported: bool = False


class GovernanceMetric(StrictModel):
    metric: str
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    target: float = Field(ge=0.0, le=1.0)

    @property
    def value(self) -> float:
        return self.numerator / self.denominator

    @property
    def passed(self) -> bool:
        return self.value >= self.target


class GovernanceAssessment(StrictModel):
    metrics: list[GovernanceMetric]
    violations: list[str] = Field(default_factory=list)
    passed: bool


class ReleaseDecision(StrictModel):
    code: DecisionCode
    reasons: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class AssuranceRun(StrictModel):
    run_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: RunStatus = RunStatus.PENDING
    request: ChangeRequest
    evidence: list[EvidenceRef] = Field(default_factory=list)
    impacts: list[ImpactFinding] = Field(default_factory=list)
    selected_tests: list[TestSelection] = Field(default_factory=list)
    healing_proposals: list[HealingProposal] = Field(default_factory=list)
    activities: list[AgentActivity] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    governance: GovernanceAssessment | None = None
    decision: ReleaseDecision | None = None
