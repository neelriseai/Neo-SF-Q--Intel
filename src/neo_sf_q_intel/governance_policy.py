from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from neo_sf_q_intel.domain import (
    DecisionCode,
    GuardrailOutcome,
    GuardrailStage,
    MetricComparator,
    StrictModel,
)

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "governance-policy.json"


class MetricGate(StrEnum):
    BLOCK = "BLOCK"
    OBSERVE = "OBSERVE"


class ZeroDenominatorAction(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAIL = "FAIL"


class MetricEvaluator(StrEnum):
    MATERIAL_CLAIM_EVIDENCE_COVERAGE = "material_claim_evidence_coverage"
    IMPACT_EVIDENCE_COVERAGE = "impact_evidence_coverage"
    SELECTED_TEST_EVIDENCE_COVERAGE = "selected_test_evidence_coverage"


class ControlEvaluator(StrEnum):
    CONFIRMED_EVIDENCE_PRESENT = "confirmed_evidence_present"
    NO_BLOCKING_ANALYSIS_GAPS = "no_blocking_analysis_gaps"
    METRIC_THRESHOLD = "metric_threshold"


class MetricDefinition(StrictModel):
    metric: MetricEvaluator
    description: str = Field(min_length=20)
    numerator_definition: str = Field(min_length=20)
    denominator_definition: str = Field(min_length=20)
    comparator: MetricComparator
    target: float = Field(ge=0.0, le=1.0)
    minimum_sample_size: int = Field(ge=1)
    zero_denominator: ZeroDenominatorAction
    gate: MetricGate


class GuardrailDefinition(StrictModel):
    control_id: str = Field(min_length=3)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    stage: GuardrailStage
    evaluator: ControlEvaluator
    metric: MetricEvaluator | None = None
    failure_outcome: GuardrailOutcome
    failure_reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    blocking: bool

    @model_validator(mode="after")
    def validate_metric_reference(self) -> GuardrailDefinition:
        if self.evaluator is ControlEvaluator.METRIC_THRESHOLD and self.metric is None:
            raise ValueError("metric_threshold controls require a metric")
        if self.evaluator is not ControlEvaluator.METRIC_THRESHOLD and self.metric is not None:
            raise ValueError("direct controls cannot reference a metric")
        if self.failure_outcome in {GuardrailOutcome.ALLOW, GuardrailOutcome.NOT_APPLICABLE}:
            raise ValueError("failure_outcome must represent a controlled failure")
        return self


class TrustedRunner(StrictModel):
    runner_id: str = Field(min_length=3, max_length=200)
    evidence_source: str = Field(min_length=3, max_length=500)
    maximum_clock_skew_seconds: int = Field(ge=0, le=300)
    maximum_result_age_seconds: int = Field(ge=1, le=604800)


class ReleaseAuthority(StrictModel):
    enabled: bool
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    evidence_model_requirements: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def prevent_unreviewed_enablement(self) -> ReleaseAuthority:
        if self.enabled:
            raise ValueError(
                "release authority cannot be enabled until the graph evidence model "
                "has an independently verified enablement contract"
            )
        return self


class GovernancePolicy(StrictModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    release_authority: ReleaseAuthority
    trusted_runners: list[TrustedRunner] = Field(min_length=1)
    metrics: list[MetricDefinition] = Field(min_length=1)
    controls: list[GuardrailDefinition] = Field(min_length=1)
    decision_mapping: dict[GuardrailOutcome, DecisionCode]

    @model_validator(mode="after")
    def validate_registry(self) -> GovernancePolicy:
        metric_ids = [item.metric for item in self.metrics]
        control_ids = [item.control_id for item in self.controls]
        runner_ids = [item.runner_id for item in self.trusted_runners]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metric identifiers must be unique")
        if len(control_ids) != len(set(control_ids)):
            raise ValueError("control identifiers must be unique")
        if len(runner_ids) != len(set(runner_ids)):
            raise ValueError("trusted runner identifiers must be unique")
        declared = set(metric_ids)
        referenced = {item.metric for item in self.controls if item.metric is not None}
        undeclared = referenced - declared
        if undeclared:
            raise ValueError(
                "controls reference undeclared metrics: " + ", ".join(sorted(undeclared))
            )
        blocking_references = {
            item.metric for item in self.controls if item.metric is not None and item.blocking
        }
        missing = {
            item.metric for item in self.metrics if item.gate is MetricGate.BLOCK
        } - blocking_references
        if missing:
            raise ValueError(
                "blocking metrics require a metric_threshold control: " + ", ".join(sorted(missing))
            )
        required_outcomes = {
            GuardrailOutcome.DENY,
            GuardrailOutcome.ABSTAIN,
            GuardrailOutcome.REVIEW_REQUIRED,
            GuardrailOutcome.REQUIRE_APPROVAL,
        }
        missing_mappings = required_outcomes - set(self.decision_mapping)
        if missing_mappings:
            raise ValueError(
                "decision_mapping is incomplete: " + ", ".join(sorted(missing_mappings))
            )
        safe_mapping = {
            GuardrailOutcome.DENY: DecisionCode.NO_GO,
            GuardrailOutcome.ABSTAIN: DecisionCode.INCOMPLETE,
            GuardrailOutcome.REVIEW_REQUIRED: DecisionCode.INCOMPLETE,
            GuardrailOutcome.REQUIRE_APPROVAL: DecisionCode.CONDITIONAL_GO,
        }
        if any(
            self.decision_mapping.get(outcome) is not expected
            for outcome, expected in safe_mapping.items()
        ):
            raise ValueError("decision_mapping cannot weaken blocking outcomes")
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> GovernancePolicy:
        raw = json.loads((path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))
        return cls.model_validate(raw)

    @property
    def metric_by_id(self) -> dict[MetricEvaluator, MetricDefinition]:
        return {item.metric: item for item in self.metrics}

    @property
    def trusted_runner_by_id(self) -> dict[str, TrustedRunner]:
        return {item.runner_id: item for item in self.trusted_runners}

    @property
    def sha256(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(canonical).hexdigest()
