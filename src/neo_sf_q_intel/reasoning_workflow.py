"""Deterministic integration boundary for graph-grounded specialist proposals.

This module is deliberately framework neutral.  It consumes an already-decided
``AssuranceRun`` and replay material for A3-A5, then emits a separate advisory
result.  It never writes proposal-plane output back into deterministic facts or
release governance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neo_sf_q_intel.domain import AssuranceRun, ChangeRequest, DecisionCode
from neo_sf_q_intel.governance import assess_run, build_grounded_claims, decide
from neo_sf_q_intel.governance_policy import GovernancePolicy
from neo_sf_q_intel.specialist import (
    PromptEnvelope,
    ProviderCallOutcome,
    ProviderCallStatus,
    ProviderProfile,
    SpecialistEvaluationContract,
    SpecialistPolicy,
    SpecialistProposalArtifact,
    SpecialistRequest,
    VerifiedAnalysisContext,
    provider_capture_sha256,
    replay_verify_analysis_context,
    validate_specialist_artifact,
)


class ReasoningWorkflowError(RuntimeError):
    """Base error for a rejected workflow integration."""


class ReasoningWorkflowInputError(ReasoningWorkflowError):
    """Raised when deterministic workflow inputs are unsafe or inconsistent."""


class ReasoningWorkflowIntegrityError(ReasoningWorkflowError):
    """Raised when a specialist interaction mutates deterministic run state."""


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdvisoryPosture(StrEnum):
    ANALYSIS_ONLY = "ANALYSIS_ONLY"


class AdvisoryState(StrEnum):
    CANDIDATE = "CANDIDATE"


class AdvisoryRelationshipState(StrEnum):
    INFERRED = "INFERRED"


class AdvisoryStatus(StrEnum):
    COMPLETED = "COMPLETED"
    ABSTAINED = "ABSTAINED"
    FAILED = "FAILED"


class WorkflowLimits(WorkflowModel):
    maximum_specialists: int = Field(ge=1, le=64)
    maximum_artifact_bytes: int = Field(ge=4096, le=4_194_304)
    maximum_capture_bytes: int = Field(ge=4096, le=4_194_304)
    maximum_proposals_per_specialist: int = Field(ge=1, le=512)
    maximum_total_proposals: int = Field(ge=1, le=2048)
    maximum_total_gaps: int = Field(ge=1, le=4096)
    maximum_result_bytes: int = Field(ge=4096, le=8_388_608)
    maximum_identifier_characters: int = Field(ge=16, le=2048)
    maximum_artifact_age_seconds: int = Field(ge=1, le=2_592_000)
    maximum_clock_skew_seconds: int = Field(ge=0, le=3600)


class SpecialistStageSpec(WorkflowModel):
    """Source-neutral configured specialist slot; no role-specific control flow."""

    profile_id: str = Field(min_length=1, max_length=256)
    stage_id: str = Field(min_length=1, max_length=256)
    stage_order: int = Field(ge=0, le=10_000)
    specialist_id: str = Field(min_length=1, max_length=256)
    specialist_version: str = Field(min_length=1, max_length=64)
    capability_id: str = Field(min_length=1, max_length=256)
    eligible_change_intents: tuple[str, ...] = Field(min_length=1, max_length=16)
    requires_context_seed: Literal[True] = True

    @model_validator(mode="after")
    def validate_identifiers(self) -> SpecialistStageSpec:
        for value in (
            self.profile_id,
            self.stage_id,
            self.specialist_id,
            self.capability_id,
        ):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError("Workflow identifiers must be stable lowercase identifiers")
        if not _SEMVER.fullmatch(self.specialist_version):
            raise ValueError("specialist_version must use semantic versioning")
        if self.eligible_change_intents != tuple(sorted(set(self.eligible_change_intents))):
            raise ValueError("eligible_change_intents must be sorted and unique")
        if any(not _UPPER_IDENTIFIER.fullmatch(item) for item in self.eligible_change_intents):
            raise ValueError("eligible_change_intents must use stable uppercase identifiers")
        return self


class ReasoningWorkflowPolicy(WorkflowModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: str = Field(min_length=1, max_length=256)
    policy_version: str = Field(min_length=1, max_length=64)
    specialist_profiles: tuple[SpecialistStageSpec, ...] = Field(min_length=1)
    limits: WorkflowLimits
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_policy(self) -> ReasoningWorkflowPolicy:
        if not _IDENTIFIER.fullmatch(self.policy_id):
            raise ValueError("policy_id must be a stable lowercase identifier")
        if not _SEMVER.fullmatch(self.policy_version):
            raise ValueError("policy_version must use semantic versioning")
        profiles = self.specialist_profiles
        if len(profiles) > self.limits.maximum_specialists:
            raise ValueError("Configured specialists exceed the workflow policy limit")
        for attribute in ("profile_id", "stage_id", "specialist_id", "capability_id"):
            values = [getattr(item, attribute) for item in profiles]
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate {attribute} is not permitted")
        positions = [(item.stage_order, item.profile_id) for item in profiles]
        if positions != sorted(positions) or len({item.stage_order for item in profiles}) != len(
            profiles
        ):
            raise ValueError("Specialist profiles must have unique deterministic order")
        body = self.model_dump(mode="json")
        declared = body.pop("policy_sha256")
        if _stable_hash(body) != declared:
            raise ValueError("Workflow policy digest does not match its content")
        return self


class AdvisoryProposal(WorkflowModel):
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_artifact_sha256s: tuple[str, ...]
    source_proposal_ids: tuple[str, ...]
    proposal_id: str = Field(min_length=1, max_length=256)
    proposal_kind: str = Field(min_length=1, max_length=64)
    posture: Literal[AdvisoryPosture.ANALYSIS_ONLY] = AdvisoryPosture.ANALYSIS_ONLY
    candidate_state: Literal[AdvisoryState.CANDIDATE] = AdvisoryState.CANDIDATE
    relationship_state: Literal[AdvisoryRelationshipState.INFERRED] = (
        AdvisoryRelationshipState.INFERRED
    )
    may_authorize: Literal[False] = False
    may_satisfy_release_evidence: Literal[False] = False
    authority_eligible: Literal[False] = False
    subject_entity_id: str = Field(min_length=1, max_length=2048)
    target_entity_id: str | None = Field(default=None, max_length=2048)
    canonical_relation: str | None = Field(default=None, max_length=256)
    proposition_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_ids: tuple[str, ...]
    candidate_refs: tuple[str, ...]


class WorkflowGap(WorkflowModel):
    origin: Literal["DETERMINISTIC_RUN", "SPECIALIST", "WORKFLOW"]
    code: str = Field(min_length=1, max_length=256)
    blocking: bool
    specialist_id: str | None = Field(default=None, max_length=256)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AdvisoryActivity(WorkflowModel):
    activity_id: str = Field(pattern=r"^activity:[a-f0-9]{64}$")
    profile_id: str
    specialist_id: str
    capability_id: str
    stage_id: str
    stage_order: int
    status: AdvisoryStatus
    input_artifact_ids: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]
    policy_refs: tuple[str, ...]
    gap_codes: tuple[str, ...]
    error_class: str | None = Field(default=None, max_length=128)


class AdvisoryDeliverable(WorkflowModel):
    profile_id: str
    specialist_id: str
    capability_id: str
    status: AdvisoryStatus
    conclusion: str = Field(min_length=1, max_length=512)
    evidence_ids: tuple[str, ...]
    proposal_ids: tuple[str, ...]
    blocking_gaps: tuple[str, ...]
    nonblocking_gaps: tuple[str, ...]
    measurements: dict[str, int | str]
    next_permitted_action: Literal["DETERMINISTIC_REVIEW"] = "DETERMINISTIC_REVIEW"


class ReasoningWorkflowResult(WorkflowModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    posture: Literal[AdvisoryPosture.ANALYSIS_ONLY] = AdvisoryPosture.ANALYSIS_ONLY
    candidate_state: Literal[AdvisoryState.CANDIDATE] = AdvisoryState.CANDIDATE
    relationship_state: Literal[AdvisoryRelationshipState.INFERRED] = (
        AdvisoryRelationshipState.INFERRED
    )
    may_authorize: Literal[False] = False
    may_satisfy_release_evidence: Literal[False] = False
    authority_eligible: Literal[False] = False
    run_id: UUID
    trace_id: UUID
    project_id: str
    source_snapshot: str
    source_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_graph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    deterministic_state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    governance_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision_code: Literal["INCOMPLETE"] = "INCOMPLETE"
    workflow_policy_id: str
    workflow_policy_version: str
    workflow_policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    degraded: bool
    activities: tuple[AdvisoryActivity, ...]
    deliverables: tuple[AdvisoryDeliverable, ...]
    proposals: tuple[AdvisoryProposal, ...]
    gaps: tuple[WorkflowGap, ...]
    result_bytes: int = Field(ge=0)
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_result(self) -> ReasoningWorkflowResult:
        expected_degraded = any(
            activity.status is not AdvisoryStatus.COMPLETED for activity in self.activities
        ) or any(gap.blocking for gap in self.gaps)
        if self.degraded != expected_degraded:
            raise ValueError("degraded disagrees with specialist activity status")
        body = self.model_dump(mode="json")
        declared_hash = body.pop("result_sha256")
        declared_bytes = body["result_bytes"]
        body["result_bytes"] = 0
        if _result_size(body) != declared_bytes:
            raise ValueError("Workflow result byte accounting is inconsistent")
        body["result_bytes"] = declared_bytes
        if _stable_hash(body) != declared_hash:
            raise ValueError("Workflow result digest does not match its content")
        return self


@dataclass(frozen=True, slots=True)
class SpecialistReplayBundle:
    """All original inputs needed to replay A3, A4 and A5 at consumption."""

    target_run_id: UUID
    target_trace_id: UUID
    artifact: SpecialistProposalArtifact
    context: VerifiedAnalysisContext
    request: SpecialistRequest
    profile: ProviderProfile
    policy: SpecialistPolicy
    evaluation: SpecialistEvaluationContract
    prompt: PromptEnvelope
    outcome: ProviderCallOutcome
    expected_provider_profile_sha256: str
    expected_provider_capture_sha256: str


class SpecialistStagePort(Protocol):
    def __call__(self, request: SpecialistInvocationInput) -> SpecialistReplayBundle: ...


@dataclass(frozen=True, slots=True)
class SpecialistInvocationInput:
    run_id: UUID
    trace_id: UUID
    project_id: str
    source_snapshot: str
    deterministic_state_sha256: str
    request_sha256: str
    source_graph_sha256: str
    normalized_graph_sha256: str
    ontology_id: str
    ontology_version: str
    ontology_sha256: str
    profile_id: str
    profile_version: str
    profile_sha256: str
    reasoning_policy_sha256: str
    retrieval_eval_set_sha256: str
    context_pack_sha256: str
    fusion_result_sha256: str | None
    spec: SpecialistStageSpec


@dataclass(frozen=True, slots=True)
class SpecialistStageInput:
    spec: SpecialistStageSpec
    replay_bundle: SpecialistReplayBundle | None = None
    port: SpecialistStagePort | None = None
    preflight_context: VerifiedAnalysisContext | None = None

    def __post_init__(self) -> None:
        if self.replay_bundle is not None and self.port is not None:
            raise ValueError("A stage cannot supply both a replay bundle and a live port")
        if self.replay_bundle is None and self.port is None:
            raise ValueError("A configured stage requires a replay bundle or live port")
        if self.port is not None and self.preflight_context is None:
            raise ValueError("Live ports require replayable preflight context")


_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_UPPER_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
DEFAULT_WORKFLOW_POLICY_SHA256 = "f9ed7ac7de73d68ae053d0937cfe82737845502cc49c75a24d56900a5e2233e1"
DEFAULT_GOVERNANCE_POLICY_SHA256 = (
    "288f848e26687b72ba776e2f99ce02eef2e11c32c282d3e32301aeca1614aae8"
)
_WORKFLOW_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "reasoning-workflow-policy.json"
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReasoningWorkflowInputError("Workflow policy contains a duplicate JSON key")
        result[key] = value
    return result


def load_reasoning_workflow_policy(
    path: Path = _WORKFLOW_POLICY_PATH,
) -> ReasoningWorkflowPolicy:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
        policy = ReasoningWorkflowPolicy.model_validate(document)
    except (OSError, ValueError) as exc:
        raise ReasoningWorkflowInputError("Workflow policy cannot be loaded") from exc
    if policy.policy_sha256 != DEFAULT_WORKFLOW_POLICY_SHA256:
        raise ReasoningWorkflowInputError("Workflow policy differs from the module-pinned root")
    return policy


def _result_size(body: dict[str, Any]) -> int:
    envelope = {**body, "result_sha256": "0" * 64}
    return len(_canonical_json(envelope))


def deterministic_run_state_sha256(run: AssuranceRun) -> str:
    """Hash the complete run so no specialist-side mutation can evade detection."""

    return _stable_hash(run.model_dump(mode="json"))


def canonical_change_request_sha256(request: ChangeRequest) -> str:
    """Digest the complete normalized request, including its one project identity."""

    return _stable_hash(request.model_dump(mode="json"))


def _governance_sha256(run: AssuranceRun) -> str:
    return _stable_hash(
        run.governance.model_dump(mode="json") if run.governance is not None else None
    )


def _validate_run(run: AssuranceRun) -> None:
    if run.decision is None or run.decision.code is not DecisionCode.INCOMPLETE:
        raise ReasoningWorkflowInputError(
            "Reasoning integration requires a deterministic INCOMPLETE release decision"
        )
    if run.governance is None:
        raise ReasoningWorkflowInputError("Reasoning integration requires governance output")
    governance_policy = GovernancePolicy.load()
    if governance_policy.sha256 != DEFAULT_GOVERNANCE_POLICY_SHA256:
        raise ReasoningWorkflowInputError("Governance policy differs from its module-pinned root")
    rebuilt_claims = build_grounded_claims(run)
    if run.claims != rebuilt_claims:
        raise ReasoningWorkflowInputError("Claims differ from deterministic reconstruction")
    trusted_run = run.model_copy(update={"claims": rebuilt_claims}, deep=True)
    expected_governance = assess_run(trusted_run, governance_policy)
    if run.governance != expected_governance:
        raise ReasoningWorkflowInputError("Governance output differs from deterministic replay")
    trusted_run.governance = expected_governance
    if run.decision != decide(trusted_run, governance_policy):
        raise ReasoningWorkflowInputError("Release decision differs from deterministic replay")
    if not all(
        (
            run.request.project_id,
            run.source_snapshot,
            run.source_graph_sha256,
            run.normalized_graph_sha256,
            run.ontology_id,
            run.ontology_version,
            run.ontology_sha256,
            run.source_profile_id,
            run.source_profile_version,
            run.source_profile_sha256,
        )
    ):
        raise ReasoningWorkflowInputError("Reasoning integration requires complete graph identity")


def _validate_stage_set(
    stages: tuple[SpecialistStageInput, ...], policy: ReasoningWorkflowPolicy
) -> tuple[SpecialistStageInput, ...]:
    if not stages or len(stages) > policy.limits.maximum_specialists:
        raise ReasoningWorkflowInputError("Stage input count is outside workflow bounds")
    by_profile = {item.spec.profile_id: item for item in stages}
    if len(by_profile) != len(stages):
        raise ReasoningWorkflowInputError("Duplicate stage profile input is not permitted")
    expected = {item.profile_id: item for item in policy.specialist_profiles}
    if not set(by_profile).issubset(expected):
        raise ReasoningWorkflowInputError("Stage inputs differ from configured profiles")
    for profile_id, stage in by_profile.items():
        if stage.spec != expected[profile_id]:
            raise ReasoningWorkflowInputError("Stage specification differs from trusted policy")
    return tuple(sorted(stages, key=lambda item: (item.spec.stage_order, item.spec.profile_id)))


def _identity_matches(run: AssuranceRun, bundle: SpecialistReplayBundle) -> bool:
    pack = bundle.context.pack
    artifact = bundle.artifact
    return (
        bundle.target_run_id == run.run_id
        and bundle.target_trace_id == run.trace_id
        and pack.request_sha256 == canonical_change_request_sha256(run.request)
        and bundle.request.context_request_sha256 == canonical_change_request_sha256(run.request)
        and pack.project_id == run.request.project_id == artifact.project_id
        and pack.source_snapshot == run.source_snapshot == artifact.source_snapshot
        and pack.source_graph_sha256 == run.source_graph_sha256 == artifact.source_graph_sha256
        and pack.normalized_graph_sha256
        == run.normalized_graph_sha256
        == artifact.normalized_graph_sha256
        and pack.ontology_id == run.ontology_id == artifact.ontology_id
        and pack.ontology_version == run.ontology_version == artifact.ontology_version
        and pack.ontology_sha256 == run.ontology_sha256 == artifact.ontology_sha256
        and pack.profile_id == run.source_profile_id == artifact.profile_id
        and pack.profile_version == run.source_profile_version == artifact.profile_version
        and pack.profile_sha256 == run.source_profile_sha256 == artifact.profile_sha256
        and pack.reasoning_policy_sha256
        == run.reasoning_policy_sha256
        == artifact.reasoning_policy_sha256
        and pack.retrieval_eval_set_sha256
        == run.reasoning_eval_set_sha256
        == artifact.retrieval_eval_set_sha256
    )


def _context_identity_matches(run: AssuranceRun, context: VerifiedAnalysisContext) -> bool:
    pack = context.pack
    return (
        pack.project_id == run.request.project_id
        and pack.source_snapshot == run.source_snapshot
        and pack.source_graph_sha256 == run.source_graph_sha256
        and pack.normalized_graph_sha256 == run.normalized_graph_sha256
        and pack.ontology_id == run.ontology_id
        and pack.ontology_version == run.ontology_version
        and pack.ontology_sha256 == run.ontology_sha256
        and pack.profile_id == run.source_profile_id
        and pack.profile_version == run.source_profile_version
        and pack.profile_sha256 == run.source_profile_sha256
        and pack.reasoning_policy_sha256 == run.reasoning_policy_sha256
        and pack.retrieval_eval_set_sha256 == run.reasoning_eval_set_sha256
    )


def _failure_code(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, (TimeoutError,)):
        return "SPECIALIST_PORT_FAILURE", "TIMEOUT"
    if isinstance(exc, (OSError, ConnectionError)):
        return "SPECIALIST_PORT_FAILURE", "DEPENDENCY_ERROR"
    if isinstance(exc, ReasoningWorkflowInputError):
        return "SPECIALIST_INPUT_INVALID", "VALIDATION_ERROR"
    return "SPECIALIST_ARTIFACT_INVALID", "VALIDATION_ERROR"


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReasoningWorkflowInputError("Workflow timestamps require timezone offsets")
    return parsed.astimezone(UTC)


def _failure_stage(
    spec: SpecialistStageSpec,
    policy: ReasoningWorkflowPolicy,
    code: str,
    error_class: str,
    *,
    status: AdvisoryStatus = AdvisoryStatus.FAILED,
    blocking: bool = True,
) -> tuple[AdvisoryActivity, AdvisoryDeliverable, WorkflowGap]:
    gap = WorkflowGap(
        origin="WORKFLOW", code=code, blocking=blocking, specialist_id=spec.specialist_id
    )
    activity_body = {
        "profile_id": spec.profile_id,
        "specialist_id": spec.specialist_id,
        "capability_id": spec.capability_id,
        "stage_id": spec.stage_id,
        "stage_order": spec.stage_order,
        "status": status,
        "input_artifact_ids": (),
        "output_artifact_ids": (),
        "policy_refs": (f"workflow:{policy.policy_sha256}",),
        "gap_codes": (code,),
        "error_class": error_class,
    }
    activity = AdvisoryActivity(
        activity_id=f"activity:{_stable_hash(activity_body)}", **activity_body
    )
    deliverable = AdvisoryDeliverable(
        profile_id=spec.profile_id,
        specialist_id=spec.specialist_id,
        capability_id=spec.capability_id,
        status=status,
        conclusion="No replay-verified advisory proposal was accepted.",
        evidence_ids=(),
        proposal_ids=(),
        blocking_gaps=(code,) if blocking else (),
        nonblocking_gaps=() if blocking else (code,),
        measurements={"accepted_proposal_count": 0, "status": status.value},
    )
    return activity, deliverable, gap


def _validate_bundle(
    run: AssuranceRun,
    spec: SpecialistStageSpec,
    bundle: SpecialistReplayBundle,
    policy: ReasoningWorkflowPolicy,
    evaluated_at: str,
) -> SpecialistProposalArtifact:
    if not _identity_matches(run, bundle):
        raise ReasoningWorkflowInputError("Specialist artifact has a cross-run identity")
    identity = bundle.request.identity
    if (
        identity.specialist_id != spec.specialist_id
        or identity.specialist_version != spec.specialist_version
        or identity.capability_id != spec.capability_id
        or bundle.artifact.specialist_id != spec.specialist_id
        or bundle.artifact.specialist_version != spec.specialist_version
        or bundle.artifact.capability_id != spec.capability_id
    ):
        raise ReasoningWorkflowInputError("Specialist identity differs from configured profile")
    if bundle.expected_provider_profile_sha256 != bundle.profile.profile_sha256:
        raise ReasoningWorkflowInputError("Provider profile differs from its trusted root")
    if provider_capture_sha256(bundle.outcome) != bundle.expected_provider_capture_sha256:
        raise ReasoningWorkflowInputError("Provider capture differs from its trusted root")
    evaluated = _parse_timestamp(evaluated_at)
    completed = _parse_timestamp(bundle.outcome.completed_at)
    skew = policy.limits.maximum_clock_skew_seconds
    if completed.timestamp() > evaluated.timestamp() + skew:
        raise ReasoningWorkflowInputError("Provider capture is from the future")
    if evaluated.timestamp() - completed.timestamp() > policy.limits.maximum_artifact_age_seconds:
        raise ReasoningWorkflowInputError("Provider capture has expired")
    if bundle.context.fusion_replay is not None:
        fusion_replay = bundle.context.fusion_replay
        expiry_values = [
            *(item.expires_at for item in fusion_replay.channel_roots),
            *(item.expires_at for item in fusion_replay.candidates),
        ]
        if expiry_values and evaluated > min(_parse_timestamp(item) for item in expiry_values):
            raise ReasoningWorkflowInputError("Fusion receipt has expired")

    # Recreate trust at this boundary.  Merely receiving a VerifiedAnalysisContext instance
    # is not sufficient because its nested objects can have been reconstructed by a caller.
    verified_context = replay_verify_analysis_context(
        bundle.context.pack,
        bundle.context.graph_replay,
        fusion=bundle.context.fusion,
        fusion_replay=bundle.context.fusion_replay,
    )
    artifact = validate_specialist_artifact(
        bundle.artifact,
        verified_context,
        bundle.request,
        bundle.profile,
        bundle.policy,
        bundle.evaluation,
        bundle.prompt,
        bundle.outcome,
        expected_provider_profile_sha256=bundle.expected_provider_profile_sha256,
        expected_provider_capture_sha256=bundle.expected_provider_capture_sha256,
    )
    artifact_bytes = len(_canonical_json(artifact.model_dump(mode="json")))
    capture_bytes = len(bundle.outcome.raw_response.encode()) if bundle.outcome.raw_response else 0
    if artifact_bytes > policy.limits.maximum_artifact_bytes:
        raise ReasoningWorkflowInputError("Specialist artifact exceeds workflow bounds")
    if capture_bytes > policy.limits.maximum_capture_bytes:
        raise ReasoningWorkflowInputError("Provider capture exceeds workflow bounds")
    if len(artifact.proposals) > policy.limits.maximum_proposals_per_specialist:
        raise ReasoningWorkflowInputError("Specialist proposals exceed workflow bounds")
    identifiers = [
        spec.profile_id,
        spec.stage_id,
        spec.specialist_id,
        spec.capability_id,
        artifact.request_id,
        artifact.artifact_sha256,
        *(item.proposal_id for item in artifact.proposals),
        *(value for item in artifact.proposals for value in item.evidence_ids),
        *(item.subject_entity_id for item in artifact.proposals),
        *(item.target_entity_id for item in artifact.proposals if item.target_entity_id),
        *(item.canonical_relation for item in artifact.proposals if item.canonical_relation),
        *(value for item in artifact.proposals for value in item.candidate_refs),
        *(item.code for item in artifact.gaps),
    ]
    if any(len(item) > policy.limits.maximum_identifier_characters for item in identifiers):
        raise ReasoningWorkflowInputError("Specialist identifiers exceed workflow bounds")
    return artifact


def _successful_stage(
    spec: SpecialistStageSpec,
    artifact: SpecialistProposalArtifact,
    policy: ReasoningWorkflowPolicy,
    expected_provider_capture_sha256: str,
) -> tuple[
    AdvisoryActivity,
    AdvisoryDeliverable,
    tuple[AdvisoryProposal, ...],
    tuple[WorkflowGap, ...],
]:
    if artifact.provider_receipt.status is ProviderCallStatus.SUCCESS:
        status = AdvisoryStatus.COMPLETED if artifact.complete else AdvisoryStatus.ABSTAINED
        error_class = None
    else:
        status = AdvisoryStatus.FAILED
        error_class = (
            artifact.provider_receipt.error_code.value
            if artifact.provider_receipt.error_code
            else "PROVIDER_FAILURE"
        )
    proposals = tuple(
        AdvisoryProposal(
            artifact_sha256=artifact.artifact_sha256,
            source_artifact_sha256s=(artifact.artifact_sha256,),
            source_proposal_ids=(item.proposal_id,),
            proposal_id=item.proposal_id,
            proposal_kind=item.proposal_kind.value,
            subject_entity_id=item.subject_entity_id,
            target_entity_id=item.target_entity_id,
            canonical_relation=item.canonical_relation,
            proposition_sha256=item.proposition_sha256,
            evidence_ids=item.evidence_ids,
            candidate_refs=item.candidate_refs,
        )
        for item in sorted(artifact.proposals, key=lambda item: item.proposal_id)
    )
    gaps = tuple(
        WorkflowGap(
            origin="SPECIALIST",
            code=item.code,
            blocking=item.blocking,
            specialist_id=spec.specialist_id,
            artifact_sha256=artifact.artifact_sha256,
        )
        for item in artifact.gaps
    )
    evidence_ids = tuple(sorted({value for item in proposals for value in item.evidence_ids}))
    proposal_ids = tuple(item.proposal_id for item in proposals)
    gap_codes = tuple(sorted({item.code for item in gaps}))
    activity_body = {
        "profile_id": spec.profile_id,
        "specialist_id": spec.specialist_id,
        "capability_id": spec.capability_id,
        "stage_id": spec.stage_id,
        "stage_order": spec.stage_order,
        "status": status,
        "input_artifact_ids": (artifact.context_pack_sha256, artifact.artifact_sha256),
        "output_artifact_ids": proposal_ids,
        "policy_refs": tuple(
            sorted(
                {
                    f"workflow:{policy.policy_sha256}",
                    f"specialist:{artifact.specialist_policy_sha256}",
                    f"prompt:{artifact.prompt_sha256}",
                    f"evaluation:{artifact.evaluation_set_sha256}",
                    f"provider-profile:{artifact.provider_receipt.provider_profile_sha256}",
                    f"provider-capture:{expected_provider_capture_sha256}",
                }
            )
        ),
        "gap_codes": gap_codes,
        "error_class": error_class,
    }
    activity = AdvisoryActivity(
        activity_id=f"activity:{_stable_hash(activity_body)}", **activity_body
    )
    blocking = tuple(sorted({item.code for item in gaps if item.blocking}))
    nonblocking = tuple(sorted({item.code for item in gaps if not item.blocking}))
    deliverable = AdvisoryDeliverable(
        profile_id=spec.profile_id,
        specialist_id=spec.specialist_id,
        capability_id=spec.capability_id,
        status=status,
        conclusion=(
            f"Accepted {len(proposals)} replay-verified analysis-only candidate proposals."
            if proposals
            else "The replay-verified specialist abstained from advisory proposals."
        ),
        evidence_ids=evidence_ids,
        proposal_ids=proposal_ids,
        blocking_gaps=blocking,
        nonblocking_gaps=nonblocking,
        measurements={
            "accepted_proposal_count": len(proposals),
            "blocking_gap_count": len(blocking),
            "nonblocking_gap_count": len(nonblocking),
            "status": status.value,
        },
    )
    return activity, deliverable, proposals, gaps


def _proposal_identity(item: AdvisoryProposal) -> tuple[Any, ...]:
    return (
        item.proposal_kind,
        item.subject_entity_id,
        item.target_entity_id,
        item.canonical_relation,
        item.proposition_sha256,
    )


def _reconcile_proposals(
    proposals: list[AdvisoryProposal],
) -> tuple[list[AdvisoryProposal], list[WorkflowGap]]:
    """Deduplicate only identical facts; A4/A5 own explicit conflict classification."""

    exact: dict[tuple[Any, ...], list[AdvisoryProposal]] = {}
    for item in proposals:
        exact.setdefault(_proposal_identity(item), []).append(item)
    deduplicated: list[AdvisoryProposal] = []
    for rows in exact.values():
        first = min(rows, key=lambda item: (item.artifact_sha256, item.proposal_id))
        deduplicated.append(
            first.model_copy(
                update={
                    "artifact_sha256": min(item.artifact_sha256 for item in rows),
                    "source_artifact_sha256s": tuple(
                        sorted({value for item in rows for value in item.source_artifact_sha256s})
                    ),
                    "source_proposal_ids": tuple(
                        sorted({value for item in rows for value in item.source_proposal_ids})
                    ),
                    "evidence_ids": tuple(
                        sorted({value for item in rows for value in item.evidence_ids})
                    ),
                    "candidate_refs": tuple(
                        sorted({value for item in rows for value in item.candidate_refs})
                    ),
                }
            )
        )
    return deduplicated, []


def integrate_reasoning_workflow(
    run: AssuranceRun,
    stages: tuple[SpecialistStageInput, ...],
    policy: ReasoningWorkflowPolicy,
    *,
    expected_workflow_policy_sha256: str,
    evaluated_at: str,
    live_evaluated_at: Callable[[], str] | None = None,
) -> ReasoningWorkflowResult:
    """Validate and merge advisory specialist artifacts without mutating ``run``."""

    _validate_run(run)
    if (
        policy.policy_sha256 != expected_workflow_policy_sha256
        or policy.policy_sha256 != DEFAULT_WORKFLOW_POLICY_SHA256
        or policy != load_reasoning_workflow_policy()
    ):
        raise ReasoningWorkflowInputError("Workflow policy differs from its trusted root")
    # Re-evaluate the model digest even if a caller bypassed ordinary construction semantics.
    policy_body = policy.model_dump(mode="json")
    declared = policy_body.pop("policy_sha256")
    if _stable_hash(policy_body) != declared:
        raise ReasoningWorkflowInputError("Workflow policy digest is invalid")
    ordered = _validate_stage_set(stages, policy)
    _parse_timestamp(evaluated_at)
    before = deterministic_run_state_sha256(run)
    governance_before = _canonical_json(run.governance.model_dump(mode="json"))

    activities: list[AdvisoryActivity] = []
    deliverables: list[AdvisoryDeliverable] = []
    proposals: list[AdvisoryProposal] = []
    gaps = [
        WorkflowGap(origin="DETERMINISTIC_RUN", code=item.code, blocking=item.blocking)
        for item in run.analysis_gaps
    ]
    for stage in ordered:
        intent = run.request.change_intent.value
        if intent not in stage.spec.eligible_change_intents:
            activity, deliverable, gap = _failure_stage(
                stage.spec,
                policy,
                "SPECIALIST_INPUT_INELIGIBLE",
                "NOT_APPLICABLE",
                status=AdvisoryStatus.ABSTAINED,
                blocking=False,
            )
            activities.append(activity)
            deliverables.append(deliverable)
            gaps.append(gap)
            continue
        try:
            bundle = stage.replay_bundle
            stage_evaluated_at = evaluated_at
            preflight: VerifiedAnalysisContext | None = None
            if bundle is None:
                assert stage.port is not None
                assert stage.preflight_context is not None
                preflight = replay_verify_analysis_context(
                    stage.preflight_context.pack,
                    stage.preflight_context.graph_replay,
                    fusion=stage.preflight_context.fusion,
                    fusion_replay=stage.preflight_context.fusion_replay,
                )
                if not _context_identity_matches(run, preflight):
                    raise ReasoningWorkflowInputError(
                        "Specialist preflight context has a cross-run snapshot identity"
                    )
                if preflight.pack.request_sha256 != canonical_change_request_sha256(run.request):
                    raise ReasoningWorkflowInputError(
                        "Specialist preflight context has a cross-request identity"
                    )
                if not preflight.pack.seed_ids:
                    activity, deliverable, gap = _failure_stage(
                        stage.spec,
                        policy,
                        "SPECIALIST_CONTEXT_HAS_NO_SEED",
                        "NOT_APPLICABLE",
                        status=AdvisoryStatus.ABSTAINED,
                        blocking=False,
                    )
                    activities.append(activity)
                    deliverables.append(deliverable)
                    gaps.append(gap)
                    continue
                bundle = stage.port(
                    SpecialistInvocationInput(
                        run_id=run.run_id,
                        trace_id=run.trace_id,
                        project_id=run.request.project_id or "",
                        source_snapshot=run.source_snapshot or "",
                        deterministic_state_sha256=before,
                        request_sha256=canonical_change_request_sha256(run.request),
                        source_graph_sha256=preflight.pack.source_graph_sha256,
                        normalized_graph_sha256=preflight.pack.normalized_graph_sha256,
                        ontology_id=preflight.pack.ontology_id,
                        ontology_version=preflight.pack.ontology_version,
                        ontology_sha256=preflight.pack.ontology_sha256,
                        profile_id=preflight.pack.profile_id,
                        profile_version=preflight.pack.profile_version,
                        profile_sha256=preflight.pack.profile_sha256,
                        reasoning_policy_sha256=preflight.pack.reasoning_policy_sha256,
                        retrieval_eval_set_sha256=preflight.pack.retrieval_eval_set_sha256,
                        context_pack_sha256=preflight.pack.context_pack_sha256,
                        fusion_result_sha256=(
                            preflight.fusion.result_sha256 if preflight.fusion else None
                        ),
                        spec=stage.spec,
                    )
                )
                stage_evaluated_at = (
                    live_evaluated_at()
                    if live_evaluated_at
                    else _format_timestamp(datetime.now(UTC))
                )
            if not isinstance(bundle, SpecialistReplayBundle):
                raise ReasoningWorkflowInputError("Specialist port returned an invalid bundle")
            if preflight is not None and bundle.context != preflight:
                raise ReasoningWorkflowInputError(
                    "Specialist port substituted its replay-verified preflight context"
                )
            if not bundle.context.pack.seed_ids:
                activity, deliverable, gap = _failure_stage(
                    stage.spec,
                    policy,
                    "SPECIALIST_CONTEXT_HAS_NO_SEED",
                    "NOT_APPLICABLE",
                    status=AdvisoryStatus.ABSTAINED,
                    blocking=True,
                )
                activities.append(activity)
                deliverables.append(deliverable)
                gaps.append(gap)
                continue
            artifact = _validate_bundle(run, stage.spec, bundle, policy, stage_evaluated_at)
            activity, deliverable, stage_proposals, stage_gaps = _successful_stage(
                stage.spec,
                artifact,
                policy,
                bundle.expected_provider_capture_sha256,
            )
            activities.append(activity)
            deliverables.append(deliverable)
            proposals.extend(stage_proposals)
            gaps.extend(stage_gaps)
        except Exception as exc:  # trusted stage boundary; exception text is never emitted
            code, error_class = _failure_code(exc)
            activity, deliverable, gap = _failure_stage(stage.spec, policy, code, error_class)
            activities.append(activity)
            deliverables.append(deliverable)
            gaps.append(gap)
        if deterministic_run_state_sha256(run) != before:
            raise ReasoningWorkflowIntegrityError(
                "A specialist interaction mutated deterministic assurance state"
            )
        if _canonical_json(run.governance.model_dump(mode="json")) != governance_before:
            raise ReasoningWorkflowIntegrityError(
                "A specialist interaction mutated governance measurements"
            )

    proposals, reconciliation_gaps = _reconcile_proposals(proposals)
    gaps.extend(reconciliation_gaps)
    if len(proposals) > policy.limits.maximum_total_proposals:
        raise ReasoningWorkflowInputError("Merged proposals exceed workflow bounds")
    if len(gaps) > policy.limits.maximum_total_gaps:
        raise ReasoningWorkflowInputError("Merged gaps exceed workflow bounds")
    proposals.sort(
        key=lambda item: (item.artifact_sha256, item.proposal_id, item.subject_entity_id)
    )
    gaps.sort(
        key=lambda item: (
            item.origin,
            item.specialist_id or "",
            item.code,
            item.artifact_sha256 or "",
            item.blocking,
        )
    )
    body = {
        "schema_version": "1.0.0",
        "posture": AdvisoryPosture.ANALYSIS_ONLY,
        "candidate_state": AdvisoryState.CANDIDATE,
        "relationship_state": AdvisoryRelationshipState.INFERRED,
        "may_authorize": False,
        "may_satisfy_release_evidence": False,
        "authority_eligible": False,
        "run_id": str(run.run_id),
        "trace_id": str(run.trace_id),
        "project_id": run.request.project_id,
        "source_snapshot": run.source_snapshot,
        "source_graph_sha256": run.source_graph_sha256,
        "normalized_graph_sha256": run.normalized_graph_sha256,
        "deterministic_state_sha256": before,
        "governance_sha256": _governance_sha256(run),
        "decision_code": "INCOMPLETE",
        "workflow_policy_id": policy.policy_id,
        "workflow_policy_version": policy.policy_version,
        "workflow_policy_sha256": policy.policy_sha256,
        "degraded": any(item.status is not AdvisoryStatus.COMPLETED for item in activities)
        or any(item.blocking for item in gaps),
        "activities": [item.model_dump(mode="json") for item in activities],
        "deliverables": [item.model_dump(mode="json") for item in deliverables],
        "proposals": [item.model_dump(mode="json") for item in proposals],
        "gaps": [item.model_dump(mode="json") for item in gaps],
        "result_bytes": 0,
    }
    body["result_bytes"] = _result_size(body)
    if body["result_bytes"] > policy.limits.maximum_result_bytes:
        raise ReasoningWorkflowInputError("Workflow result exceeds its byte bound")
    return ReasoningWorkflowResult.model_validate({**body, "result_sha256": _stable_hash(body)})


def validate_reasoning_workflow_result(
    result: ReasoningWorkflowResult,
    run: AssuranceRun,
    stages: tuple[SpecialistStageInput, ...],
    policy: ReasoningWorkflowPolicy,
    *,
    expected_workflow_policy_sha256: str,
    evaluated_at: str,
) -> ReasoningWorkflowResult:
    """Replay integration from captured stage inputs and reject altered results."""

    if any(item.port is not None for item in stages):
        raise ReasoningWorkflowInputError(
            "Replay validation requires captured specialist bundles, not live ports"
        )
    expected = integrate_reasoning_workflow(
        run,
        stages,
        policy,
        expected_workflow_policy_sha256=expected_workflow_policy_sha256,
        evaluated_at=evaluated_at,
        live_evaluated_at=lambda: evaluated_at,
    )
    if result != expected:
        raise ReasoningWorkflowIntegrityError(
            "Workflow result differs from deterministic integration replay"
        )
    return result
