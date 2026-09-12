"""Offline review material; never installs or enables a host phase policy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipts import EvidencePhase
from neo_sf_q_intel.local_validation_phase import (
    CANDIDATE_REQUIRED_PHASES,
    HostLocalValidationPhasePolicy,
    LocalPhaseFilePin,
    LocalPhaseObligationPin,
    classify_local_validation_phase,
    local_validation_phase_implementation_sha256,
)
from neo_sf_q_intel.temporal import aware_utc


class LocalPhasePolicyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_class: Literal["REVIEW_PROPOSAL_ONLY"] = "REVIEW_PROPOSAL_ONLY"
    authorizes_execution: Literal[False] = False
    operator_review_required: Literal[True] = True
    compilation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    unresolved_obligation_ids: tuple[str, ...]
    draft_policy: HostLocalValidationPhasePolicy
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_proposal(self) -> LocalPhasePolicyProposal:
        if self.unresolved_obligation_ids != tuple(
            value.obligation_id for value in self.draft_policy.exemptions
        ):
            raise ValueError("Proposal must preserve the complete unresolved local set")
        if self.proposal_sha256 != stable_sha256(
            self.model_dump(mode="json", exclude={"proposal_sha256"})
        ):
            raise ValueError("Review proposal digest differs from exact body")
        return self


def propose_local_validation_phase_policy(
    capture_compilation: Callable[[], CandidateTargetCompilation],
    *,
    observed_at: datetime,
    policy_id: str = "reviewed-read-only-baseline-local-applicability",
    maximum_validity_seconds: int = 3600,
) -> LocalPhasePolicyProposal:
    """Read the fixed host capture callback; return draft data with no external writes.

    The caller must supply the configured service's capture_compilation, not a remote
    target, source path, caller-provided compilation JSON, executable or API/MCP input.
    Independent review and a separate local file/SHA installation are still required.
    """
    observed_at = aware_utc(observed_at)
    if type(maximum_validity_seconds) is not int or not 1 <= maximum_validity_seconds <= 3600:
        raise ValueError("LOCAL_PHASE_PROPOSAL_VALIDITY_INVALID")
    compilation = capture_compilation()
    compilation = CandidateTargetCompilation.model_validate(compilation.model_dump(mode="python"))
    if set(compilation.blocking_reason_codes) - {"LOCAL_SOURCE_VALIDATION_REQUIRED"}:
        raise ValueError("LOCAL_PHASE_PROPOSAL_NONLOCAL_BLOCKER")
    if not compilation.required_local_validations:
        raise ValueError("LOCAL_PHASE_PROPOSAL_NO_LOCAL_OBLIGATIONS")
    issued_at = observed_at.astimezone(UTC).replace(microsecond=0)
    expires_at = (
        min(
            issued_at + timedelta(seconds=maximum_validity_seconds),
            datetime.fromisoformat(compilation.verified_scope.valid_until.replace("Z", "+00:00")),
        )
        .astimezone(UTC)
        .replace(microsecond=0)
    )
    pins = []
    for required in compilation.required_local_validations:
        if required.obligation.required_evidence_phases != CANDIDATE_REQUIRED_PHASES:
            raise ValueError("LOCAL_PHASE_PROPOSAL_EXPLICIT_SOURCE_REQUEST_REQUIRED")
        files = {value.locator: value for value in required.bound_files}
        changed = []
        for value in compilation.changed_file_dispositions:
            if required.obligation.obligation_id not in value.local_obligation_ids:
                continue
            if value.disposition != "NON_SALESFORCE_RUNTIME" or value.locator not in files:
                raise ValueError("LOCAL_PHASE_PROPOSAL_FILE_NOT_EXEMPTABLE")
            changed.append(
                LocalPhaseFilePin(
                    locator=value.locator,
                    category=value.category,
                    content_sha256=files[value.locator].content_sha256,
                )
            )
        pins.append(
            LocalPhaseObligationPin(
                obligation_id=required.obligation.obligation_id,
                obligation_sha256=stable_sha256(
                    required.obligation.model_dump(by_alias=True, mode="json")
                ),
                candidate_tree_sha256=required.candidate_tree_sha256,
                bound_files_sha256=stable_sha256(
                    [value.model_dump(mode="json") for value in required.bound_files]
                ),
                changed_nonruntime_files=tuple(sorted(changed, key=lambda value: value.locator)),
                not_required_for_phase=EvidencePhase.LIVE_BASELINE,
            )
        )
    policy_body = HostLocalValidationPhasePolicy.model_construct(
        schema_version="1.0.0",
        authority_class="HOST_READ_ONLY_BASELINE_PHASE_APPLICABILITY",
        policy_id=policy_id,
        project_id=compilation.verified_scope.project_id,
        source_contract_sha256=compilation.source_contract_sha256,
        implementation_sha256=local_validation_phase_implementation_sha256(),
        issued_at=issued_at,
        valid_until=expires_at,
        exemptions=tuple(pins),
    ).model_dump(mode="json")
    policy_body["policy_sha256"] = stable_sha256(policy_body)
    draft = HostLocalValidationPhasePolicy.model_validate(policy_body)
    classified = classify_local_validation_phase(compilation, policy=draft, observed_at=observed_at)
    if len(classified) != len(compilation.required_local_validations):
        raise ValueError("LOCAL_PHASE_PROPOSAL_INCOMPLETE")
    proposal_body = LocalPhasePolicyProposal.model_construct(
        compilation_sha256=compilation.compilation_sha256,
        candidate_bundle_sha256=compilation.candidate_bundle_sha256,
        unresolved_obligation_ids=tuple(value.obligation_id for value in classified),
        draft_policy=draft,
    ).model_dump(mode="json")
    proposal_body["proposal_sha256"] = stable_sha256(proposal_body)
    return LocalPhasePolicyProposal.model_validate(proposal_body)
