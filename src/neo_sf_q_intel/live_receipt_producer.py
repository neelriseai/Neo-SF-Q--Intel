"""Internal, fail-closed boundary for producing durable live gate receipts.

This module does not execute Salesforce work and does not decide campaign acceptance.  It
binds already-observed evidence to a host-pinned gate definition, signs the exact canonical
receipt bytes, verifies the signature through the configured trust registry, and only then
offers those bytes to the append-only ledger.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import (
    ExecutionAssertionError,
    ExecutionAssertionStore,
    TrustedExecutionRunnerKey,
    replay_execution_assertion,
    validate_trusted_execution_runner_keys,
)
from neo_sf_q_intel.live_receipt_ledger import (
    LiveReceiptLedger,
    StoredLiveReceipt,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    HostEnrollmentBinding,
    LiveGateDefinition,
    PinnedLiveAcceptanceProfile,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptReference,
    ReceiptScope,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    sign_live_receipt,
)
from neo_sf_q_intel.temporal import aware_utc


class LiveReceiptProducerCode(StrEnum):
    AUTHORITY_INVALID = "LIVE_RECEIPT_AUTHORITY_INVALID"
    GATE_BINDING_INVALID = "LIVE_RECEIPT_GATE_BINDING_INVALID"
    SIGNATURE_INVALID = "LIVE_RECEIPT_SIGNATURE_INVALID"
    ASSERTION_ARTIFACT_INVALID = "LIVE_RECEIPT_ASSERTION_ARTIFACT_INVALID"
    APPEND_FAILED = "LIVE_RECEIPT_APPEND_FAILED"


class LiveReceiptProducerError(RuntimeError):
    """Sanitized producer failure that never includes evidence, key, or receipt content."""

    def __init__(self, code: LiveReceiptProducerCode) -> None:
        self.code = code
        super().__init__(code.value)


class GateExecutionPath(StrEnum):
    STANDARD = "STANDARD"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GateExecutionEvidence(_Model):
    """Reference to exact, durable execution assertions plus gate-specific enrollment."""

    assertion_artifact_id: str = Field(pattern=r"^execution-assertion:[a-f0-9]{64}$")
    enrollment: HostEnrollmentBinding | None = None


_AUTHORITY_SEAL = object()


@dataclass(frozen=True, slots=True, repr=False)
class TrustedGateAppendAuthority:
    """Opaque gate authority minted only from typed host-owned composition roots."""

    _seal: object
    _producer_identity: int
    gate_definition: LiveGateDefinition
    authority_class: str
    evidence_phase: EvidencePhase
    scope: ReceiptScope
    dependency_receipts: tuple[SignedLiveReceipt, ...]
    input_receipts: tuple[SignedLiveReceipt, ...]
    execution_path: GateExecutionPath
    binding_sha256: str

    def __post_init__(self) -> None:
        if self._seal is not _AUTHORITY_SEAL:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)

    def __repr__(self) -> str:
        return (
            "TrustedGateAppendAuthority("
            f"gate_id={self.gate_definition.gateId!r}, "
            f"evidence_phase={self.evidence_phase.value!r}, binding=<redacted>)"
        )


class TrustedLiveReceiptProducer:
    """Host-composed signer/ledger boundary; no mapping-based authority is accepted."""

    def __init__(
        self,
        *,
        pinned_profile: PinnedLiveAcceptanceProfile,
        expected_scope: ReceiptScope,
        issuer: TrustedIssuer,
        issuer_registry: TrustedIssuerRegistry,
        ledger: LiveReceiptLedger,
        execution_assertion_store: ExecutionAssertionStore,
        trusted_execution_runner_keys: Mapping[str, TrustedExecutionRunnerKey],
        expected_execution_contract_document: bytes | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(pinned_profile, PinnedLiveAcceptanceProfile):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if not isinstance(issuer, TrustedIssuer):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if not isinstance(expected_scope, ReceiptScope):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if not isinstance(issuer_registry, TrustedIssuerRegistry):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if not callable(getattr(ledger, "append", None)) or not callable(
            getattr(ledger, "replay", None)
        ):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if not callable(getattr(execution_assertion_store, "get", None)):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        try:
            runner_keys = validate_trusted_execution_runner_keys(trusted_execution_runner_keys)
        except ValueError:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID) from None
        if clock is not None and not callable(clock):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if expected_execution_contract_document is not None and (
            not isinstance(expected_execution_contract_document, bytes)
            or not expected_execution_contract_document
            or len(expected_execution_contract_document) > 2 * 1024 * 1024
        ):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        if (
            stable_sha256(pinned_profile.profile.model_dump(mode="json"))
            != pinned_profile.parsed_model_sha256
            or expected_scope.profile_sha256 != pinned_profile.profile_sha256
        ):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        self._profile = pinned_profile
        self._expected_scope = expected_scope
        self._issuer = issuer
        self._issuer_registry = issuer_registry
        self._ledger = ledger
        self._execution_assertion_store = execution_assertion_store
        self._trusted_execution_runner_keys = runner_keys
        self._expected_execution_contract_document = expected_execution_contract_document
        self._clock = clock or (lambda: datetime.now(UTC))
        self._identity = id(self)

    def gate_definition(self, gate_id: str) -> LiveGateDefinition:
        """Return the pinned immutable definition used by this producer."""

        try:
            return self._profile.profile.gates_by_id[gate_id]
        except KeyError:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.GATE_BINDING_INVALID) from None

    def bind_gate(
        self,
        *,
        gate_id: str,
        evidence_phase: EvidencePhase,
        dependency_receipts: Sequence[SignedLiveReceipt] = (),
        input_receipts: Sequence[SignedLiveReceipt] = (),
        execution_path: GateExecutionPath = GateExecutionPath.STANDARD,
    ) -> TrustedGateAppendAuthority:
        """Mint one immutable authority after replaying exact profile and receipt roots."""

        try:
            observed_at = self._now()
            if not isinstance(evidence_phase, EvidencePhase):
                raise TypeError
            dependencies = _typed_receipts(dependency_receipts)
            inputs = _typed_receipts(input_receipts)
            definition = self._profile.profile.gates_by_id[gate_id]
            self._validate_gate_binding(
                definition=definition,
                evidence_phase=evidence_phase,
                scope=self._expected_scope,
                dependency_receipts=dependencies,
                input_receipts=inputs,
                execution_path=execution_path,
                observed_at=observed_at,
            )
            binding = {
                "profile_sha256": self._profile.profile_sha256,
                "gate_definition": definition.model_dump(mode="json"),
                "authority_class": definition.authorityClass,
                "evidence_phase": evidence_phase.value,
                "scope": self._expected_scope.model_dump(mode="json"),
                "dependency_receipt_ids": [item.receipt_id for item in dependencies],
                "input_receipt_ids": [item.receipt_id for item in inputs],
                "execution_path": execution_path.value,
            }
            return TrustedGateAppendAuthority(
                _seal=_AUTHORITY_SEAL,
                _producer_identity=self._identity,
                gate_definition=definition,
                authority_class=definition.authorityClass,
                evidence_phase=evidence_phase,
                scope=self._expected_scope,
                dependency_receipts=dependencies,
                input_receipts=inputs,
                execution_path=execution_path,
                binding_sha256=stable_sha256(binding),
            )
        except LiveReceiptProducerError:
            raise
        except Exception:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.GATE_BINDING_INVALID) from None

    def append_gate_receipt(
        self,
        authority: TrustedGateAppendAuthority,
        evidence: GateExecutionEvidence,
    ) -> StoredLiveReceipt:
        """Sign, registry-verify, serialize, and append one exact receipt document."""

        if not isinstance(authority, TrustedGateAppendAuthority) or not isinstance(
            evidence, GateExecutionEvidence
        ):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        try:
            observed_at = self._now()
            self._replay_authority(authority, observed_at=observed_at)
            try:
                assertion_artifact = replay_execution_assertion(
                    self._execution_assertion_store,
                    assertion_artifact_id=evidence.assertion_artifact_id,
                    expected_scope=authority.scope,
                    expected_gate_id=authority.gate_definition.gateId,
                    expected_evidence_phase=authority.evidence_phase,
                    trusted_runner_keys=self._trusted_execution_runner_keys,
                    expected_receipt_role=authority.gate_definition.receiptType,
                    expected_contract_document=(
                        self._expected_execution_contract_document
                        if _requires_expected_execution_contract(authority.gate_definition.gateId)
                        else None
                    ),
                    now=observed_at,
                )
                if assertion_artifact.producer_id != self._issuer.issuer_id:
                    raise ValueError
                self._validate_evidence_causality(authority, assertion_artifact.started_at)
            except LiveReceiptProducerError:
                raise
            except ExecutionAssertionError:
                raise LiveReceiptProducerError(
                    LiveReceiptProducerCode.ASSERTION_ARTIFACT_INVALID
                ) from None
            except Exception:
                raise LiveReceiptProducerError(
                    LiveReceiptProducerCode.ASSERTION_ARTIFACT_INVALID
                ) from None
            enrollment_required = authority.gate_definition.receiptType == (
                "HOST_ENROLLMENT_RECEIPT"
            )
            if (evidence.enrollment is not None) != enrollment_required:
                raise LiveReceiptProducerError(LiveReceiptProducerCode.GATE_BINDING_INVALID)
            if evidence.enrollment is not None:
                allowed_classes = set(
                    self._profile.profile.orgClassificationPolicy.get(
                        "allowedNonProductionClasses", ()
                    )
                )
                if (
                    evidence.enrollment.environment_class not in allowed_classes
                    or evidence.enrollment.organization_id_sha256
                    != authority.scope.org_fingerprint_sha256
                    or evidence.enrollment.persona_fingerprint_sha256
                    != authority.scope.actor_fingerprint_sha256
                ):
                    raise LiveReceiptProducerError(LiveReceiptProducerCode.GATE_BINDING_INVALID)
            payload = GateReceiptPayload(
                gate_id=authority.gate_definition.gateId,
                receipt_type=authority.gate_definition.receiptType,
                effect_class=authority.gate_definition.effectClass,
                evidence_phase=authority.evidence_phase,
                requirement_ids=authority.gate_definition.requirementIds,
                capability_ids=authority.gate_definition.capabilityIds,
                scope=authority.scope,
                dependency_receipt_ids=tuple(
                    item.receipt_id for item in authority.dependency_receipts
                ),
                input_receipts=tuple(
                    ReceiptReference(role=item.receipt_role, receipt_id=item.receipt_id)
                    for item in authority.input_receipts
                ),
                provenance=(
                    ReceiptProvenance.HOST_AUTHORITY
                    if self._issuer.issuer_class is TrustedIssuerClass.HOST_AUTHORITY
                    else ReceiptProvenance.PRODUCT_OWNED
                ),
                outcome=assertion_artifact.derived_outcome,
                issued_at=assertion_artifact.started_at,
                terminal_at=assertion_artifact.terminal_at,
                expires_at=assertion_artifact.expires_at,
                artifact_index_sha256=assertion_artifact.artifact_index_sha256,
                assertions_sha256=assertion_artifact.assertions_sha256,
                enrollment=evidence.enrollment,
                gaps=assertion_artifact.gap_codes,
            )
            receipt = sign_live_receipt(
                payload,
                issuer_id=self._issuer.issuer_id,
                hmac_key=self._issuer.hmac_key,
            )
            if self._issuer_registry.verify(receipt) is not None:
                raise LiveReceiptProducerError(LiveReceiptProducerCode.SIGNATURE_INVALID)
            document = serialize_live_receipt(receipt)
        except LiveReceiptProducerError:
            raise
        except Exception:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.GATE_BINDING_INVALID) from None
        try:
            stored = self._ledger.append(document)
            if stored.receipt_document != document or stored.receipt_id != receipt.receipt_id:
                raise ValueError
            return stored
        except Exception:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.APPEND_FAILED) from None

    def _validate_gate_binding(
        self,
        *,
        definition: LiveGateDefinition,
        evidence_phase: EvidencePhase,
        scope: ReceiptScope,
        dependency_receipts: tuple[SignedLiveReceipt, ...],
        input_receipts: tuple[SignedLiveReceipt, ...],
        execution_path: GateExecutionPath,
        observed_at: datetime,
    ) -> None:
        if scope.profile_sha256 != self._profile.profile_sha256:
            raise ValueError
        if evidence_phase not in definition.acceptedEvidencePhases:
            raise ValueError
        expected_issuer_class = (
            TrustedIssuerClass.HOST_AUTHORITY
            if definition.receiptType == "HOST_ENROLLMENT_RECEIPT"
            else TrustedIssuerClass.PRODUCT_EXECUTION
        )
        if (
            self._issuer.issuer_class is not expected_issuer_class
            or definition.receiptType not in self._issuer.allowed_receipt_roles
        ):
            raise ValueError

        dependency_gate_ids = tuple(_gate_id(item) for item in dependency_receipts)
        if dependency_gate_ids != definition.dependsOn:
            raise ValueError
        expected_roles = _expected_input_roles(definition, execution_path)
        if tuple(item.receipt_role for item in input_receipts) != expected_roles:
            raise ValueError
        if _requires_expected_execution_contract(definition.gateId):
            if self._expected_execution_contract_document is None:
                raise ValueError
            contract_receipts = tuple(
                receipt
                for receipt in input_receipts
                if receipt.receipt_role == "EXPECTED_EXECUTION_CONTRACT_RECEIPT"
            )
            if len(contract_receipts) != 1 or not isinstance(
                contract_receipts[0].payload, SupportingReceiptPayload
            ):
                raise ValueError
            if (
                contract_receipts[0].payload.artifact_sha256
                != hashlib.sha256(self._expected_execution_contract_document).hexdigest()
            ):
                raise ValueError
        for receipt in (*dependency_receipts, *input_receipts):
            self._validate_input_receipt(
                receipt,
                scope=scope,
                evidence_phase=evidence_phase,
                observed_at=observed_at,
            )

    def _validate_input_receipt(
        self,
        receipt: SignedLiveReceipt,
        *,
        scope: ReceiptScope,
        evidence_phase: EvidencePhase,
        observed_at: datetime,
    ) -> None:
        payload = receipt.payload
        if (
            payload.scope != scope
            or self._issuer_registry.verify(receipt) is not None
            or payload.terminal_at > observed_at
            or payload.expires_at <= observed_at
        ):
            raise ValueError
        exact_document = serialize_live_receipt(receipt)
        durable_matches = tuple(
            stored
            for stored in self._ledger.replay(campaign_id=scope.campaign_id)
            if stored.receipt_id == receipt.receipt_id
        )
        if len(durable_matches) != 1 or durable_matches[0].receipt_document != exact_document:
            raise ValueError
        if isinstance(receipt.payload, GateReceiptPayload):
            referenced_definition = self._profile.profile.gates_by_id.get(receipt.payload.gate_id)
            expected_provenance = (
                ReceiptProvenance.HOST_AUTHORITY
                if receipt.payload.receipt_type == "HOST_ENROLLMENT_RECEIPT"
                else ReceiptProvenance.PRODUCT_OWNED
            )
            if (
                referenced_definition is None
                or receipt.payload.receipt_type != referenced_definition.receiptType
                or receipt.payload.effect_class != referenced_definition.effectClass
                or receipt.payload.evidence_phase
                not in referenced_definition.acceptedEvidencePhases
                or receipt.payload.requirement_ids != referenced_definition.requirementIds
                or receipt.payload.capability_ids != referenced_definition.capabilityIds
                or receipt.payload.outcome is not ReceiptOutcome.PASSED
                or receipt.payload.provenance is not expected_provenance
            ):
                raise ValueError
            return
        assert isinstance(receipt.payload, SupportingReceiptPayload)
        support = receipt.payload
        if support.evidence_phase is not None and support.evidence_phase is not evidence_phase:
            raise ValueError
        if _is_authority_role(support.receipt_role):
            expected_gate_ids = tuple(
                sorted(
                    gate.gateId
                    for gate in self._profile.profile.gates_by_id.values()
                    if support.receipt_role in _all_input_roles(gate)
                )
            )
            expected_effect_classes = tuple(
                sorted(
                    {
                        self._profile.profile.gates_by_id[gate_id].effectClass
                        for gate_id in expected_gate_ids
                    }
                )
            )
            if (
                support.provenance is not ReceiptProvenance.HOST_AUTHORITY
                or tuple(sorted(support.authorized_gate_ids)) != expected_gate_ids
                or tuple(sorted(support.authorized_effect_classes)) != expected_effect_classes
            ):
                raise ValueError
        elif (
            support.authorized_gate_ids
            or support.authorized_effect_classes
            or support.recovery_permit is not None
        ):
            raise ValueError

    def _replay_authority(
        self,
        authority: TrustedGateAppendAuthority,
        *,
        observed_at: datetime,
    ) -> None:
        if authority._seal is not _AUTHORITY_SEAL or authority._producer_identity != self._identity:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        current = self._profile.profile.gates_by_id.get(authority.gate_definition.gateId)
        if (
            current != authority.gate_definition
            or authority.authority_class != current.authorityClass
        ):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)
        self._validate_gate_binding(
            definition=current,
            evidence_phase=authority.evidence_phase,
            scope=authority.scope,
            dependency_receipts=authority.dependency_receipts,
            input_receipts=authority.input_receipts,
            execution_path=authority.execution_path,
            observed_at=observed_at,
        )
        body = {
            "profile_sha256": self._profile.profile_sha256,
            "gate_definition": current.model_dump(mode="json"),
            "authority_class": current.authorityClass,
            "evidence_phase": authority.evidence_phase.value,
            "scope": authority.scope.model_dump(mode="json"),
            "dependency_receipt_ids": [item.receipt_id for item in authority.dependency_receipts],
            "input_receipt_ids": [item.receipt_id for item in authority.input_receipts],
            "execution_path": authority.execution_path.value,
        }
        if stable_sha256(body) != authority.binding_sha256:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID)

    def _validate_evidence_causality(
        self,
        authority: TrustedGateAppendAuthority,
        execution_started_at: datetime,
    ) -> None:
        if any(
            receipt.payload.terminal_at > execution_started_at
            for receipt in (*authority.dependency_receipts, *authority.input_receipts)
        ):
            raise LiveReceiptProducerError(LiveReceiptProducerCode.GATE_BINDING_INVALID)

    def _now(self) -> datetime:
        try:
            return aware_utc(self._clock())
        except ValueError:
            raise LiveReceiptProducerError(LiveReceiptProducerCode.AUTHORITY_INVALID) from None


def _typed_receipts(receipts: Sequence[SignedLiveReceipt]) -> tuple[SignedLiveReceipt, ...]:
    if isinstance(receipts, (str, bytes)):
        raise TypeError
    typed = tuple(receipts)
    if any(not isinstance(item, SignedLiveReceipt) for item in typed):
        raise TypeError
    if len({item.receipt_id for item in typed}) != len(typed):
        raise ValueError
    return typed


def _gate_id(receipt: SignedLiveReceipt) -> str:
    if not isinstance(receipt.payload, GateReceiptPayload):
        raise TypeError
    return receipt.payload.gate_id


def _expected_input_roles(
    definition: LiveGateDefinition,
    execution_path: GateExecutionPath,
) -> tuple[str, ...]:
    has_path_roles = bool(
        definition.successPathAdditionalInputReceiptRoles
        or definition.failurePathAdditionalInputReceiptRoles
    )
    if has_path_roles and execution_path is GateExecutionPath.STANDARD:
        raise ValueError
    if not has_path_roles and execution_path is not GateExecutionPath.STANDARD:
        raise ValueError
    additional: tuple[str, ...] = ()
    if execution_path is GateExecutionPath.SUCCESS:
        additional = definition.successPathAdditionalInputReceiptRoles
    elif execution_path is GateExecutionPath.FAILURE:
        additional = definition.failurePathAdditionalInputReceiptRoles
    return (*definition.requiredInputReceiptRoles, *additional)


def _all_input_roles(definition: LiveGateDefinition) -> tuple[str, ...]:
    return (
        *definition.requiredInputReceiptRoles,
        *definition.successPathAdditionalInputReceiptRoles,
        *definition.failurePathAdditionalInputReceiptRoles,
    )


def _is_authority_role(role: str) -> bool:
    return "AUTHORITY" in role or "APPROVAL" in role or "PERMIT" in role


def _requires_expected_execution_contract(gate_id: str) -> bool:
    return gate_id in {"SF-L03", "SF-L04", "SF-L05", "SF-L07", "SF-L08"}


__all__ = [
    "GateExecutionEvidence",
    "GateExecutionPath",
    "LiveReceiptProducerCode",
    "LiveReceiptProducerError",
    "TrustedGateAppendAuthority",
    "TrustedLiveReceiptProducer",
]
