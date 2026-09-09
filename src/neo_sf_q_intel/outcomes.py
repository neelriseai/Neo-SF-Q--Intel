"""Append-only, non-authorizing outcome-memory contracts.

Outcome records are historical candidates for later retrieval and evaluation.  They
cannot confirm graph facts, satisfy release evidence, change governance, or approve
tools.  This foundation derives every authority-relevant lineage field from an
originating assurance run. A6 advisory linkage is intentionally deferred until
the complete A6 replay bundle can be validated at this boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.domain import (
    AssuranceRun,
    EvidenceState,
    TestOutcome,
)
from neo_sf_q_intel.governance import (
    TrustedTestExecutionReceipt,
    validate_test_execution_receipt,
)
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.safety import (
    SensitiveTextError,
    contains_sensitive_text,
    require_no_sensitive_text,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTCOME_POLICY_PATH = _REPOSITORY_ROOT / "config" / "outcome-memory-policy.json"
DEFAULT_OUTCOME_EVALUATION_PATH = (
    _REPOSITORY_ROOT / "quality" / "evals" / "outcome-memory-contract-v1.json"
)
DEFAULT_OUTCOME_POLICY_SHA256 = "f02d6f5cf4c725befce27556bf16c58e70b8b75f31a23bfb22a01c412c39fe44"
DEFAULT_OUTCOME_EVALUATION_SHA256 = (
    "64a2af0d71fa4cfce418d974609432d0c147ce0f267f9434963957d602c66583"
)

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_OPAQUE_RECEIPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
class OutcomeMemoryError(RuntimeError):
    """Base exception for rejected outcome-memory operations."""


class OutcomeContractError(OutcomeMemoryError):
    """Raised when a pinned policy or evaluation contract is invalid."""


class OutcomeInputError(OutcomeMemoryError):
    """Raised when an outcome cannot be grounded in its originating run."""


class OutcomeIntegrityError(OutcomeMemoryError):
    """Raised when an immutable outcome or predecessor fails replay."""


class OutcomeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, use_enum_values=False
    )


class OutcomePosture(StrEnum):
    HISTORICAL_CANDIDATE = "HISTORICAL_CANDIDATE"


class OutcomeAuthorityState(StrEnum):
    NON_AUTHORIZING = "NON_AUTHORIZING"


class OutcomeCandidateState(StrEnum):
    CANDIDATE = "CANDIDATE"


class OutcomeKind(StrEnum):
    TEST_EXECUTION = "TEST_EXECUTION"
    INCIDENT_EVENT = "INCIDENT_EVENT"
    HUMAN_CORRECTION_CLAIM = "HUMAN_CORRECTION_CLAIM"


class CorrectionTargetKind(StrEnum):
    CLAIM = "CLAIM"
    EVIDENCE = "EVIDENCE"
    IMPACT = "IMPACT"
    TEST_SELECTION = "TEST_SELECTION"


class IncidentLifecycleEvent(StrEnum):
    OPENED = "OPENED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


class OutcomeLimits(OutcomeModel):
    maximum_identifier_characters: int = Field(alias="maximumIdentifierCharacters", ge=16, le=2048)
    maximum_text_characters: int = Field(alias="maximumTextCharacters", ge=16, le=8192)
    maximum_evidence_ids: int = Field(alias="maximumEvidenceIds", ge=1, le=1000)
    maximum_target_ids: int = Field(alias="maximumTargetIds", ge=1, le=1000)
    maximum_clock_skew_seconds: int = Field(alias="maximumClockSkewSeconds", ge=0, le=300)
    maximum_record_bytes: int = Field(alias="maximumRecordBytes", ge=4096, le=1_048_576)
    maximum_incident_chain_depth: int = Field(
        alias="maximumIncidentChainDepth", ge=1, le=1000
    )


class OutcomeMemoryPolicy(OutcomeModel):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId")
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    posture: Literal[OutcomePosture.HISTORICAL_CANDIDATE]
    authority_state: Literal[OutcomeAuthorityState.NON_AUTHORIZING] = Field(alias="authorityState")
    evaluation_set_id: str = Field(alias="evaluationSetId")
    evaluation_set_path: str = Field(alias="evaluationSetPath", max_length=1024)
    evaluation_set_sha256: str = Field(alias="evaluationSetSha256", pattern=r"^[a-f0-9]{64}$")
    incident_transitions: dict[IncidentLifecycleEvent, tuple[IncidentLifecycleEvent, ...]] = Field(
        alias="incidentTransitions"
    )
    limits: OutcomeLimits

    @model_validator(mode="after")
    def validate_policy(self) -> OutcomeMemoryPolicy:
        for value, label in (
            (self.schema_version, "schemaVersion"),
            (self.policy_version, "policyVersion"),
        ):
            if not _SEMVER.fullmatch(value):
                raise ValueError(f"{label} must use semantic versioning")
        for value, label in (
            (self.policy_id, "policyId"),
            (self.evaluation_set_id, "evaluationSetId"),
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{label} must be a stable lowercase identifier")
        path = Path(self.evaluation_set_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("evaluationSetPath must be repository-relative")
        expected_events = set(IncidentLifecycleEvent)
        if set(self.incident_transitions) != expected_events:
            raise ValueError("incidentTransitions must cover every lifecycle event")
        for event, predecessors in self.incident_transitions.items():
            if predecessors != tuple(sorted(set(predecessors), key=str)):
                raise ValueError(f"Predecessors for {event} must be sorted and unique")
            if event is IncidentLifecycleEvent.OPENED and predecessors:
                raise ValueError("OPENED must be the initial incident event")
        body = self.model_dump(mode="json", by_alias=True)
        declared = body.pop("sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Outcome policy digest does not match its content")
        return self


class OutcomeEvaluationCase(OutcomeModel):
    case_id: str = Field(alias="caseId")
    test_id: str = Field(alias="testId")

    @model_validator(mode="after")
    def validate_case(self) -> OutcomeEvaluationCase:
        if not _IDENTIFIER.fullmatch(self.case_id):
            raise ValueError("caseId must be a stable identifier")
        if not re.fullmatch(r"tests/test_outcomes\.py::test_[a-z0-9_]+", self.test_id):
            raise ValueError("testId must identify an executable outcome-memory contract test")
        return self


class OutcomeEvaluationContract(OutcomeModel):
    schema_version: str = Field(alias="schemaVersion")
    evaluation_set_id: str = Field(alias="evaluationSetId")
    evaluation_set_version: str = Field(alias="evaluationSetVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_policy_id: str = Field(alias="targetPolicyId")
    target_policy_version: str = Field(alias="targetPolicyVersion")
    invariant_status: Literal["NOT_RUN"] = Field(alias="invariantStatus")
    invariant_case_count: int = Field(alias="invariantCaseCount", ge=1)
    invariant_pass_count: Literal[0] = Field(alias="invariantPassCount")
    quality_sample_status: Literal["INSUFFICIENT_ADJUDICATED_CASES"] = Field(
        alias="qualitySampleStatus"
    )
    adjudicated_case_count: int = Field(alias="adjudicatedCaseCount", ge=0)
    minimum_adjudicated_case_count: int = Field(alias="minimumAdjudicatedCaseCount", ge=1)
    contract_cases: tuple[OutcomeEvaluationCase, ...] = Field(alias="contractCases", min_length=1)

    @model_validator(mode="after")
    def validate_evaluation(self) -> OutcomeEvaluationContract:
        for value in (
            self.schema_version,
            self.evaluation_set_version,
            self.target_policy_version,
        ):
            if not _SEMVER.fullmatch(value):
                raise ValueError("Evaluation versions must use semantic versioning")
        for value in (self.evaluation_set_id, self.target_policy_id):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError("Evaluation identifiers must be stable lowercase identifiers")
        if self.invariant_case_count != len(self.contract_cases):
            raise ValueError("invariantCaseCount must match the executable case list")
        if self.adjudicated_case_count >= self.minimum_adjudicated_case_count:
            raise ValueError("Insufficient quality status contradicts adjudicated case counts")
        case_ids = tuple(item.case_id for item in self.contract_cases)
        test_ids = tuple(item.test_id for item in self.contract_cases)
        if case_ids != tuple(sorted(set(case_ids))) or len(test_ids) != len(set(test_ids)):
            raise ValueError("contractCases must be sorted and unique")
        body = self.model_dump(mode="json", by_alias=True)
        declared = body.pop("sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Outcome evaluation digest does not match its content")
        return self


BoundedIdentifier = Annotated[str, Field(min_length=1, max_length=256)]


class OutcomeLineage(OutcomeModel):
    run_id: str
    trace_id: str
    run_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_id: BoundedIdentifier
    source_snapshot: BoundedIdentifier
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ontology_id: BoundedIdentifier
    ontology_version: BoundedIdentifier
    ontology_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_profile_id: BoundedIdentifier
    source_profile_version: BoundedIdentifier
    source_profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_policy_schema_version: BoundedIdentifier
    reasoning_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_evaluation_set_id: BoundedIdentifier
    reasoning_evaluation_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_evaluation_identity_status: Literal["INCOMPLETE_VERSION_UNAVAILABLE_UPSTREAM"] = (
        "INCOMPLETE_VERSION_UNAVAILABLE_UPSTREAM"
    )
    reasoning_workflow_linkage_status: Literal["DEFERRED_UNTIL_FULL_A6_REPLAY"] = (
        "DEFERRED_UNTIL_FULL_A6_REPLAY"
    )
    identity_gaps: tuple[
        Literal[
            "A6_REPLAY_LINKAGE_UNAVAILABLE",
            "REASONING_EVALUATION_VERSION_UNAVAILABLE",
        ],
        ...,
    ] = (
        "A6_REPLAY_LINKAGE_UNAVAILABLE",
        "REASONING_EVALUATION_VERSION_UNAVAILABLE",
    )
    governance_policy_schema_version: BoundedIdentifier | None = None
    governance_policy_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    outcome_policy_id: BoundedIdentifier
    outcome_policy_version: BoundedIdentifier
    outcome_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome_evaluation_set_id: BoundedIdentifier
    outcome_evaluation_set_version: BoundedIdentifier
    outcome_evaluation_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_optional_roots(self) -> OutcomeLineage:
        governance = (
            self.governance_policy_schema_version,
            self.governance_policy_sha256,
        )
        if any(governance) and not all(governance):
            raise ValueError("Governance lineage must be complete")
        return self


class TestExecutionOutcomePayload(OutcomeModel):
    kind: Literal[OutcomeKind.TEST_EXECUTION] = OutcomeKind.TEST_EXECUTION
    test_id: BoundedIdentifier
    outcome: TestOutcome
    runner_id: BoundedIdentifier
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    executed_at: datetime
    valid_until: datetime
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_execution(self) -> TestExecutionOutcomePayload:
        _require_aware(self.executed_at, "executed_at")
        _require_aware(self.valid_until, "valid_until")
        if self.valid_until <= self.executed_at:
            raise ValueError("valid_until must follow executed_at")
        _require_sorted_unique(self.evidence_ids, "evidence_ids")
        return self


class IncidentTargetInput(OutcomeModel):
    target_kind: CorrectionTargetKind
    target_id: BoundedIdentifier


class IncidentTargetReference(IncidentTargetInput):
    target_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _target_sort_key(
    target: IncidentTargetInput | IncidentTargetReference,
) -> tuple[str, str]:
    return target.target_kind.value, target.target_id


def _require_sorted_unique_targets(
    targets: tuple[IncidentTargetInput | IncidentTargetReference, ...],
) -> None:
    keys = tuple(_target_sort_key(target) for target in targets)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("Incident targets must be sorted by typed key and unique")


class IncidentEventInput(OutcomeModel):
    incident_id: BoundedIdentifier
    event: IncidentLifecycleEvent
    summary: str = Field(min_length=1, max_length=2000)
    observed_at: datetime
    targets: tuple[IncidentTargetInput, ...] = Field(min_length=1)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sets(self) -> IncidentEventInput:
        _require_sorted_unique_targets(self.targets)
        _require_sorted_unique(self.evidence_ids, "evidence_ids")
        _require_aware(self.observed_at, "observed_at")
        if contains_sensitive_text(self.summary):
            raise ValueError("Incident summary contains secret or machine-path text")
        return self


class IncidentEventPayload(OutcomeModel):
    kind: Literal[OutcomeKind.INCIDENT_EVENT] = OutcomeKind.INCIDENT_EVENT
    incident_id: BoundedIdentifier
    event: IncidentLifecycleEvent
    summary: str = Field(min_length=1, max_length=2000)
    observed_at: datetime
    targets: tuple[IncidentTargetReference, ...] = Field(min_length=1)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1)
    predecessor_outcome_id: str | None = Field(default=None, pattern=r"^outcome:[a-f0-9]{64}$")
    predecessor_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_predecessor_pair(self) -> IncidentEventPayload:
        if (self.predecessor_outcome_id is None) != (self.predecessor_sha256 is None):
            raise ValueError("Incident predecessor ID and digest must be supplied together")
        _require_sorted_unique_targets(self.targets)
        _require_sorted_unique(self.evidence_ids, "evidence_ids")
        _require_aware(self.observed_at, "observed_at")
        if contains_sensitive_text(self.summary):
            raise ValueError("Incident summary contains secret or machine-path text")
        return self


class HumanCorrectionClaimInput(OutcomeModel):
    correction_id: BoundedIdentifier
    target_kind: CorrectionTargetKind
    target_id: BoundedIdentifier
    prior_assertion: str = Field(min_length=1, max_length=2000)
    claimed_correction: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1)
    authority_receipt_ref: str = Field(min_length=3, max_length=256)

    @model_validator(mode="after")
    def validate_correction(self) -> HumanCorrectionClaimInput:
        _require_sorted_unique(self.evidence_ids, "evidence_ids")
        if self.prior_assertion == self.claimed_correction:
            raise ValueError("A correction must change the assertion")
        if not _OPAQUE_RECEIPT.fullmatch(self.authority_receipt_ref):
            raise ValueError("authority_receipt_ref must be an opaque identifier")
        if any(
            contains_sensitive_text(value)
            for value in (
                self.prior_assertion,
                self.claimed_correction,
                self.rationale,
                self.authority_receipt_ref,
            )
        ):
            raise ValueError("Correction contains secret or machine-path text")
        return self


class HumanCorrectionClaimPayload(OutcomeModel):
    kind: Literal[OutcomeKind.HUMAN_CORRECTION_CLAIM] = OutcomeKind.HUMAN_CORRECTION_CLAIM
    correction_id: BoundedIdentifier
    target_kind: CorrectionTargetKind
    target_id: BoundedIdentifier
    target_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prior_assertion: str = Field(min_length=1, max_length=2000)
    prior_assertion_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    claimed_correction: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[BoundedIdentifier, ...] = Field(min_length=1)
    authority_receipt_ref: str = Field(min_length=3, max_length=256)
    authority_receipt_state: Literal["UNVERIFIED_CANDIDATE"] = "UNVERIFIED_CANDIDATE"
    evidence_state: Literal[EvidenceState.INFERRED] = EvidenceState.INFERRED

    @model_validator(mode="after")
    def validate_correction(self) -> HumanCorrectionClaimPayload:
        _require_sorted_unique(self.evidence_ids, "evidence_ids")
        if self.prior_assertion == self.claimed_correction:
            raise ValueError("A correction must change the assertion")
        if self.prior_assertion_sha256 != _stable_hash(self.prior_assertion):
            raise ValueError("prior_assertion_sha256 does not match the assertion")
        if not _OPAQUE_RECEIPT.fullmatch(self.authority_receipt_ref):
            raise ValueError("authority_receipt_ref must be an opaque identifier")
        if any(
            contains_sensitive_text(value)
            for value in (
                self.prior_assertion,
                self.claimed_correction,
                self.rationale,
                self.authority_receipt_ref,
            )
        ):
            raise ValueError("Correction contains secret or machine-path text")
        return self


OutcomePayload = Annotated[
    TestExecutionOutcomePayload | IncidentEventPayload | HumanCorrectionClaimPayload,
    Field(discriminator="kind"),
]


class OutcomeRecord(OutcomeModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    posture: Literal[OutcomePosture.HISTORICAL_CANDIDATE] = OutcomePosture.HISTORICAL_CANDIDATE
    candidate_state: Literal[OutcomeCandidateState.CANDIDATE] = OutcomeCandidateState.CANDIDATE
    authority_state: Literal[OutcomeAuthorityState.NON_AUTHORIZING] = (
        OutcomeAuthorityState.NON_AUTHORIZING
    )
    may_authorize: Literal[False] = False
    may_satisfy_release_evidence: Literal[False] = False
    may_mutate_governance: Literal[False] = False
    may_mutate_decision: Literal[False] = False
    may_mutate_policy: Literal[False] = False
    may_mutate_evaluation: Literal[False] = False
    recorded_at: datetime
    lineage: OutcomeLineage
    payload: OutcomePayload
    outcome_id: str = Field(pattern=r"^outcome:[a-f0-9]{64}$")
    outcome_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_record(self) -> OutcomeRecord:
        _require_aware(self.recorded_at, "recorded_at")
        body = self.model_dump(mode="json")
        declared_id = body.pop("outcome_id")
        declared_sha = body.pop("outcome_sha256")
        actual = _stable_hash(body)
        if declared_sha != actual or declared_id != f"outcome:{actual}":
            raise ValueError("Outcome identity does not match its canonical content")
        return self


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _validate_serialized_text_safety(value: Any, *, error_type: type[OutcomeMemoryError]) -> None:
    try:
        require_no_sensitive_text(value)
    except SensitiveTextError as exc:
        raise error_type("Outcome record contains secret or machine-path text") from exc


def _contract_sha256(document: dict[str, Any]) -> str:
    body = dict(document)
    body.pop("sha256", None)
    return _stable_hash(body)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise OutcomeContractError(f"Duplicate JSON key: {key}")
        document[key] = value
    return document


def _load_contract(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not path.is_file():
        raise OutcomeContractError(f"Required contract is missing: {path.name}")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except OutcomeContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeContractError(f"Cannot read valid JSON from {path.name}") from exc
    if not isinstance(document, dict):
        raise OutcomeContractError(f"Expected a JSON object in {path.name}")
    actual = _contract_sha256(document)
    if document.get("sha256") != actual:
        raise OutcomeContractError(f"{path.name} digest does not match its content")
    if actual != expected_sha256.casefold():
        raise OutcomeContractError(f"{path.name} differs from its trusted root")
    return document


def load_outcome_evaluation_contract(
    path: Path = DEFAULT_OUTCOME_EVALUATION_PATH,
    *,
    expected_sha256: str = DEFAULT_OUTCOME_EVALUATION_SHA256,
) -> OutcomeEvaluationContract:
    try:
        return OutcomeEvaluationContract.model_validate(_load_contract(path, expected_sha256))
    except ValidationError as exc:
        raise OutcomeContractError(f"Invalid outcome evaluation contract: {exc}") from exc


def load_outcome_policy(
    path: Path = DEFAULT_OUTCOME_POLICY_PATH,
    *,
    expected_sha256: str = DEFAULT_OUTCOME_POLICY_SHA256,
) -> OutcomeMemoryPolicy:
    try:
        policy = OutcomeMemoryPolicy.model_validate(_load_contract(path, expected_sha256))
    except ValidationError as exc:
        raise OutcomeContractError(f"Invalid outcome policy: {exc}") from exc
    evaluation_path = _REPOSITORY_ROOT / policy.evaluation_set_path
    evaluation = load_outcome_evaluation_contract(
        evaluation_path, expected_sha256=policy.evaluation_set_sha256
    )
    if (evaluation.target_policy_id, evaluation.target_policy_version) != (
        policy.policy_id,
        policy.policy_version,
    ):
        raise OutcomeContractError("Outcome evaluation targets a different policy")
    return policy


def _validate_contract_pair(
    policy: OutcomeMemoryPolicy,
    evaluation: OutcomeEvaluationContract,
    *,
    expected_policy_sha256: str,
    expected_evaluation_sha256: str,
) -> tuple[OutcomeMemoryPolicy, OutcomeEvaluationContract]:
    if (
        expected_policy_sha256.casefold() != DEFAULT_OUTCOME_POLICY_SHA256
        or expected_evaluation_sha256.casefold() != DEFAULT_OUTCOME_EVALUATION_SHA256
    ):
        raise OutcomeContractError("Outcome contracts must use the module-pinned roots")
    try:
        checked_policy = OutcomeMemoryPolicy.model_validate(
            policy.model_dump(mode="json", by_alias=True)
        )
        checked_evaluation = OutcomeEvaluationContract.model_validate(
            evaluation.model_dump(mode="json", by_alias=True)
        )
    except ValidationError as exc:
        raise OutcomeContractError("Outcome contract integrity validation failed") from exc
    if checked_policy != policy or checked_evaluation != evaluation:
        raise OutcomeContractError("Outcome contracts differ from validated representations")
    if policy.sha256 != expected_policy_sha256.casefold():
        raise OutcomeContractError("Outcome policy differs from its trusted root")
    if evaluation.sha256 != expected_evaluation_sha256.casefold():
        raise OutcomeContractError("Outcome evaluation differs from its trusted root")
    if (
        policy.evaluation_set_id != evaluation.evaluation_set_id
        or policy.evaluation_set_sha256 != evaluation.sha256
        or (evaluation.target_policy_id, evaluation.target_policy_version)
        != (policy.policy_id, policy.policy_version)
    ):
        raise OutcomeContractError("Outcome policy and evaluation roots are inconsistent")
    return checked_policy, checked_evaluation


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def _require_aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC)


def _run_sha256(run: AssuranceRun) -> str:
    return _stable_hash(run.model_dump(mode="json"))


def _known_evidence_ids(run: AssuranceRun) -> set[str]:
    return {item.evidence_id for item in run.evidence}


def _canonical_target_artifacts(
    run: AssuranceRun,
) -> dict[tuple[CorrectionTargetKind, str], str]:
    artifacts: dict[tuple[CorrectionTargetKind, str], str] = {}
    groups = (
        (CorrectionTargetKind.EVIDENCE, run.evidence, "evidence_id"),
        (CorrectionTargetKind.IMPACT, run.impacts, "entity_id"),
        (CorrectionTargetKind.TEST_SELECTION, run.selected_tests, "test_id"),
        (CorrectionTargetKind.CLAIM, run.claims, "claim_id"),
    )
    for kind, items, identifier_field in groups:
        for item in items:
            identifier = str(getattr(item, identifier_field))
            key = (kind, identifier)
            if key in artifacts:
                raise OutcomeInputError("Duplicate typed correction target in originating run")
            artifacts[key] = _stable_hash(item.model_dump(mode="json"))
    return artifacts


def _require_scope(
    run: AssuranceRun,
    *,
    targets: tuple[tuple[CorrectionTargetKind, str], ...],
    evidence_ids: tuple[str, ...],
) -> None:
    unknown_evidence = set(evidence_ids) - _known_evidence_ids(run)
    if unknown_evidence:
        raise OutcomeInputError("Outcome cites evidence outside the originating run")
    unknown_targets = set(targets) - set(_canonical_target_artifacts(run))
    if unknown_targets:
        raise OutcomeInputError("Outcome targets typed artifacts outside the originating run")


def _derive_incident_targets(
    run: AssuranceRun,
    targets: tuple[IncidentTargetInput, ...],
    *,
    error_type: type[OutcomeMemoryError],
) -> tuple[IncidentTargetReference, ...]:
    try:
        artifacts = _canonical_target_artifacts(run)
    except OutcomeInputError as exc:
        raise error_type(str(exc)) from exc
    keys = tuple(_target_sort_key(target) for target in targets)
    if keys != tuple(sorted(set(keys))):
        raise error_type("Incident targets contain a duplicate or unsorted typed key")
    references: list[IncidentTargetReference] = []
    for target in targets:
        artifact_sha256 = artifacts.get((target.target_kind, target.target_id))
        if artifact_sha256 is None:
            raise error_type("Incident targets a typed artifact outside the originating run")
        references.append(
            IncidentTargetReference(
                target_kind=target.target_kind,
                target_id=target.target_id,
                target_artifact_sha256=artifact_sha256,
            )
        )
    return tuple(references)


def _validate_payload_bounds(payload: OutcomePayload, policy: OutcomeMemoryPolicy) -> None:
    if isinstance(payload, TestExecutionOutcomePayload):
        identifiers = (payload.test_id, payload.runner_id, *payload.evidence_ids)
        evidence_count = len(payload.evidence_ids)
        target_count = 1
        texts: tuple[str, ...] = ()
    elif isinstance(payload, IncidentEventPayload):
        identifiers = (
            payload.incident_id,
            *(
                value
                for target in payload.targets
                for value in (target.target_kind.value, target.target_id)
            ),
            *payload.evidence_ids,
            *(value for value in (payload.predecessor_outcome_id,) if value),
        )
        evidence_count = len(payload.evidence_ids)
        target_count = len(payload.targets)
        texts = (payload.summary,)
    else:
        identifiers = (
            payload.correction_id,
            payload.target_kind.value,
            payload.target_id,
            *payload.evidence_ids,
            payload.authority_receipt_ref,
        )
        evidence_count = len(payload.evidence_ids)
        target_count = 1
        texts = (
            payload.prior_assertion,
            payload.claimed_correction,
            payload.rationale,
        )
    limits = policy.limits
    if evidence_count > limits.maximum_evidence_ids:
        raise OutcomeInputError("Outcome evidence list exceeds its policy bound")
    if target_count > limits.maximum_target_ids:
        raise OutcomeInputError("Outcome target list exceeds its policy bound")
    if any(len(item) > limits.maximum_identifier_characters for item in identifiers):
        raise OutcomeInputError("Outcome identifier exceeds its policy bound")
    if any(len(item) > limits.maximum_text_characters for item in texts):
        raise OutcomeInputError("Outcome text exceeds its policy bound")


def _validate_governance_policy_binding(
    run: AssuranceRun, governance_policy: GovernancePolicy | None
) -> None:
    if run.governance is None:
        if governance_policy is not None:
            raise OutcomeInputError("Governance policy was supplied for an unassessed run")
        return
    try:
        canonical = GovernancePolicy.load()
    except (OSError, ValueError, ValidationError) as exc:
        raise OutcomeInputError("Module-pinned governance policy is unavailable") from exc
    if (
        canonical.schema_version != run.governance.policy_version
        or canonical.sha256 != run.governance.policy_sha256
    ):
        raise OutcomeInputError(
            "Originating assessment differs from the module-pinned governance policy"
        )
    if governance_policy is None:
        return
    try:
        validated = GovernancePolicy.model_validate(governance_policy.model_dump(mode="python"))
    except ValidationError as exc:
        raise OutcomeInputError("Governance policy is invalid") from exc
    if validated != canonical or validated.sha256 != canonical.sha256:
        raise OutcomeInputError("Governance policy differs from the module-pinned policy")


def _validate_incident_history_shape(
    history: tuple[OutcomeRecord, ...],
    policy: OutcomeMemoryPolicy,
    *,
    includes_current: bool,
    error_type: type[OutcomeMemoryError],
) -> None:
    total_depth = len(history) if includes_current else len(history) + 1
    if total_depth > policy.limits.maximum_incident_chain_depth:
        raise error_type("Incident chain exceeds its policy depth bound")
    outcome_ids = tuple(item.outcome_id for item in history)
    if len(outcome_ids) != len(set(outcome_ids)):
        raise error_type("Incident chain contains a duplicate outcome ID")


def _lineage(
    run: AssuranceRun,
    policy: OutcomeMemoryPolicy,
    evaluation: OutcomeEvaluationContract,
) -> OutcomeLineage:
    required = {
        "project_id": run.request.project_id,
        "source_snapshot": run.source_snapshot,
        "source_graph_sha256": run.source_graph_sha256,
        "normalized_graph_sha256": run.normalized_graph_sha256,
        "ontology_id": run.ontology_id,
        "ontology_version": run.ontology_version,
        "ontology_sha256": run.ontology_sha256,
        "source_profile_id": run.source_profile_id,
        "source_profile_version": run.source_profile_version,
        "source_profile_sha256": run.source_profile_sha256,
    }
    if not all(required.values()):
        raise OutcomeInputError("Outcome memory requires complete project and graph identity")
    return OutcomeLineage(
        run_id=str(run.run_id),
        trace_id=str(run.trace_id),
        run_sha256=_run_sha256(run),
        project_id=str(run.request.project_id),
        source_snapshot=str(run.source_snapshot),
        source_graph_sha256=str(run.source_graph_sha256),
        normalized_graph_sha256=str(run.normalized_graph_sha256),
        ontology_id=str(run.ontology_id),
        ontology_version=str(run.ontology_version),
        ontology_sha256=str(run.ontology_sha256),
        source_profile_id=str(run.source_profile_id),
        source_profile_version=str(run.source_profile_version),
        source_profile_sha256=str(run.source_profile_sha256),
        reasoning_policy_schema_version=run.reasoning_policy_version,
        reasoning_policy_sha256=run.reasoning_policy_sha256,
        reasoning_evaluation_set_id=run.reasoning_eval_set_id,
        reasoning_evaluation_set_sha256=run.reasoning_eval_set_sha256,
        governance_policy_schema_version=(
            run.governance.policy_version if run.governance is not None else None
        ),
        governance_policy_sha256=(
            run.governance.policy_sha256 if run.governance is not None else None
        ),
        outcome_policy_id=policy.policy_id,
        outcome_policy_version=policy.policy_version,
        outcome_policy_sha256=policy.sha256,
        outcome_evaluation_set_id=evaluation.evaluation_set_id,
        outcome_evaluation_set_version=evaluation.evaluation_set_version,
        outcome_evaluation_set_sha256=evaluation.sha256,
    )


def _trusted_test_execution(
    run: AssuranceRun,
    test_id: str,
    governance_policy: GovernancePolicy,
    recorded_at: datetime,
) -> TrustedTestExecutionReceipt:
    _validate_governance_policy_binding(run, governance_policy)
    if run.governance is None:
        raise OutcomeInputError("Trusted test outcomes require a governance assessment")
    results = [item for item in run.test_results if item.test_id == test_id]
    if len(results) != 1:
        raise OutcomeInputError("Test outcome is not a unique execution")
    validation = validate_test_execution_receipt(
        run, results[0], governance_policy, evaluated_at=recorded_at
    )
    if not validation.accepted or validation.receipt is None:
        codes = ",".join(validation.diagnostics)
        raise OutcomeInputError(f"Test execution receipt was rejected: {codes}")
    return validation.receipt


class OutcomeMemoryService:
    """Derive replayable historical candidates without persisting or mutating state."""

    def __init__(
        self,
        policy: OutcomeMemoryPolicy,
        evaluation: OutcomeEvaluationContract,
        *,
        expected_policy_sha256: str,
        expected_evaluation_sha256: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        checked_policy, checked_evaluation = _validate_contract_pair(
            policy,
            evaluation,
            expected_policy_sha256=expected_policy_sha256,
            expected_evaluation_sha256=expected_evaluation_sha256,
        )
        self._policy = checked_policy
        self._evaluation = checked_evaluation
        self._clock = clock or (lambda: datetime.now(UTC))

    @classmethod
    def from_repository(
        cls, *, clock: Callable[[], datetime] | None = None
    ) -> OutcomeMemoryService:
        policy = load_outcome_policy()
        evaluation = load_outcome_evaluation_contract(expected_sha256=policy.evaluation_set_sha256)
        return cls(
            policy,
            evaluation,
            expected_policy_sha256=DEFAULT_OUTCOME_POLICY_SHA256,
            expected_evaluation_sha256=DEFAULT_OUTCOME_EVALUATION_SHA256,
            clock=clock,
        )

    def _now(self) -> datetime:
        try:
            return _require_aware(self._clock(), "recorded_at")
        except (TypeError, ValueError) as exc:
            raise OutcomeInputError("Outcome clock must return a timezone-aware datetime") from exc

    def _record(
        self,
        run: AssuranceRun,
        payload: OutcomePayload,
        *,
        governance_policy: GovernancePolicy | None,
        recorded_at: datetime,
    ) -> OutcomeRecord:
        before = _run_sha256(run)
        _validate_governance_policy_binding(run, governance_policy)
        _validate_payload_bounds(payload, self._policy)
        lineage = _lineage(run, self._policy, self._evaluation)
        body = {
            "schema_version": "1.0.0",
            "posture": OutcomePosture.HISTORICAL_CANDIDATE,
            "candidate_state": OutcomeCandidateState.CANDIDATE,
            "authority_state": OutcomeAuthorityState.NON_AUTHORIZING,
            "may_authorize": False,
            "may_satisfy_release_evidence": False,
            "may_mutate_governance": False,
            "may_mutate_decision": False,
            "may_mutate_policy": False,
            "may_mutate_evaluation": False,
            "recorded_at": recorded_at,
            "lineage": lineage,
            "payload": payload,
        }
        digest = _stable_hash(
            OutcomeRecord.model_construct(
                **body, outcome_id=f"outcome:{'0' * 64}", outcome_sha256="0" * 64
            ).model_dump(mode="json", exclude={"outcome_id", "outcome_sha256"})
        )
        record = OutcomeRecord.model_validate(
            {**body, "outcome_id": f"outcome:{digest}", "outcome_sha256": digest}
        )
        _validate_serialized_text_safety(
            record.model_dump(mode="json"), error_type=OutcomeInputError
        )
        if (
            len(_canonical_json(record.model_dump(mode="json")))
            > self._policy.limits.maximum_record_bytes
        ):
            raise OutcomeInputError("Outcome record exceeds its policy byte bound")
        if _run_sha256(run) != before:
            raise OutcomeIntegrityError("Outcome derivation mutated the originating run")
        return record

    def derive_test_execution(
        self,
        run: AssuranceRun,
        test_id: str,
        governance_policy: GovernancePolicy,
    ) -> OutcomeRecord:
        now = self._now()
        result = _trusted_test_execution(run, test_id, governance_policy, now)
        evidence_ids = tuple(sorted(result.evidence_ids))
        _require_scope(
            run,
            targets=((CorrectionTargetKind.TEST_SELECTION, test_id),),
            evidence_ids=evidence_ids,
        )
        payload = TestExecutionOutcomePayload(
            test_id=result.test_id,
            outcome=result.outcome,
            runner_id=result.runner_id,
            result_sha256=result.result_sha256,
            executed_at=result.executed_at,
            valid_until=result.valid_until,
            evidence_ids=evidence_ids,
        )
        return self._record(
            run,
            payload,
            governance_policy=governance_policy,
            recorded_at=now,
        )

    def derive_incident_event(
        self,
        run: AssuranceRun,
        event: IncidentEventInput,
        *,
        predecessor: OutcomeRecord | None = None,
        predecessor_chain: tuple[OutcomeRecord, ...] = (),
        governance_policy: GovernancePolicy | None = None,
    ) -> OutcomeRecord:
        now = self._now()
        observed = _require_aware(event.observed_at, "observed_at")
        if observed > now + timedelta(seconds=self._policy.limits.maximum_clock_skew_seconds):
            raise OutcomeInputError("Incident event is from the future")
        target_references = _derive_incident_targets(
            run, event.targets, error_type=OutcomeInputError
        )
        _require_scope(
            run,
            targets=tuple(
                (target.target_kind, target.target_id) for target in target_references
            ),
            evidence_ids=event.evidence_ids,
        )
        predecessor_id: str | None = None
        predecessor_sha: str | None = None
        history = predecessor_chain + ((predecessor,) if predecessor is not None else ())
        _validate_incident_history_shape(
            history,
            self._policy,
            includes_current=False,
            error_type=OutcomeInputError,
        )
        if predecessor is None:
            if predecessor_chain:
                raise OutcomeInputError("Incident predecessor chain has no immediate predecessor")
            if event.event is not IncidentLifecycleEvent.OPENED:
                raise OutcomeInputError("An incident chain must begin with OPENED")
        else:
            if now < predecessor.recorded_at:
                raise OutcomeInputError("Incident record time cannot precede its predecessor")
            prior = predecessor_chain[-1] if predecessor_chain else None
            validate_outcome_record(
                predecessor,
                run,
                self._policy,
                self._evaluation,
                governance_policy=governance_policy,
                predecessor=prior,
                predecessor_chain=predecessor_chain[:-1],
                evaluated_at=now,
            )
            if not isinstance(predecessor.payload, IncidentEventPayload):
                raise OutcomeInputError("Incident predecessor has the wrong outcome kind")
            if event.event is IncidentLifecycleEvent.OPENED:
                raise OutcomeInputError("OPENED cannot have an incident predecessor")
            if predecessor.payload.incident_id != event.incident_id:
                raise OutcomeInputError("Incident predecessor belongs to another incident")
            if observed < predecessor.payload.observed_at:
                raise OutcomeInputError("Incident observed_at cannot move backward")
            allowed = self._policy.incident_transitions[event.event]
            if predecessor.payload.event not in allowed:
                raise OutcomeInputError("Incident lifecycle transition is not permitted")
            predecessor_id = predecessor.outcome_id
            predecessor_sha = predecessor.outcome_sha256
        payload = IncidentEventPayload(
            incident_id=event.incident_id,
            event=event.event,
            summary=event.summary,
            observed_at=observed,
            targets=target_references,
            evidence_ids=event.evidence_ids,
            predecessor_outcome_id=predecessor_id,
            predecessor_sha256=predecessor_sha,
        )
        return self._record(
            run,
            payload,
            governance_policy=governance_policy,
            recorded_at=now,
        )

    def derive_human_correction_claim(
        self,
        run: AssuranceRun,
        correction: HumanCorrectionClaimInput,
        *,
        governance_policy: GovernancePolicy | None = None,
    ) -> OutcomeRecord:
        now = self._now()
        _require_scope(
            run,
            targets=((correction.target_kind, correction.target_id),),
            evidence_ids=correction.evidence_ids,
        )
        artifact_sha256 = _canonical_target_artifacts(run).get(
            (correction.target_kind, correction.target_id)
        )
        if artifact_sha256 is None:
            raise OutcomeInputError("Correction target kind does not match its typed artifact")
        payload = HumanCorrectionClaimPayload(
            **correction.model_dump(mode="python"),
            target_artifact_sha256=artifact_sha256,
            prior_assertion_sha256=_stable_hash(correction.prior_assertion),
        )
        return self._record(
            run,
            payload,
            governance_policy=governance_policy,
            recorded_at=now,
        )


def validate_outcome_record(
    record: OutcomeRecord,
    run: AssuranceRun,
    policy: OutcomeMemoryPolicy,
    evaluation: OutcomeEvaluationContract,
    *,
    governance_policy: GovernancePolicy | None = None,
    predecessor: OutcomeRecord | None = None,
    predecessor_chain: tuple[OutcomeRecord, ...] = (),
    evaluated_at: datetime | None = None,
) -> OutcomeRecord:
    """Replay lineage, scope, receipt trust and append-only transition integrity."""

    policy, evaluation = _validate_contract_pair(
        policy,
        evaluation,
        expected_policy_sha256=DEFAULT_OUTCOME_POLICY_SHA256,
        expected_evaluation_sha256=DEFAULT_OUTCOME_EVALUATION_SHA256,
    )
    try:
        replayed = OutcomeRecord.model_validate(record.model_dump(mode="json"))
    except ValidationError as exc:
        raise OutcomeIntegrityError("Outcome record fails its immutable contract") from exc
    if replayed != record:
        raise OutcomeIntegrityError("Outcome differs from its validated representation")
    _validate_serialized_text_safety(
        record.model_dump(mode="json"), error_type=OutcomeIntegrityError
    )
    now = _require_aware(evaluated_at or datetime.now(UTC), "evaluated_at")
    if record.recorded_at > now + timedelta(seconds=policy.limits.maximum_clock_skew_seconds):
        raise OutcomeIntegrityError("Outcome record is from the future")
    if record.recorded_at < run.created_at.astimezone(UTC):
        raise OutcomeIntegrityError("Outcome record predates its originating run")
    _validate_governance_policy_binding(run, governance_policy)
    if record.lineage != _lineage(run, policy, evaluation):
        raise OutcomeIntegrityError("Outcome lineage differs from the originating run")
    payload = record.payload
    if isinstance(payload, TestExecutionOutcomePayload):
        if predecessor is not None or predecessor_chain:
            raise OutcomeIntegrityError("Test outcomes cannot carry incident predecessors")
        if governance_policy is None:
            raise OutcomeInputError("Test outcome replay requires its governance policy")
        result = _trusted_test_execution(
            run, payload.test_id, governance_policy, record.recorded_at
        )
        expected = TestExecutionOutcomePayload(
            test_id=result.test_id,
            outcome=result.outcome,
            runner_id=result.runner_id,
            result_sha256=result.result_sha256,
            executed_at=result.executed_at,
            valid_until=result.valid_until,
            evidence_ids=tuple(sorted(result.evidence_ids)),
        )
        if payload != expected:
            raise OutcomeIntegrityError("Stored test outcome differs from its trusted execution")
        targets = ((CorrectionTargetKind.TEST_SELECTION, payload.test_id),)
        evidence_ids = payload.evidence_ids
    elif isinstance(payload, IncidentEventPayload):
        incident_inputs = tuple(
            IncidentTargetInput(
                target_kind=target.target_kind,
                target_id=target.target_id,
            )
            for target in payload.targets
        )
        expected_targets = _derive_incident_targets(
            run, incident_inputs, error_type=OutcomeIntegrityError
        )
        if payload.targets != expected_targets:
            raise OutcomeIntegrityError("Incident targets differ from originating artifacts")
        targets = tuple(
            (target.target_kind, target.target_id) for target in payload.targets
        )
        evidence_ids = payload.evidence_ids
        history = predecessor_chain + ((predecessor,) if predecessor is not None else ()) + (
            record,
        )
        _validate_incident_history_shape(
            history,
            policy,
            includes_current=True,
            error_type=OutcomeIntegrityError,
        )
        if payload.observed_at > record.recorded_at + timedelta(
            seconds=policy.limits.maximum_clock_skew_seconds
        ):
            raise OutcomeIntegrityError("Incident event is later than its outcome record")
        if payload.event is IncidentLifecycleEvent.OPENED:
            if payload.predecessor_outcome_id or predecessor is not None or predecessor_chain:
                raise OutcomeIntegrityError("OPENED cannot carry an incident predecessor")
        elif predecessor is None:
            raise OutcomeIntegrityError("Incident transition requires its predecessor object")
        else:
            prior = predecessor_chain[-1] if predecessor_chain else None
            validate_outcome_record(
                predecessor,
                run,
                policy,
                evaluation,
                governance_policy=governance_policy,
                predecessor=prior,
                predecessor_chain=predecessor_chain[:-1],
                evaluated_at=now,
            )
            if not isinstance(predecessor.payload, IncidentEventPayload):
                raise OutcomeIntegrityError("Incident predecessor has the wrong outcome kind")
            if (
                payload.predecessor_outcome_id != predecessor.outcome_id
                or payload.predecessor_sha256 != predecessor.outcome_sha256
                or payload.incident_id != predecessor.payload.incident_id
                or predecessor.payload.event not in policy.incident_transitions[payload.event]
                or payload.observed_at < predecessor.payload.observed_at
                or record.recorded_at < predecessor.recorded_at
            ):
                raise OutcomeIntegrityError("Incident predecessor chain is invalid")
    else:
        if predecessor is not None or predecessor_chain:
            raise OutcomeIntegrityError("Correction claims cannot carry incident predecessors")
        targets = ((payload.target_kind, payload.target_id),)
        evidence_ids = payload.evidence_ids
        if payload.evidence_state is not EvidenceState.INFERRED:
            raise OutcomeIntegrityError("Human correction cannot self-confirm authority")
        expected_target_hash = _canonical_target_artifacts(run).get(
            (payload.target_kind, payload.target_id)
        )
        if payload.target_artifact_sha256 != expected_target_hash:
            raise OutcomeIntegrityError("Correction target artifact differs from the run")
    _validate_payload_bounds(payload, policy)
    _require_scope(
        run,
        targets=targets,
        evidence_ids=evidence_ids,
    )
    if len(_canonical_json(record.model_dump(mode="json"))) > policy.limits.maximum_record_bytes:
        raise OutcomeIntegrityError("Outcome record exceeds its policy byte bound")
    return record
