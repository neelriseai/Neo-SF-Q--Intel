"""Signed current-candidate local validation evidence; never runs source commands."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipt_ledger import (
    LiveReceiptLedger,
    StoredLiveReceipt,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipts import (
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptScope,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
)
from neo_sf_q_intel.safety import require_safe_repository_locator
from neo_sf_q_intel.temporal import UtcModel, aware_utc

if TYPE_CHECKING:
    from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation

ROLE = "LOCAL_SOURCE_VALIDATION_RECEIPT"
_HEX = r"^[a-f0-9]{64}$"
_VERSION = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_LOCAL_VALIDATION_ARTIFACT_BYTES = 64 * 1024 * 1024


class LocalValidationVerificationError(RuntimeError):
    """Evidence is missing or does not prove the exact current local obligations."""


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalValidationRunnerPins(_Model):
    runner_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    runner_version: str = Field(pattern=_VERSION)
    implementation_sha256: str = Field(pattern=_HEX)
    node_executable_locator: str = Field(min_length=1, max_length=500)
    node_expected_major: int = Field(strict=True, ge=1, le=999)
    node_minimum_version: str = Field(pattern=_VERSION)
    allowed_runner_kinds: tuple[Literal["NODE_TEST", "NODE_GENERATED_CHECK"], ...] = Field(
        min_length=1
    )
    maximum_global_timeout_seconds: int = Field(strict=True, ge=1, le=900)
    maximum_output_bytes: int = Field(strict=True, ge=1024, le=_MAX_OUTPUT_BYTES)

    @model_validator(mode="after")
    def validate_pins(self) -> LocalValidationRunnerPins:
        require_safe_repository_locator(self.node_executable_locator)
        if self.allowed_runner_kinds != tuple(sorted(set(self.allowed_runner_kinds))):
            raise ValueError("Allowed local runners must be sorted and unique")
        if _version(self.node_minimum_version)[0] != self.node_expected_major:
            raise ValueError("Node minimum version must use the pinned major")
        return self


class LocalValidationFileRoot(_Model):
    locator: str = Field(min_length=1, max_length=4096)
    size_bytes: int = Field(strict=True, ge=0, le=_MAX_OUTPUT_BYTES)
    content_sha256: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def validate_locator(self) -> LocalValidationFileRoot:
        require_safe_repository_locator(self.locator)
        return self


class LocalValidationGeneratedOutput(LocalValidationFileRoot):
    content_bytes: bytes = Field(max_length=_MAX_OUTPUT_BYTES)

    @model_validator(mode="after")
    def validate_bytes(self) -> LocalValidationGeneratedOutput:
        if (
            len(self.content_bytes) != self.size_bytes
            or hashlib.sha256(self.content_bytes).hexdigest() != self.content_sha256
        ):
            raise ValueError("Regenerated output bytes differ from their exact source root")
        self.content_bytes.decode("utf-8", "strict")
        return self


class LocalValidationInvocation(_Model):
    test_locator: str = Field(min_length=1, max_length=4096)
    complete: bool = Field(strict=True)
    exit_code: int = Field(strict=True, ge=-2147483648, le=2147483647)
    total_count: int = Field(strict=True, ge=0, le=100000)
    passed_count: int = Field(strict=True, ge=0, le=100000)
    failed_count: int = Field(strict=True, ge=0, le=100000)
    skipped_count: int = Field(strict=True, ge=0, le=100000)
    cancelled_count: int = Field(strict=True, ge=0, le=100000)
    todo_count: int = Field(strict=True, ge=0, le=100000)

    @model_validator(mode="after")
    def validate_counts(self) -> LocalValidationInvocation:
        require_safe_repository_locator(self.test_locator)
        if (
            self.total_count
            != self.passed_count
            + self.failed_count
            + self.skipped_count
            + self.cancelled_count
            + self.todo_count
        ):
            raise ValueError("Local invocation counts are inconsistent")
        return self


class LocalValidationArtifact(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["LOCAL_VALIDATION_EVIDENCE_ONLY"] = "LOCAL_VALIDATION_EVIDENCE_ONLY"
    authorizes_execution: Literal[False] = False
    candidate_bundle_sha256: str = Field(pattern=_HEX)
    candidate_tree_sha256: str = Field(pattern=_HEX)
    obligation_id: str = Field(min_length=1, max_length=200)
    command_contract_sha256: str = Field(pattern=_HEX)
    bound_files: tuple[LocalValidationFileRoot, ...] = Field(min_length=1, max_length=8192)
    runner_kind: Literal["NODE_TEST", "NODE_GENERATED_CHECK"]
    runner_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    runner_version: str = Field(pattern=_VERSION)
    runner_implementation_sha256: str = Field(pattern=_HEX)
    node_executable_locator: str = Field(min_length=1, max_length=500)
    node_version: str = Field(pattern=_VERSION)
    working_directory: str = Field(min_length=1, max_length=4096)
    test_locators: tuple[str, ...] = Field(min_length=1, max_length=64)
    regenerated_outputs: tuple[LocalValidationGeneratedOutput, ...] = Field(max_length=64)
    invocations: tuple[LocalValidationInvocation, ...] = Field(min_length=1, max_length=64)
    started_at: datetime
    terminal_at: datetime
    expires_at: datetime
    result_sha256: str = Field(pattern=_HEX)
    artifact_sha256: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def validate_artifact(self) -> LocalValidationArtifact:
        require_safe_repository_locator(self.node_executable_locator)
        require_safe_repository_locator(self.working_directory)
        for paths in (
            tuple(value.locator for value in self.bound_files),
            self.test_locators,
            tuple(value.locator for value in self.regenerated_outputs),
            tuple(value.test_locator for value in self.invocations),
        ):
            if paths != tuple(sorted(set(paths))):
                raise ValueError("Local artifact paths must be sorted and unique")
        for path in self.test_locators:
            require_safe_repository_locator(path)
        if self.test_locators != tuple(value.test_locator for value in self.invocations):
            raise ValueError("Local result must account for every exact test invocation")
        for timestamp in (self.started_at, self.terminal_at, self.expires_at):
            _utc(timestamp)
        if not self.started_at <= self.terminal_at < self.expires_at:
            raise ValueError("Local artifact time range is invalid")
        if sum(value.size_bytes for value in self.regenerated_outputs) > _MAX_OUTPUT_BYTES:
            raise ValueError("Aggregate local generated output capacity exceeded")
        if self.result_sha256 != stable_sha256(
            [value.model_dump(mode="json") for value in self.invocations]
        ):
            raise ValueError("Sanitized local result root mismatch")
        if self.artifact_sha256 != stable_sha256(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        ):
            raise ValueError("Local validation artifact root mismatch")
        return self


class LocalValidationEvidence(_Model):
    artifact: LocalValidationArtifact
    receipt: SignedLiveReceipt


def serialize_local_validation_artifact(artifact: LocalValidationArtifact) -> bytes:
    """One bounded canonical UTF-8 representation for storage and exact replay."""
    parsed = LocalValidationArtifact.model_validate(artifact.model_dump(mode="python"))
    raw = json.dumps(
        parsed.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(raw) > MAX_LOCAL_VALIDATION_ARTIFACT_BYTES:
        raise ValueError("Local artifact exceeds durable document capacity")
    return raw


class StoredLocalValidationArtifact(_Model):
    artifact_sha256: str = Field(pattern=_HEX)
    document_sha256: str = Field(pattern=_HEX)
    document_bytes: bytes = Field(min_length=2, max_length=MAX_LOCAL_VALIDATION_ARTIFACT_BYTES)
    appended_at: datetime

    @model_validator(mode="after")
    def validate_record(self) -> StoredLocalValidationArtifact:
        _utc(self.appended_at)
        parsed = LocalValidationArtifact.model_validate_json(self.document_bytes)
        if (
            self.artifact_sha256 != parsed.artifact_sha256
            or hashlib.sha256(self.document_bytes).hexdigest() != self.document_sha256
            or serialize_local_validation_artifact(parsed) != self.document_bytes
        ):
            raise ValueError("Stored local artifact differs from its exact canonical bytes")
        return self


class LocalValidationArtifactStore(Protocol):
    """Host-owned immutable storage; it grants no validation or execution authority."""

    def append(self, artifact_document: bytes) -> StoredLocalValidationArtifact: ...

    def read(self, *, artifact_sha256: str) -> StoredLocalValidationArtifact: ...


class VerifiedLocalValidation(_Model):
    obligation_id: str
    command_contract_sha256: str = Field(pattern=_HEX)
    artifact_sha256: str = Field(pattern=_HEX)
    receipt_id: str = Field(pattern=r"^live-receipt:[a-f0-9]{64}$")
    receipt_document_sha256: str = Field(pattern=_HEX)
    artifact_document_sha256: str = Field(pattern=_HEX)
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expiry(self) -> VerifiedLocalValidation:
        _utc(self.expires_at)
        return self


def verify_local_validation_evidence(
    compilation: CandidateTargetCompilation,
    evidence: tuple[LocalValidationEvidence, ...],
    *,
    scope: ReceiptScope,
    registry: TrustedIssuerRegistry,
    ledger: LiveReceiptLedger,
    artifact_store: LocalValidationArtifactStore,
    runner_pins: LocalValidationRunnerPins,
    observed_at: datetime,
) -> tuple[VerifiedLocalValidation, ...]:
    """Replay signed durable proof; source claims or caller booleans are never satisfaction."""
    try:
        observed_at = aware_utc(observed_at)
        if not isinstance(evidence, tuple):
            raise ValueError("Local evidence must use typed immutable pairs")
        pins = LocalValidationRunnerPins.model_validate(runner_pins.model_dump(mode="python"))
        pairs = tuple(
            LocalValidationEvidence.model_validate(value.model_dump(mode="python"))
            for value in evidence
        )
        requirements = {
            value.obligation.obligation_id: value
            for value in compilation.required_local_validations
        }
        ids = tuple(value.artifact.obligation_id for value in pairs)
        if len(set(ids)) != len(ids) or set(ids) != set(requirements):
            raise ValueError("Local evidence obligation set differs from complete expected set")
        if len({value.receipt.receipt_id for value in pairs}) != len(pairs):
            raise ValueError("Local evidence repeats a receipt")
        if (
            scope.candidate_sha256 != compilation.candidate_bundle_sha256
            or scope.project_id != compilation.verified_scope.project_id
            or scope.source_contract_sha256 != compilation.source_contract_sha256
        ):
            raise ValueError("Local evidence scope differs from current candidate")
        current_until = datetime.fromisoformat(
            compilation.verified_scope.valid_until.replace("Z", "+00:00")
        )
        if observed_at >= current_until:
            raise ValueError("Current candidate source capture expired")
        if observed_at >= scope.recovery_deadline:
            raise ValueError("Host campaign authority expired")
        if len({value.artifact.expires_at for value in pairs}) > 1:
            raise ValueError("Local batch requires one common host-bounded expiry")
        stored_records = ledger.replay(campaign_id=scope.campaign_id)
        verified = []
        output_bytes = 0
        for pair in pairs:
            artifact, receipt = pair.artifact, pair.receipt
            required = requirements[artifact.obligation_id]
            obligation = required.obligation
            if (
                artifact.candidate_bundle_sha256 != compilation.candidate_bundle_sha256
                or artifact.candidate_tree_sha256 != required.candidate_tree_sha256
                or artifact.command_contract_sha256 != required.command_contract_sha256
                or tuple(value.model_dump() for value in artifact.bound_files)
                != tuple(value.model_dump() for value in required.bound_files)
                or artifact.runner_kind != obligation.runner_kind
                or artifact.runner_kind not in pins.allowed_runner_kinds
                or artifact.runner_id != pins.runner_id
                or artifact.runner_version != pins.runner_version
                or artifact.runner_implementation_sha256 != pins.implementation_sha256
                or artifact.node_executable_locator != pins.node_executable_locator
                or _version(artifact.node_version)[0] != pins.node_expected_major
                or _version(artifact.node_version) < _version(pins.node_minimum_version)
                or artifact.working_directory != obligation.working_directory
                or artifact.test_locators != obligation.test_locators
            ):
                raise ValueError("Local result does not match its exact source/runner obligation")
            if (
                artifact.started_at > observed_at
                or artifact.terminal_at > observed_at
                or artifact.expires_at <= observed_at
                or artifact.expires_at > current_until
                or artifact.expires_at > scope.recovery_deadline
                or (artifact.terminal_at - artifact.started_at).total_seconds()
                > min(obligation.maximum_seconds, pins.maximum_global_timeout_seconds)
            ):
                raise ValueError("Local result is stale, future or outside timeout")
            if any(
                not item.complete
                or item.exit_code != 0
                or item.total_count < 1
                or item.total_count != item.passed_count
                for item in artifact.invocations
            ):
                raise ValueError("Local result is failed, incomplete, skipped or empty")
            output_bytes += len(
                json.dumps(
                    [value.model_dump(mode="json") for value in artifact.invocations],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if (
                tuple(value.locator for value in artifact.regenerated_outputs)
                != obligation.regenerated_locators
            ):
                raise ValueError("Local generated output set is incomplete or extra")
            roots = {value.locator: value for value in artifact.bound_files}
            for output in artifact.regenerated_outputs:
                root = roots.get(output.locator)
                if root is None or (root.size_bytes, root.content_sha256) != (
                    output.size_bytes,
                    output.content_sha256,
                ):
                    raise ValueError("Regeneration does not match current candidate output bytes")
                output_bytes += output.size_bytes
            payload = receipt.payload
            if (
                not isinstance(payload, SupportingReceiptPayload)
                or receipt.receipt_role != ROLE
                or payload.scope != scope
                or payload.provenance is not ReceiptProvenance.PRODUCT_OWNED
                or payload.outcome is not ReceiptOutcome.PASSED
                or payload.evidence_phase is not None
                or payload.authorized_gate_ids
                or payload.authorized_effect_classes
                or payload.recovery_permit is not None
                or payload.artifact_sha256 != artifact.artifact_sha256
                or payload.issued_at != artifact.started_at
                or payload.terminal_at != artifact.terminal_at
                or payload.expires_at != artifact.expires_at
                or registry.verify(
                    receipt, expected_issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION
                )
                is not None
            ):
                raise ValueError(
                    "Local supporting receipt is not current trusted execution evidence"
                )
            exact = serialize_live_receipt(receipt)
            matches = [value for value in stored_records if value.receipt_id == receipt.receipt_id]
            if len(matches) != 1:
                raise ValueError("Exactly one durable local receipt is required")
            stored = StoredLiveReceipt.model_validate(matches[0].model_dump(mode="python"))
            if (
                stored.receipt_document != exact
                or stored.appended_at > observed_at
                or stored.appended_at < artifact.terminal_at
            ):
                raise ValueError("Durable local receipt bytes/time differ from signed evidence")
            stored_artifact = StoredLocalValidationArtifact.model_validate(
                artifact_store.read(artifact_sha256=artifact.artifact_sha256).model_dump(
                    mode="python"
                )
            )
            if (
                stored_artifact.document_bytes != serialize_local_validation_artifact(artifact)
                or stored_artifact.appended_at > observed_at
                or stored_artifact.appended_at < artifact.terminal_at
            ):
                raise ValueError("Durable local artifact bytes/time differ from signed evidence")
            verified.append(
                VerifiedLocalValidation(
                    obligation_id=artifact.obligation_id,
                    command_contract_sha256=artifact.command_contract_sha256,
                    artifact_sha256=artifact.artifact_sha256,
                    receipt_id=receipt.receipt_id,
                    receipt_document_sha256=hashlib.sha256(exact).hexdigest(),
                    artifact_document_sha256=stored_artifact.document_sha256,
                    expires_at=artifact.expires_at,
                )
            )
        if output_bytes > pins.maximum_output_bytes:
            raise ValueError("Global local output capacity exceeded")
        if (
            pairs
            and (
                max(value.artifact.terminal_at for value in pairs)
                - min(value.artifact.started_at for value in pairs)
            ).total_seconds()
            > pins.maximum_global_timeout_seconds
        ):
            raise ValueError("Global local validation timeout exceeded")
        return tuple(sorted(verified, key=lambda value: value.obligation_id))
    except Exception as exc:
        raise LocalValidationVerificationError(
            "Local validation evidence does not satisfy current signed durable obligations"
        ) from exc


def _version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(_VERSION, value):
        raise ValueError("Node/runner version must be exact semantic version")
    parts = tuple(int(part) for part in value.split("."))
    return parts[0], parts[1], parts[2]


def _utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("Local evidence requires UTC timestamps")
