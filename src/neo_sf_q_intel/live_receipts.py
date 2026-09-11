"""Fail-closed validation foundations for live Salesforce acceptance receipts.

This module validates a bounded, signed receipt bundle against an externally pinned,
definition-only acceptance profile.  It deliberately does not execute Salesforce operations,
persist evidence, satisfy requirements, or grant release authority.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, TypeAdapter, model_validator

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.temporal import UtcModel

_HEX64 = r"^[a-f0-9]{64}$"
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_ROLE = r"^[A-Z][A-Z0-9_]{0,127}$"
_RECEIPT_ID = r"^live-receipt:[a-f0-9]{64}$"


class LiveReceiptContractError(RuntimeError):
    """Raised when a profile or receipt cannot be parsed under the strict contract."""


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidencePhase(StrEnum):
    LIVE_BASELINE = "LIVE_BASELINE"
    CANDIDATE_CHECK_ONLY = "CANDIDATE_CHECK_ONLY"
    DEPLOYED_CANDIDATE = "DEPLOYED_CANDIDATE"
    RESTORED_BASELINE = "RESTORED_BASELINE"


class ReceiptOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    RECORDED = "RECORDED"


class ReceiptProvenance(StrEnum):
    PRODUCT_OWNED = "PRODUCT_OWNED"
    HOST_AUTHORITY = "HOST_AUTHORITY"
    FIXTURE = "FIXTURE"
    MANUAL_OBSERVATION = "MANUAL_OBSERVATION"


class CampaignEvidenceState(StrEnum):
    INCOMPLETE = "INCOMPLETE"


class TrustedIssuerClass(StrEnum):
    PRODUCT_EXECUTION = "PRODUCT_EXECUTION"
    HOST_AUTHORITY = "HOST_AUTHORITY"


class ReceiptGapCode(StrEnum):
    PROFILE_PIN_MISMATCH = "PROFILE_PIN_MISMATCH"
    RECEIPT_DUPLICATE = "RECEIPT_DUPLICATE"
    RECEIPT_UNKNOWN_ISSUER = "RECEIPT_UNKNOWN_ISSUER"
    RECEIPT_SIGNATURE_INVALID = "RECEIPT_SIGNATURE_INVALID"
    RECEIPT_ROLE_NOT_AUTHORIZED = "RECEIPT_ROLE_NOT_AUTHORIZED"
    RECEIPT_SCOPE_MISMATCH = "RECEIPT_SCOPE_MISMATCH"
    RECEIPT_STALE = "RECEIPT_STALE"
    RECEIPT_FUTURE = "RECEIPT_FUTURE"
    RECEIPT_ORDER_INVALID = "RECEIPT_ORDER_INVALID"
    RECEIPT_PROVENANCE_FORBIDDEN = "RECEIPT_PROVENANCE_FORBIDDEN"
    RECEIPT_CAPACITY_EXCEEDED = "RECEIPT_CAPACITY_EXCEEDED"
    UNEXPECTED_RECEIPT = "UNEXPECTED_RECEIPT"
    ORG_CLASSIFICATION_BLOCKED = "ORG_CLASSIFICATION_BLOCKED"
    GATE_RECEIPT_MISSING = "GATE_RECEIPT_MISSING"
    GATE_RECEIPT_DUPLICATE = "GATE_RECEIPT_DUPLICATE"
    GATE_RECEIPT_NONPASSING = "GATE_RECEIPT_NONPASSING"
    GATE_ROLE_MISMATCH = "GATE_ROLE_MISMATCH"
    GATE_PHASE_MISMATCH = "GATE_PHASE_MISMATCH"
    GATE_BINDING_MISMATCH = "GATE_BINDING_MISMATCH"
    DEPENDENCY_MISMATCH = "DEPENDENCY_MISMATCH"
    DEPENDENCY_INVALID = "DEPENDENCY_INVALID"
    INPUT_ROLE_MISMATCH = "INPUT_ROLE_MISMATCH"
    INPUT_REFERENCE_MISSING = "INPUT_REFERENCE_MISSING"
    INPUT_RECEIPT_INVALID = "INPUT_RECEIPT_INVALID"
    SELF_AUTHORIZATION = "SELF_AUTHORIZATION"
    AUTHORIZATION_SCOPE_MISMATCH = "AUTHORIZATION_SCOPE_MISMATCH"
    DEPLOYMENT_DISPATCH_INCONSISTENT = "DEPLOYMENT_DISPATCH_INCONSISTENT"
    RECOVERY_PERMIT_MISSING = "RECOVERY_PERMIT_MISSING"
    RECOVERY_PERMIT_INVALID = "RECOVERY_PERMIT_INVALID"
    RESTORE_REQUIRED = "RESTORE_REQUIRED"
    RESTORE_UNRESOLVED = "RESTORE_UNRESOLVED"
    DURABLE_LEDGER_REQUIRED = "DURABLE_LEDGER_REQUIRED"


class ReceiptScope(_Model):
    campaign_id: str = Field(pattern=_IDENTIFIER)
    project_id: str = Field(pattern=_IDENTIFIER)
    source_contract_sha256: str = Field(pattern=_HEX64)
    candidate_sha256: str = Field(pattern=_HEX64)
    build_sha256: str = Field(pattern=_HEX64)
    operation_plan_sha256: str = Field(pattern=_HEX64)
    restore_scope_sha256: str = Field(pattern=_HEX64)
    policy_sha256: str = Field(pattern=_HEX64)
    profile_sha256: str = Field(pattern=_HEX64)
    org_fingerprint_sha256: str = Field(pattern=_HEX64)
    actor_fingerprint_sha256: str = Field(pattern=_HEX64)
    recovery_deadline: datetime

    @model_validator(mode="after")
    def require_utc_deadline(self) -> ReceiptScope:
        _require_aware_utc(self.recovery_deadline, "recovery_deadline")
        return self


class ReceiptReference(_Model):
    role: str = Field(pattern=_ROLE)
    receipt_id: str = Field(pattern=_RECEIPT_ID)


class HostEnrollmentBinding(_Model):
    alias_reference_sha256: str = Field(pattern=_HEX64)
    organization_id_sha256: str = Field(pattern=_HEX64)
    instance_host_sha256: str = Field(pattern=_HEX64)
    edition_sha256: str = Field(pattern=_HEX64)
    environment_class: str = Field(pattern=_ROLE)
    persona_fingerprint_sha256: str = Field(pattern=_HEX64)


class RecoveryPermit(_Model):
    deployment_gate_id: str = Field(pattern=_IDENTIFIER)
    recovery_gate_id: str = Field(pattern=_IDENTIFIER)
    activation_receipt_role: Literal["DEPLOYMENT_DISPATCH_RECEIPT"]
    restore_scope_sha256: str = Field(pattern=_HEX64)
    operation_plan_sha256: str = Field(pattern=_HEX64)
    valid_through: datetime

    @model_validator(mode="after")
    def require_utc_validity(self) -> RecoveryPermit:
        _require_aware_utc(self.valid_through, "valid_through")
        return self


class GateReceiptPayload(_Model):
    kind: Literal["GATE"] = "GATE"
    gate_id: str = Field(pattern=_IDENTIFIER)
    receipt_type: str = Field(pattern=_ROLE)
    effect_class: str = Field(pattern=_ROLE)
    evidence_phase: EvidencePhase
    requirement_ids: tuple[str, ...] = Field(min_length=1, max_length=32)
    capability_ids: tuple[str, ...] = Field(min_length=1, max_length=64)
    scope: ReceiptScope
    dependency_receipt_ids: tuple[str, ...] = Field(default=(), max_length=32)
    input_receipts: tuple[ReceiptReference, ...] = Field(default=(), max_length=64)
    provenance: ReceiptProvenance
    outcome: ReceiptOutcome
    issued_at: datetime
    terminal_at: datetime
    expires_at: datetime
    artifact_index_sha256: str = Field(pattern=_HEX64)
    assertions_sha256: str = Field(pattern=_HEX64)
    enrollment: HostEnrollmentBinding | None = None
    gaps: tuple[Annotated[str, Field(min_length=1, max_length=256)], ...] = Field(
        default=(), max_length=64
    )

    @model_validator(mode="after")
    def validate_payload(self) -> GateReceiptPayload:
        _require_unique(self.requirement_ids, "requirement_ids")
        _require_unique(self.capability_ids, "capability_ids")
        _require_unique(self.dependency_receipt_ids, "dependency_receipt_ids")
        _require_unique(tuple(item.role for item in self.input_receipts), "input receipt roles")
        _require_unique(tuple(item.receipt_id for item in self.input_receipts), "input receipt IDs")
        _validate_times(self.issued_at, self.terminal_at, self.expires_at)
        if self.outcome is ReceiptOutcome.PASSED and self.gaps:
            raise ValueError("PASSED gate receipt cannot contain gaps")
        return self


class SupportingReceiptPayload(_Model):
    kind: Literal["SUPPORT"] = "SUPPORT"
    receipt_role: str = Field(pattern=_ROLE)
    scope: ReceiptScope
    evidence_phase: EvidencePhase | None = None
    authorized_gate_ids: tuple[str, ...] = Field(default=(), max_length=32)
    authorized_effect_classes: tuple[str, ...] = Field(default=(), max_length=32)
    recovery_permit: RecoveryPermit | None = None
    provenance: ReceiptProvenance
    outcome: Literal[ReceiptOutcome.RECORDED, ReceiptOutcome.PASSED]
    issued_at: datetime
    terminal_at: datetime
    expires_at: datetime
    artifact_sha256: str = Field(pattern=_HEX64)

    @model_validator(mode="after")
    def validate_payload(self) -> SupportingReceiptPayload:
        _require_unique(self.authorized_gate_ids, "authorized_gate_ids")
        _require_unique(self.authorized_effect_classes, "authorized_effect_classes")
        _validate_times(self.issued_at, self.terminal_at, self.expires_at)
        return self


ReceiptPayload = Annotated[
    GateReceiptPayload | SupportingReceiptPayload,
    Field(discriminator="kind"),
]
_PAYLOAD_ADAPTER = TypeAdapter(ReceiptPayload)


class SignedLiveReceipt(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    issuer_id: str = Field(pattern=_IDENTIFIER)
    payload: ReceiptPayload
    payload_sha256: str = Field(pattern=_HEX64)
    signature_sha256: str = Field(pattern=_HEX64)

    @model_validator(mode="after")
    def validate_payload_digest(self) -> SignedLiveReceipt:
        if stable_sha256(self.payload.model_dump(mode="json")) != self.payload_sha256:
            raise ValueError("payload_sha256 does not match canonical payload")
        return self

    @property
    def receipt_id(self) -> str:
        identity = stable_sha256(
            {
                "schema_version": self.schema_version,
                "issuer_id": self.issuer_id,
                "payload_sha256": self.payload_sha256,
            }
        )
        return f"live-receipt:{identity}"

    @property
    def receipt_role(self) -> str:
        if isinstance(self.payload, GateReceiptPayload):
            return self.payload.receipt_type
        return self.payload.receipt_role


@dataclass(frozen=True, slots=True, repr=False)
class TrustedIssuer:
    issuer_id: str
    issuer_class: TrustedIssuerClass
    hmac_key: bytes
    allowed_receipt_roles: frozenset[str]

    def __post_init__(self) -> None:
        if not re.fullmatch(_IDENTIFIER, self.issuer_id):
            raise ValueError("invalid trusted issuer ID")
        if len(self.hmac_key) < 32:
            raise ValueError("trusted issuer HMAC key must contain at least 32 bytes")
        if not self.allowed_receipt_roles or any(
            not re.fullmatch(_ROLE, role) for role in self.allowed_receipt_roles
        ):
            raise ValueError("trusted issuer roles are invalid")
        if len(self.allowed_receipt_roles) > 256:
            raise ValueError("trusted issuer role capacity exceeded")

    def __repr__(self) -> str:
        return (
            f"TrustedIssuer(issuer_id={self.issuer_id!r}, "
            f"issuer_class={self.issuer_class.value!r}, hmac_key=<redacted>)"
        )


class TrustedIssuerRegistry:
    """Host-owned issuer lookup; key material is never serialized by this module."""

    def __init__(self, issuers: Sequence[TrustedIssuer]) -> None:
        by_id = {item.issuer_id: item for item in issuers}
        if len(by_id) != len(issuers):
            raise ValueError("trusted issuer IDs must be unique")
        self._issuers = by_id

    def verify(
        self,
        receipt: SignedLiveReceipt,
        *,
        expected_issuer_class: TrustedIssuerClass | None = None,
    ) -> ReceiptGapCode | None:
        issuer = self._issuers.get(receipt.issuer_id)
        if issuer is None:
            return ReceiptGapCode.RECEIPT_UNKNOWN_ISSUER
        if expected_issuer_class is not None and issuer.issuer_class is not expected_issuer_class:
            return ReceiptGapCode.RECEIPT_ROLE_NOT_AUTHORIZED
        if receipt.receipt_role not in issuer.allowed_receipt_roles:
            return ReceiptGapCode.RECEIPT_ROLE_NOT_AUTHORIZED
        if isinstance(receipt.payload, GateReceiptPayload):
            expected_class = (
                TrustedIssuerClass.HOST_AUTHORITY
                if receipt.payload.receipt_type == "HOST_ENROLLMENT_RECEIPT"
                else TrustedIssuerClass.PRODUCT_EXECUTION
            )
            if issuer.issuer_class is not expected_class:
                return ReceiptGapCode.RECEIPT_ROLE_NOT_AUTHORIZED
        elif _is_authority_role(receipt.payload.receipt_role):
            if issuer.issuer_class is not TrustedIssuerClass.HOST_AUTHORITY:
                return ReceiptGapCode.RECEIPT_ROLE_NOT_AUTHORIZED
        expected = _signature(issuer.hmac_key, receipt.payload_sha256)
        if not hmac.compare_digest(expected, receipt.signature_sha256):
            return ReceiptGapCode.RECEIPT_SIGNATURE_INVALID
        return None


class LiveGateDefinition(_Model):
    gateId: str = Field(pattern=_IDENTIFIER)
    kind: str = Field(pattern=_ROLE)
    receiptType: str = Field(pattern=_ROLE)
    effectClass: str = Field(pattern=_ROLE)
    authorityClass: str = Field(pattern=_ROLE)
    targetPlanInput: str = Field(pattern=_ROLE)
    requirementIds: tuple[str, ...] = Field(min_length=1, max_length=32)
    capabilityIds: tuple[str, ...] = Field(min_length=1, max_length=64)
    dependsOn: tuple[str, ...] = Field(default=(), max_length=32)
    acceptedEvidencePhases: tuple[EvidencePhase, ...] = Field(min_length=1, max_length=4)
    requiredInputReceiptRoles: tuple[str, ...] = Field(default=(), max_length=64)
    requiredForCompletion: Literal[True]
    fixtureMaySatisfy: Literal[False]
    evidenceRequirements: tuple[str, ...] = Field(min_length=3, max_length=32)
    activationRule: str | None = Field(default=None, pattern=_ROLE)
    readbackBeforeRecovery: bool | None = None
    successPathAdditionalInputReceiptRoles: tuple[str, ...] = Field(default=(), max_length=64)
    failurePathAdditionalInputReceiptRoles: tuple[str, ...] = Field(default=(), max_length=64)
    unknownExternalStateRule: str | None = Field(default=None, pattern=_ROLE)

    @model_validator(mode="after")
    def validate_definition(self) -> LiveGateDefinition:
        _require_unique(self.requirementIds, "requirementIds")
        _require_unique(self.capabilityIds, "capabilityIds")
        _require_unique(self.dependsOn, "dependsOn")
        _require_unique(self.acceptedEvidencePhases, "acceptedEvidencePhases")
        _require_unique(self.requiredInputReceiptRoles, "requiredInputReceiptRoles")
        _require_unique(
            self.successPathAdditionalInputReceiptRoles,
            "successPathAdditionalInputReceiptRoles",
        )
        _require_unique(
            self.failurePathAdditionalInputReceiptRoles,
            "failurePathAdditionalInputReceiptRoles",
        )
        combined_roles = (
            *self.requiredInputReceiptRoles,
            *self.successPathAdditionalInputReceiptRoles,
            *self.failurePathAdditionalInputReceiptRoles,
        )
        _require_unique(combined_roles, "combined input receipt roles")
        return self


class CandidateCampaignDefinition(_Model):
    requiredForLiveSalesforceCampaignCompletion: Literal[True]
    baselineEvidenceMaySatisfy: Literal[False]
    checkOnlyEvidenceMaySatisfy: Literal[False]
    orderedGateIds: tuple[str, ...] = Field(min_length=1, max_length=32)
    preDispatchFailureRule: Literal["STOP_AND_RECORD_BLOCKED_OR_NOT_RUN"]
    postDispatchFailureRule: Literal["ALWAYS_ATTEMPT_SF_C06_BEFORE_TERMINATION"]
    manualRecoveryStatus: Literal["RESTORE_REQUIRED_MANUAL_INTERVENTION"]
    quarantineOnUnresolvedRestore: Literal[True]
    deploymentRetryWhileQuarantined: Literal[False]

    @model_validator(mode="after")
    def validate_order(self) -> CandidateCampaignDefinition:
        _require_unique(self.orderedGateIds, "orderedGateIds")
        return self


class OrgClassificationPolicy(_Model):
    """Closed v1.1 policy; neither cached display nor a preferred alias proves session subject."""

    schemaVersion: Literal["1.1.0"]
    allowedNonProductionClasses: tuple[
        Literal["SANDBOX"], Literal["SCRATCH_ORG"], Literal["DEVELOPER_EDITION"]
    ]
    blockedClasses: tuple[Literal["PRODUCTION"], Literal["UNKNOWN"]]
    requiredFingerprintInputs: tuple[
        Literal["ORGANIZATION_ID"],
        Literal["INSTANCE_HOST"],
        Literal["EDITION"],
        Literal["ENVIRONMENT_CLASS"],
        Literal["AUTHENTICATED_USER_ID"],
        Literal["AUTHENTICATED_USERNAME"],
    ]
    aliasAloneSufficient: Literal[False]
    isSandboxAloneSufficient: Literal[False]
    classificationBootstrapOperation: Literal[
        "ORG_DISPLAY+ORGANIZATION+USERINFO_SUBJECT+ACTIVE_USER_RECONCILIATION"
    ]
    classificationBootstrapOperationSha256: str = Field(pattern=_HEX64)
    serverSubjectObservationRequired: Literal[True]
    repeatSubjectAndDisplayRequired: Literal[True]
    displayAliasIsInformational: Literal[True]
    bootstrapOutputMayLeaveHostBroker: Literal[False]

    @model_validator(mode="after")
    def validate_operation(self) -> OrgClassificationPolicy:
        from neo_sf_q_intel.classification_bootstrap import CLASSIFICATION_OPERATION_PLAN_SHA256

        if self.classificationBootstrapOperationSha256 != CLASSIFICATION_OPERATION_PLAN_SHA256:
            raise ValueError("Classification policy must pin the current exact host operation plan")
        return self


class LiveAcceptanceProfile(_Model):
    schemaVersion: Literal["1.0.0"]
    profileId: str = Field(pattern=_IDENTIFIER)
    definitionOnly: Literal[True]
    acceptanceProfileDigestRequiredInEveryReceipt: Literal[True]
    missingReceiptResult: Literal["NOT_RUN"]
    completionRule: Literal["ALL_REQUIRED_GATES_PASSED"]
    fixtureSubstitutionPolicy: Literal["PROHIBITED_FOR_LIVE_GATES"]
    releaseAuthorityEnabled: Literal[False]
    evidencePhases: tuple[EvidencePhase, ...] = Field(min_length=4, max_length=4)
    dependencyReceiptRule: Literal[
        "EACH_DEPENDENCY_PASSED_RECEIPT_IS_AN_IMPLICIT_EXACT_CURRENT_SAME_CAMPAIGN_INPUT_ROOT"
    ]
    liveTargetPlan: dict[str, Any]
    authorityReceiptRules: dict[str, Any]
    orgClassificationPolicy: dict[str, Any]
    metadataRetrievalPolicy: dict[str, Any]
    candidatePhaseReceiptRules: dict[str, Any]
    compensationAuthorizationRules: dict[str, Any]
    browserActionPolicy: dict[str, Any]
    requiredGateIds: tuple[str, ...] = Field(min_length=15, max_length=15)
    gates: tuple[LiveGateDefinition, ...] = Field(min_length=1, max_length=32)
    candidateGates: tuple[LiveGateDefinition, ...] = Field(min_length=1, max_length=32)
    candidateCampaign: CandidateCampaignDefinition
    nonEvidence: tuple[str, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_profile(self) -> LiveAcceptanceProfile:
        OrgClassificationPolicy.model_validate(self.orgClassificationPolicy)
        _require_unique(self.evidencePhases, "evidencePhases")
        _require_unique(self.requiredGateIds, "requiredGateIds")
        all_gates = (*self.gates, *self.candidateGates)
        gate_ids = tuple(item.gateId for item in all_gates)
        _require_unique(gate_ids, "gate IDs")
        if len(gate_ids) != 15 or set(gate_ids) != set(self.requiredGateIds):
            raise ValueError("profile must contain the exact fifteen required gate definitions")
        if set(self.evidencePhases) != set(EvidencePhase):
            raise ValueError("profile must contain the exact four evidence phases")
        if set(self.candidateCampaign.orderedGateIds) != {
            item.gateId for item in self.candidateGates
        }:
            raise ValueError("candidate campaign must order every candidate gate exactly once")
        for gate in all_gates:
            if any(dependency not in gate_ids for dependency in gate.dependsOn):
                raise ValueError(f"{gate.gateId} has an unknown dependency")
        _require_acyclic_dependencies({item.gateId: item.dependsOn for item in all_gates})
        if sum(item.kind == "AUTHORIZED_EXACT_DEPLOYMENT" for item in self.candidateGates) != 1:
            raise ValueError("profile must contain one exact deployment gate")
        if (
            sum(item.kind == "RESTORE_AND_RESIDUE_RECONCILIATION" for item in self.candidateGates)
            != 1
        ):
            raise ValueError("profile must contain one restore and reconciliation gate")
        return self

    @property
    def gates_by_id(self) -> dict[str, LiveGateDefinition]:
        return {item.gateId: item for item in (*self.gates, *self.candidateGates)}


@dataclass(frozen=True, slots=True)
class PinnedLiveAcceptanceProfile:
    profile: LiveAcceptanceProfile
    profile_sha256: str
    parsed_model_sha256: str


class ReceiptValidationGap(_Model):
    code: ReceiptGapCode
    gate_id: str | None = None
    receipt_id: str | None = None
    blocking: Literal[True] = True


class CampaignValidationResult(_Model):
    evidence_state: Literal[CampaignEvidenceState.INCOMPLETE] = CampaignEvidenceState.INCOMPLETE
    release_eligible: Literal[False] = False
    requirements_satisfied: Literal[False] = False
    accepted_completion_numerator: Literal[0] = 0
    completion_denominator: int = Field(ge=15, le=15)
    locally_valid_gate_ids: tuple[str, ...]
    quarantined: bool
    gaps: tuple[ReceiptValidationGap, ...]
    evaluation_sha256: str = Field(pattern=_HEX64)

    @model_validator(mode="after")
    def validate_digest(self) -> CampaignValidationResult:
        body = self.model_dump(mode="json", exclude={"evaluation_sha256"})
        if stable_sha256(body) != self.evaluation_sha256:
            raise ValueError("evaluation_sha256 does not match result")
        return self


def parse_pinned_profile(
    document: Mapping[str, Any], *, expected_profile_sha256: str
) -> PinnedLiveAcceptanceProfile:
    """Parse a definition-only profile only when its canonical digest matches a host pin."""

    if not re.fullmatch(_HEX64, expected_profile_sha256):
        raise LiveReceiptContractError("expected profile digest is invalid")
    actual = stable_sha256(dict(document))
    if actual != expected_profile_sha256:
        raise LiveReceiptContractError(ReceiptGapCode.PROFILE_PIN_MISMATCH.value)
    try:
        profile = LiveAcceptanceProfile.model_validate(document)
    except Exception as exc:
        raise LiveReceiptContractError("live acceptance profile is invalid") from exc
    return PinnedLiveAcceptanceProfile(
        profile=profile,
        profile_sha256=actual,
        parsed_model_sha256=stable_sha256(profile.model_dump(mode="json")),
    )


def sign_live_receipt(
    payload: ReceiptPayload | Mapping[str, Any], *, issuer_id: str, hmac_key: bytes
) -> SignedLiveReceipt:
    """Create a signed envelope for a trusted host/product producer boundary."""

    parsed = _PAYLOAD_ADAPTER.validate_python(payload)
    digest = stable_sha256(parsed.model_dump(mode="json"))
    return SignedLiveReceipt(
        issuer_id=issuer_id,
        payload=parsed,
        payload_sha256=digest,
        signature_sha256=_signature(hmac_key, digest),
    )


def validate_live_campaign(
    *,
    pinned_profile: PinnedLiveAcceptanceProfile,
    expected_scope: ReceiptScope,
    receipts: Sequence[SignedLiveReceipt],
    issuer_registry: TrustedIssuerRegistry,
) -> CampaignValidationResult:
    """Replay a bounded campaign bundle without granting requirement or release authority."""

    now = _utc_now()
    profile = pinned_profile.profile
    gaps: list[ReceiptValidationGap] = []
    profile_current = (
        expected_scope.profile_sha256 == pinned_profile.profile_sha256
        and stable_sha256(profile.model_dump(mode="json")) == pinned_profile.parsed_model_sha256
    )
    if not profile_current:
        gaps.append(_gap(ReceiptGapCode.PROFILE_PIN_MISMATCH))
    if len(receipts) > 256:
        body = {
            "evidence_state": CampaignEvidenceState.INCOMPLETE,
            "release_eligible": False,
            "requirements_satisfied": False,
            "accepted_completion_numerator": 0,
            "completion_denominator": len(profile.requiredGateIds),
            "locally_valid_gate_ids": (),
            "quarantined": False,
            "gaps": [
                _gap(ReceiptGapCode.RECEIPT_CAPACITY_EXCEEDED).model_dump(mode="json"),
                _gap(ReceiptGapCode.DURABLE_LEDGER_REQUIRED).model_dump(mode="json"),
            ],
        }
        return CampaignValidationResult(**body, evaluation_sha256=stable_sha256(body))

    receipts_by_id: dict[str, SignedLiveReceipt] = {}
    duplicate_ids: set[str] = set()
    invalid_ids: set[str] = set()
    for receipt in receipts:
        receipt_id = receipt.receipt_id
        if receipt_id in receipts_by_id:
            duplicate_ids.add(receipt_id)
            invalid_ids.add(receipt_id)
            continue
        receipts_by_id[receipt_id] = receipt
        code = issuer_registry.verify(receipt)
        if code is not None:
            invalid_ids.add(receipt_id)
            gaps.append(_gap(code, receipt_id=receipt_id))
        if receipt.payload.scope != expected_scope:
            invalid_ids.add(receipt_id)
            gaps.append(_gap(ReceiptGapCode.RECEIPT_SCOPE_MISMATCH, receipt_id=receipt_id))
        if receipt.payload.provenance in {
            ReceiptProvenance.FIXTURE,
            ReceiptProvenance.MANUAL_OBSERVATION,
        }:
            invalid_ids.add(receipt_id)
            gaps.append(_gap(ReceiptGapCode.RECEIPT_PROVENANCE_FORBIDDEN, receipt_id=receipt_id))
        if isinstance(receipt.payload, GateReceiptPayload):
            expected_provenance = (
                ReceiptProvenance.HOST_AUTHORITY
                if receipt.payload.receipt_type == "HOST_ENROLLMENT_RECEIPT"
                else ReceiptProvenance.PRODUCT_OWNED
            )
            if receipt.payload.provenance is not expected_provenance:
                invalid_ids.add(receipt_id)
                gaps.append(
                    _gap(
                        ReceiptGapCode.RECEIPT_PROVENANCE_FORBIDDEN,
                        receipt_id=receipt_id,
                    )
                )
        elif _is_authority_role(receipt.payload.receipt_role):
            if receipt.payload.provenance is not ReceiptProvenance.HOST_AUTHORITY:
                invalid_ids.add(receipt_id)
                gaps.append(
                    _gap(
                        ReceiptGapCode.RECEIPT_PROVENANCE_FORBIDDEN,
                        receipt_id=receipt_id,
                    )
                )
        if receipt.payload.terminal_at > now:
            invalid_ids.add(receipt_id)
            gaps.append(_gap(ReceiptGapCode.RECEIPT_FUTURE, receipt_id=receipt_id))
        if receipt.payload.expires_at < now:
            invalid_ids.add(receipt_id)
            gaps.append(_gap(ReceiptGapCode.RECEIPT_STALE, receipt_id=receipt_id))
    for receipt_id in sorted(duplicate_ids):
        gaps.append(_gap(ReceiptGapCode.RECEIPT_DUPLICATE, receipt_id=receipt_id))

    gate_receipts: dict[str, SignedLiveReceipt] = {}
    for gate_id in profile.requiredGateIds:
        candidates = [
            item
            for item in receipts
            if isinstance(item.payload, GateReceiptPayload) and item.payload.gate_id == gate_id
        ]
        if not candidates:
            gaps.append(_gap(ReceiptGapCode.GATE_RECEIPT_MISSING, gate_id=gate_id))
            continue
        if len(candidates) != 1:
            gaps.append(_gap(ReceiptGapCode.GATE_RECEIPT_DUPLICATE, gate_id=gate_id))
            invalid_ids.update(item.receipt_id for item in candidates)
            continue
        gate_receipts[gate_id] = candidates[0]

    referenced_receipt_ids = {
        reference.receipt_id
        for receipt in gate_receipts.values()
        if isinstance(receipt.payload, GateReceiptPayload)
        for reference in receipt.payload.input_receipts
    }
    accepted_bundle_ids = {
        *(item.receipt_id for item in gate_receipts.values()),
        *referenced_receipt_ids,
    }
    for receipt_id in sorted(set(receipts_by_id) - accepted_bundle_ids):
        gaps.append(_gap(ReceiptGapCode.UNEXPECTED_RECEIPT, receipt_id=receipt_id))

    referenced_roles: dict[str, set[str]] = {}
    for gate in profile.gates_by_id.values():
        for role in gate.requiredInputReceiptRoles:
            referenced_roles.setdefault(role, set()).add(gate.gateId)

    invalid_gate_ids: set[str] = set()
    for gate_id, receipt in gate_receipts.items():
        definition = profile.gates_by_id[gate_id]
        payload = receipt.payload
        assert isinstance(payload, GateReceiptPayload)
        if receipt.receipt_id in invalid_ids:
            invalid_gate_ids.add(gate_id)
        if (
            payload.receipt_type != definition.receiptType
            or payload.effect_class != definition.effectClass
        ):
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.GATE_ROLE_MISMATCH, gate_id, receipt.receipt_id))
        if payload.evidence_phase not in definition.acceptedEvidencePhases:
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.GATE_PHASE_MISMATCH, gate_id, receipt.receipt_id))
        if (
            payload.requirement_ids != definition.requirementIds
            or payload.capability_ids != definition.capabilityIds
        ):
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.GATE_BINDING_MISMATCH, gate_id, receipt.receipt_id))
        if definition.kind == "HOST_NONPRODUCTION_ENROLLMENT":
            enrollment = payload.enrollment
            allowed_classes = set(
                profile.orgClassificationPolicy.get("allowedNonProductionClasses", [])
            )
            if (
                enrollment is None
                or enrollment.environment_class not in allowed_classes
                or enrollment.organization_id_sha256 != expected_scope.org_fingerprint_sha256
                or enrollment.persona_fingerprint_sha256 != expected_scope.actor_fingerprint_sha256
            ):
                invalid_gate_ids.add(gate_id)
                gaps.append(
                    _gap(
                        ReceiptGapCode.ORG_CLASSIFICATION_BLOCKED,
                        gate_id,
                        receipt.receipt_id,
                    )
                )
        elif payload.enrollment is not None:
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.GATE_BINDING_MISMATCH, gate_id, receipt.receipt_id))
        if payload.outcome is not ReceiptOutcome.PASSED:
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.GATE_RECEIPT_NONPASSING, gate_id, receipt.receipt_id))

        expected_dependencies = tuple(
            gate_receipts[item].receipt_id for item in definition.dependsOn if item in gate_receipts
        )
        if (
            len(expected_dependencies) != len(definition.dependsOn)
            or payload.dependency_receipt_ids != expected_dependencies
        ):
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.DEPENDENCY_MISMATCH, gate_id, receipt.receipt_id))

        references = {item.role: item.receipt_id for item in payload.input_receipts}
        expected_input_roles = definition.requiredInputReceiptRoles
        if definition.kind == "RESTORE_AND_RESIDUE_RECONCILIATION":
            successful_candidate = next(
                (
                    item
                    for item in profile.candidateGates
                    if item.kind == "DEPLOYED_CANDIDATE_ASSERTIONS"
                ),
                None,
            )
            if (
                successful_candidate is not None
                and successful_candidate.gateId in gate_receipts
                and successful_candidate.gateId not in invalid_gate_ids
            ):
                expected_input_roles = (
                    *expected_input_roles,
                    *definition.successPathAdditionalInputReceiptRoles,
                )
            else:
                expected_input_roles = (
                    *expected_input_roles,
                    *definition.failurePathAdditionalInputReceiptRoles,
                )
        if tuple(references) != expected_input_roles:
            invalid_gate_ids.add(gate_id)
            gaps.append(_gap(ReceiptGapCode.INPUT_ROLE_MISMATCH, gate_id, receipt.receipt_id))
        for role, referenced_id in references.items():
            if referenced_id == receipt.receipt_id:
                invalid_gate_ids.add(gate_id)
                gaps.append(_gap(ReceiptGapCode.SELF_AUTHORIZATION, gate_id, receipt.receipt_id))
                continue
            referenced = receipts_by_id.get(referenced_id)
            if referenced is None:
                invalid_gate_ids.add(gate_id)
                gaps.append(_gap(ReceiptGapCode.INPUT_REFERENCE_MISSING, gate_id, referenced_id))
                continue
            if referenced.receipt_role != role or referenced_id in invalid_ids:
                invalid_gate_ids.add(gate_id)
                gaps.append(_gap(ReceiptGapCode.INPUT_RECEIPT_INVALID, gate_id, referenced_id))
                continue
            if referenced.payload.terminal_at > payload.issued_at:
                invalid_gate_ids.add(gate_id)
                gaps.append(_gap(ReceiptGapCode.RECEIPT_ORDER_INVALID, gate_id, referenced_id))
                continue
            if isinstance(referenced.payload, SupportingReceiptPayload):
                support = referenced.payload
                if (
                    support.evidence_phase is not None
                    and support.evidence_phase != payload.evidence_phase
                ):
                    invalid_gate_ids.add(gate_id)
                    gaps.append(_gap(ReceiptGapCode.INPUT_RECEIPT_INVALID, gate_id, referenced_id))
                if _is_authority_role(role):
                    expected_gates = tuple(sorted(referenced_roles.get(role, set())))
                    expected_effects = tuple(
                        sorted({profile.gates_by_id[item].effectClass for item in expected_gates})
                    )
                    if (
                        tuple(sorted(support.authorized_gate_ids)) != expected_gates
                        or tuple(sorted(support.authorized_effect_classes)) != expected_effects
                    ):
                        invalid_gate_ids.add(gate_id)
                        gaps.append(
                            _gap(
                                ReceiptGapCode.AUTHORIZATION_SCOPE_MISMATCH,
                                gate_id,
                                referenced_id,
                            )
                        )

    changed = True
    while changed:
        changed = False
        for gate_id, receipt in gate_receipts.items():
            if gate_id in invalid_gate_ids:
                continue
            definition = profile.gates_by_id[gate_id]
            if any(item in invalid_gate_ids for item in definition.dependsOn):
                invalid_gate_ids.add(gate_id)
                gaps.append(_gap(ReceiptGapCode.DEPENDENCY_INVALID, gate_id, receipt.receipt_id))
                changed = True
            elif any(
                gate_receipts[item].payload.terminal_at > receipt.payload.issued_at
                for item in definition.dependsOn
                if item in gate_receipts
            ):
                invalid_gate_ids.add(gate_id)
                gaps.append(_gap(ReceiptGapCode.RECEIPT_ORDER_INVALID, gate_id, receipt.receipt_id))
                changed = True

    deployment_gate = next(
        (item for item in profile.candidateGates if item.kind == "AUTHORIZED_EXACT_DEPLOYMENT"),
        None,
    )
    recovery_gate = next(
        (
            item
            for item in profile.candidateGates
            if item.kind == "RESTORE_AND_RESIDUE_RECONCILIATION"
        ),
        None,
    )
    dispatch_receipts = [
        item
        for item in receipts
        if isinstance(item.payload, SupportingReceiptPayload)
        and item.payload.receipt_role == "DEPLOYMENT_DISPATCH_RECEIPT"
        and item.receipt_id not in invalid_ids
    ]
    deployment_dispatched = bool(dispatch_receipts)
    quarantined = False
    if deployment_gate and deployment_gate.gateId in gate_receipts and not deployment_dispatched:
        invalid_gate_ids.add(deployment_gate.gateId)
        gaps.append(_gap(ReceiptGapCode.DEPLOYMENT_DISPATCH_INCONSISTENT, deployment_gate.gateId))
        quarantined = True
    if deployment_dispatched:
        if len(dispatch_receipts) != 1 or recovery_gate is None or deployment_gate is None:
            gaps.append(_gap(ReceiptGapCode.DEPLOYMENT_DISPATCH_INCONSISTENT))
            quarantined = True
        else:
            dispatch = dispatch_receipts[0]
            deployment_receipt = gate_receipts.get(deployment_gate.gateId)
            recovery_receipt = gate_receipts.get(recovery_gate.gateId)
            deployment_permit_id = _input_receipt_id(
                deployment_receipt, "CAMPAIGN_RECOVERY_PERMIT_RECEIPT"
            )
            recovery_permit_id = _input_receipt_id(
                recovery_receipt, "CAMPAIGN_RECOVERY_PERMIT_RECEIPT"
            )
            recovery_dispatch_id = _input_receipt_id(
                recovery_receipt, "DEPLOYMENT_DISPATCH_RECEIPT"
            )
            permit_receipt = (
                receipts_by_id.get(deployment_permit_id)
                if deployment_permit_id == recovery_permit_id
                else None
            )
            if (
                permit_receipt is None
                or permit_receipt.receipt_id in invalid_ids
                or not isinstance(permit_receipt.payload, SupportingReceiptPayload)
                or permit_receipt.payload.receipt_role != "CAMPAIGN_RECOVERY_PERMIT_RECEIPT"
                or permit_receipt.payload.recovery_permit is None
            ):
                gaps.append(_gap(ReceiptGapCode.RECOVERY_PERMIT_MISSING, recovery_gate.gateId))
                quarantined = True
            else:
                authority_payload = permit_receipt.payload
                permit = authority_payload.recovery_permit
                assert permit is not None
                dispatch_payload = dispatch.payload
                assert isinstance(dispatch_payload, SupportingReceiptPayload)
                permit_valid = (
                    permit.deployment_gate_id == deployment_gate.gateId
                    and permit.recovery_gate_id == recovery_gate.gateId
                    and permit.restore_scope_sha256 == expected_scope.restore_scope_sha256
                    and permit.operation_plan_sha256 == expected_scope.operation_plan_sha256
                    and permit.valid_through >= expected_scope.recovery_deadline
                    and authority_payload.issued_at <= dispatch_payload.issued_at
                )
                if not permit_valid:
                    gaps.append(_gap(ReceiptGapCode.RECOVERY_PERMIT_INVALID, recovery_gate.gateId))
                    quarantined = True
            if recovery_dispatch_id != dispatch.receipt_id:
                gaps.append(
                    _gap(ReceiptGapCode.DEPLOYMENT_DISPATCH_INCONSISTENT, recovery_gate.gateId)
                )
                quarantined = True
            if recovery_receipt is None:
                gaps.append(_gap(ReceiptGapCode.RESTORE_REQUIRED, recovery_gate.gateId))
                quarantined = True
            elif recovery_gate.gateId in invalid_gate_ids:
                gaps.append(
                    _gap(
                        ReceiptGapCode.RESTORE_UNRESOLVED,
                        recovery_gate.gateId,
                        recovery_receipt.receipt_id,
                    )
                )
                quarantined = True

    locally_valid = (
        tuple(
            gate_id
            for gate_id in profile.requiredGateIds
            if gate_id in gate_receipts and gate_id not in invalid_gate_ids
        )
        if profile_current
        else ()
    )
    gaps.append(_gap(ReceiptGapCode.DURABLE_LEDGER_REQUIRED))
    normalized_gaps = _normalize_gaps(gaps)
    body = {
        "evidence_state": CampaignEvidenceState.INCOMPLETE,
        "release_eligible": False,
        "requirements_satisfied": False,
        "accepted_completion_numerator": 0,
        "completion_denominator": len(profile.requiredGateIds),
        "locally_valid_gate_ids": locally_valid,
        "quarantined": quarantined,
        "gaps": [item.model_dump(mode="json") for item in normalized_gaps],
    }
    return CampaignValidationResult(**body, evaluation_sha256=stable_sha256(body))


def _is_authority_role(role: str) -> bool:
    return "AUTHORITY" in role or "APPROVAL" in role or "PERMIT" in role


def _input_receipt_id(receipt: SignedLiveReceipt | None, role: str) -> str | None:
    if receipt is None or not isinstance(receipt.payload, GateReceiptPayload):
        return None
    return next(
        (item.receipt_id for item in receipt.payload.input_receipts if item.role == role),
        None,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _signature(key: bytes, payload_sha256: str) -> str:
    return hmac.new(key, payload_sha256.encode("ascii"), hashlib.sha256).hexdigest()


def _require_aware_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be timezone-aware UTC")


def _validate_times(issued_at: datetime, terminal_at: datetime, expires_at: datetime) -> None:
    for field, value in (
        ("issued_at", issued_at),
        ("terminal_at", terminal_at),
        ("expires_at", expires_at),
    ):
        _require_aware_utc(value, field)
    if not issued_at <= terminal_at <= expires_at:
        raise ValueError("receipt chronology is invalid")


def _require_unique(values: Sequence[Any], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")


def _require_acyclic_dependencies(dependencies: Mapping[str, Sequence[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(gate_id: str) -> None:
        if gate_id in visiting:
            raise ValueError("gate dependencies must be acyclic")
        if gate_id in visited:
            return
        visiting.add(gate_id)
        for dependency in dependencies[gate_id]:
            visit(dependency)
        visiting.remove(gate_id)
        visited.add(gate_id)

    for gate_id in dependencies:
        visit(gate_id)


def _gap(
    code: ReceiptGapCode,
    gate_id: str | None = None,
    receipt_id: str | None = None,
) -> ReceiptValidationGap:
    return ReceiptValidationGap(code=code, gate_id=gate_id, receipt_id=receipt_id)


def _normalize_gaps(gaps: Sequence[ReceiptValidationGap]) -> tuple[ReceiptValidationGap, ...]:
    keyed = {(item.code.value, item.gate_id or "", item.receipt_id or ""): item for item in gaps}
    return tuple(keyed[key] for key in sorted(keyed))


__all__ = [
    "CampaignValidationResult",
    "EvidencePhase",
    "GateReceiptPayload",
    "HostEnrollmentBinding",
    "LiveAcceptanceProfile",
    "LiveReceiptContractError",
    "PinnedLiveAcceptanceProfile",
    "ReceiptGapCode",
    "ReceiptOutcome",
    "ReceiptProvenance",
    "ReceiptReference",
    "ReceiptScope",
    "RecoveryPermit",
    "SignedLiveReceipt",
    "SupportingReceiptPayload",
    "TrustedIssuer",
    "TrustedIssuerClass",
    "TrustedIssuerRegistry",
    "parse_pinned_profile",
    "sign_live_receipt",
    "validate_live_campaign",
]
