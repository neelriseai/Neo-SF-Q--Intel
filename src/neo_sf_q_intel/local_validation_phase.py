"""Host-pinned phase applicability; an exemption is unresolved work, never a pass."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field, model_validator

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipts import EvidencePhase
from neo_sf_q_intel.safety import require_safe_repository_locator
from neo_sf_q_intel.temporal import UtcModel, aware_utc

if TYPE_CHECKING:
    from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation

_HEX = r"^[a-f0-9]{64}$"
ALL_EVIDENCE_PHASES = tuple(sorted(EvidencePhase, key=str))
CANDIDATE_REQUIRED_PHASES = tuple(
    value for value in ALL_EVIDENCE_PHASES if value is not EvidencePhase.LIVE_BASELINE
)
READ_ONLY_BASELINE_GATES = ("SF-L03", "SF-L04", "SF-L05")


class LocalValidationPhaseError(RuntimeError):
    """The independently pinned phase policy does not match the exact source boundary."""


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalPhaseFilePin(_Model):
    locator: str = Field(min_length=1, max_length=4096)
    category: Literal[
        "DOCUMENTATION", "GENERATED_INDEX", "LOCAL_CATALOG_TOOL", "LOCAL_TEST_SPECIFICATION"
    ]
    content_sha256: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def validate_locator(self) -> LocalPhaseFilePin:
        require_safe_repository_locator(self.locator)
        return self


class LocalPhaseObligationPin(_Model):
    obligation_id: str = Field(min_length=1, max_length=200)
    obligation_sha256: str = Field(pattern=_HEX)
    candidate_tree_sha256: str = Field(pattern=_HEX)
    bound_files_sha256: str = Field(pattern=_HEX)
    changed_nonruntime_files: tuple[LocalPhaseFilePin, ...] = Field(min_length=1, max_length=8192)
    not_required_for_phase: Literal[EvidencePhase.LIVE_BASELINE]

    @model_validator(mode="after")
    def validate_files(self) -> LocalPhaseObligationPin:
        paths = tuple(value.locator for value in self.changed_nonruntime_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("Phase file pins must be sorted and unique")
        return self


class HostLocalValidationPhasePolicy(_Model):
    schema_version: Literal["1.0.0"]
    authority_class: Literal["HOST_READ_ONLY_BASELINE_PHASE_APPLICABILITY"]
    policy_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    source_contract_sha256: str = Field(pattern=_HEX)
    implementation_sha256: str = Field(pattern=_HEX)
    issued_at: datetime
    valid_until: datetime
    exemptions: tuple[LocalPhaseObligationPin, ...] = Field(min_length=1, max_length=64)
    policy_sha256: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def validate_policy(self) -> HostLocalValidationPhasePolicy:
        ids = tuple(value.obligation_id for value in self.exemptions)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("Phase obligation pins must be sorted and unique")
        if (
            self.issued_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or not self.issued_at < self.valid_until
        ):
            raise ValueError("Phase policy requires a bounded timezone-aware validity")
        if self.policy_sha256 != stable_sha256(
            self.model_dump(mode="json", exclude={"policy_sha256"})
        ):
            raise ValueError("Phase policy digest differs from its exact body")
        return self


class DeferredLocalValidation(_Model):
    obligation_id: str = Field(min_length=1, max_length=200)
    command_contract_sha256: str = Field(pattern=_HEX)
    obligation_sha256: str = Field(pattern=_HEX)
    candidate_tree_sha256: str = Field(pattern=_HEX)
    bound_files_sha256: str = Field(pattern=_HEX)
    changed_nonruntime_files_sha256: str = Field(pattern=_HEX)
    source_contract_sha256: str = Field(pattern=_HEX)
    phase_policy_sha256: str = Field(pattern=_HEX)
    status: Literal["UNRESOLVED"] = "UNRESOLVED"
    satisfied: Literal[False] = False
    not_required_for_phase: Literal[EvidencePhase.LIVE_BASELINE]
    required_evidence_phases: tuple[EvidencePhase, ...]

    @model_validator(mode="after")
    def validate_remaining_phases(self) -> DeferredLocalValidation:
        if self.required_evidence_phases != CANDIDATE_REQUIRED_PHASES:
            raise ValueError("A baseline exemption cannot waive candidate or restore evidence")
        return self


def local_validation_phase_implementation_sha256() -> str:
    """Pin the validator, composition and plan schema as one host implementation."""
    root = Path(__file__).parent
    return stable_sha256(
        {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "candidate_target_compiler.py",
                "live_target_plan.py",
                "local_validation_phase.py",
                "temporal.py",
            )
        }
    )


def classify_local_validation_phase(
    compilation: CandidateTargetCompilation,
    *,
    host_phase: EvidencePhase = EvidencePhase.LIVE_BASELINE,
    policy: HostLocalValidationPhasePolicy | None = None,
    observed_at: datetime,
) -> tuple[DeferredLocalValidation, ...]:
    """Return exact unresolved exemptions, with all phases mandatory by default.

    Only a host service may supply the policy after checking its external configuration
    byte pin.  Source declarations request applicability but cannot grant exemptions.
    """
    from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation

    if not isinstance(host_phase, EvidencePhase):
        raise LocalValidationPhaseError("LOCAL_PHASE_INVALID")
    if policy is None:
        return ()
    try:
        observed_at = aware_utc(observed_at)
        compilation = CandidateTargetCompilation.model_validate(
            compilation.model_dump(mode="python")
        )
        policy = HostLocalValidationPhasePolicy.model_validate(policy.model_dump(mode="python"))
        if (
            observed_at.tzinfo is None
            or not policy.issued_at <= observed_at < policy.valid_until
            or policy.project_id != compilation.verified_scope.project_id
            or policy.source_contract_sha256 != compilation.source_contract_sha256
            or policy.implementation_sha256 != local_validation_phase_implementation_sha256()
            or observed_at
            >= datetime.fromisoformat(compilation.verified_scope.valid_until.replace("Z", "+00:00"))
        ):
            raise ValueError("Phase policy authority, source or implementation differs")
        requirements = {
            value.obligation.obligation_id: value
            for value in compilation.required_local_validations
        }
        deferred = []
        for pin in policy.exemptions:
            required = requirements.get(pin.obligation_id)
            if required is None:
                raise ValueError("Phase policy names an unknown or obsolete local obligation")
            obligation = required.obligation
            source_request = obligation.required_evidence_phases
            obligation_root = stable_sha256(obligation.model_dump(by_alias=True, mode="json"))
            files_root = stable_sha256(
                [value.model_dump(mode="json") for value in required.bound_files]
            )
            if (
                source_request != CANDIDATE_REQUIRED_PHASES
                or pin.obligation_sha256 != obligation_root
                or pin.candidate_tree_sha256 != required.candidate_tree_sha256
                or pin.bound_files_sha256 != files_root
            ):
                raise ValueError("Phase exemption differs from exact source request or manifest")
            files = {value.locator: value for value in required.bound_files}
            declared = []
            for change in compilation.changed_file_dispositions:
                if pin.obligation_id not in change.local_obligation_ids:
                    continue
                if change.disposition != "NON_SALESFORCE_RUNTIME" or change.locator not in files:
                    raise ValueError("Runtime, contract, unknown or deleted file is not exemptable")
                declared.append(
                    LocalPhaseFilePin(
                        locator=change.locator,
                        category=change.category,
                        content_sha256=files[change.locator].content_sha256,
                    )
                )
            declared_files = tuple(sorted(declared, key=lambda value: value.locator))
            if not declared_files or pin.changed_nonruntime_files != declared_files:
                raise ValueError("Phase exemption category or changed file set differs")
            if host_phase is not EvidencePhase.LIVE_BASELINE:
                continue
            deferred.append(
                DeferredLocalValidation(
                    obligation_id=pin.obligation_id,
                    command_contract_sha256=required.command_contract_sha256,
                    obligation_sha256=obligation_root,
                    candidate_tree_sha256=required.candidate_tree_sha256,
                    bound_files_sha256=files_root,
                    changed_nonruntime_files_sha256=stable_sha256(
                        [value.model_dump(mode="json") for value in declared_files]
                    ),
                    source_contract_sha256=compilation.source_contract_sha256,
                    phase_policy_sha256=policy.policy_sha256,
                    not_required_for_phase=host_phase,
                    required_evidence_phases=source_request,
                )
            )
        return tuple(deferred)
    except Exception:
        raise LocalValidationPhaseError("LOCAL_PHASE_POLICY_MISMATCH") from None
