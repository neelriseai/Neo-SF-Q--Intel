"""Host-only enrollment review; never install config, issue receipts, or run baseline.

Only host composition constructs this service. Its capture callback is the configured
baseline service's zero-argument capture_compilation, not a caller-supplied document.
The sole operation input is a one-use, in-process confirmation made by this instance.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator

from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation
from neo_sf_q_intel.classification_bootstrap import (
    CLASSIFICATION_OPERATION_PLAN_SHA256,
    ClassificationAuthority,
    ClassificationError,
    ClassificationPins,
    _capture_classification_pins,
)
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_baseline import HostBaselineConfiguration
from neo_sf_q_intel.live_read_evidence import (
    _CLI_VERSION_OUTPUT,
    CliInvocation,
    CliRunner,
    SubprocessCliRunner,
    _read_nofollow,
    _strict_json,
)
from neo_sf_q_intel.live_receipts import parse_pinned_profile
from neo_sf_q_intel.live_target_plan import LiveTargetPolicy
from neo_sf_q_intel.temporal import UtcModel, aware_utc

OPERATOR_CONFIRMATION = "CONFIRM_FIXED_SYSTEM_CLASSIFICATION_FOR_REVIEW_ONLY"
_PURPOSE = "PREPARE_NONPRODUCTION_BASELINE_ENROLLMENT_REVIEW"
_HEX = r"^[a-f0-9]{64}$"
_Digest = Annotated[str, Field(pattern=_HEX)]
_VALIDITY_SECONDS = 300
_MAXIMUM_BYTES = 262144
_CHECKLIST = (
    "Independently verify the fixed alias, nonproduction class and hashed org/actor identity.",
    "Review every source, candidate, policy, profile and implementation identity and expiry.",
    "Review the complete source-derived target policy; default-deny scope grants no targets.",
    "Resolve local-validation obligations or separately review their exact baseline phase policy.",
    "Independently provision distinct host/product receipt issuers and private keys locally.",
    "Only after independent task authorization, save exact draft_configuration UTF-8 JSON to the "
    "configured private LIVE_BASELINE_CONFIG_PATH and pin its file SHA-256.",
    "Review/pin LIVE_TARGET_POLICY_SHA256 separately. A proposal or installed draft alone never "
    "enables baseline; LIVE_BASELINE_ENABLED remains false until separately authorized.",
    "Restart through the host lifecycle and require fresh classification before dependent work. "
    "No proposal satisfies a live gate, candidate/restore proof or release decision.",
)


class EnrollmentProposalError(RuntimeError):
    """Sanitized refusal; raw CLI payloads and settings are never exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class EnrollmentSourceIdentity(_Model):
    compilation_sha256: str = Field(pattern=_HEX)
    candidate_bundle_sha256: str = Field(pattern=_HEX)
    source_contract_sha256: str = Field(pattern=_HEX)
    source_snapshot_sha256: str = Field(pattern=_HEX)
    verified_change_sha256: str = Field(pattern=_HEX)
    semantic_graph_sha256: str = Field(pattern=_HEX)
    source_profile_sha256: str = Field(pattern=_HEX)
    ontology_sha256: str = Field(pattern=_HEX)
    source_operation_profile_bytes_sha256: str = Field(pattern=_HEX)
    candidate_tree_sha256s: tuple[_Digest, ...] = Field(max_length=4096)


class BaselineEnrollmentProposal(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_class: Literal["REVIEW_PROPOSAL_ONLY"] = "REVIEW_PROPOSAL_ONLY"
    authorizes_execution: Literal[False] = Field(default=False, alias="authorizesExecution")
    operator_review_required: Literal[True] = True
    issued_at: datetime
    expires_at: datetime
    classification_pins: ClassificationPins
    classification_operation_plan_sha256: str = Field(pattern=_HEX)
    source: EnrollmentSourceIdentity
    acceptance_profile_sha256: str = Field(pattern=_HEX)
    acceptance_profile_bytes_sha256: str = Field(pattern=_HEX)
    target_policy_sha256: str = Field(pattern=_HEX)
    target_policy_bytes_sha256: str = Field(pattern=_HEX)
    target_policy_implementation_sha256: str = Field(pattern=_HEX)
    proposal_implementation_sha256: str = Field(pattern=_HEX)
    installed_salesforce_cli_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    unresolved_local_obligation_sha256s: tuple[_Digest, ...] = Field(max_length=4096)
    configuration_gaps: tuple[Literal["TARGET_POLICY_EXTERNAL_PIN_REQUIRED"], ...]
    draft_configuration: HostBaselineConfiguration
    operator_checklist: tuple[str, ...]
    proposal_sha256: str = Field(pattern=_HEX)

    @model_validator(mode="after")
    def validate_proposal(self) -> BaselineEnrollmentProposal:
        config = self.draft_configuration
        pins = self.classification_pins
        if (
            self.issued_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or not self.issued_at < self.expires_at
            or (self.expires_at - self.issued_at).total_seconds() > _VALIDITY_SECONDS
            or self.classification_operation_plan_sha256 != CLASSIFICATION_OPERATION_PLAN_SHA256
            or config.issued_at != self.issued_at
            or config.valid_until != self.expires_at
            or config.live_read.classification_authority.issued_at != self.issued_at
            or config.live_read.classification_authority.expires_at != self.expires_at
            or config.live_read.classification_authority.pins_sha256 != pins.pins_sha256
            or config.live_read.salesforce_cli_version != self.installed_salesforce_cli_version
            or any(
                getattr(config.live_read, key) != value
                for key, value in pins.model_dump(exclude={"api_version"}).items()
            )
            or config.live_read.api_version != pins.api_version.removeprefix("v")
            or config.local_validation_phase_policy is not None
            or self.operator_checklist != _CHECKLIST
            or self.proposal_sha256
            != stable_sha256(
                self.model_dump(mode="json", by_alias=True, exclude={"proposal_sha256"})
            )
        ):
            raise ValueError("ENROLLMENT_PROPOSAL_INVALID")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class EnrollmentOperatorConfirmation:
    """An opaque capability, intentionally not an API/Pydantic/JSON contract."""

    alias: str
    task_authority_sha256: str
    issued_at: datetime
    expires_at: datetime
    host_settings_sha256: str
    purpose: Literal["PREPARE_NONPRODUCTION_BASELINE_ENROLLMENT_REVIEW"] = _PURPOSE
    operation_plan_sha256: str = CLASSIFICATION_OPERATION_PLAN_SHA256
    local_tool_operation: tuple[str, ...] = ("--version",)
    permits_application_access: Literal[False] = False
    permits_writes: Literal[False] = False
    _owner: object = field(default_factory=object)

    def __reduce_ex__(self, protocol: int):
        raise TypeError("ENROLLMENT_CONFIRMATION_IS_PROCESS_LOCAL")


def enrollment_proposal_implementation_sha256() -> str:
    """Bind the installed parser, broker, config and supporting contract implementation."""
    modules = (
        "baseline_enrollment_proposal.py",
        "candidate_target_compiler.py",
        "classification_bootstrap.py",
        "config.py",
        "live_baseline.py",
        "live_read_evidence.py",
        "live_receipts.py",
        "live_target_plan.py",
        "temporal.py",
    )
    root = Path(__file__).parent
    return stable_sha256(
        {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in modules}
    )


class HostBaselineEnrollmentProposalService:
    """Trusted host composition only. Construction/confirmation perform no subprocess."""

    def __init__(
        self,
        settings: Settings,
        repository_root: Path,
        *,
        capture_compilation: Callable[[], CandidateTargetCompilation],
        runner: CliRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._root = repository_root
        self._capture_compilation = capture_compilation
        self._runner = runner if runner is not None else SubprocessCliRunner()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._owner = object()
        self._lock = threading.Lock()
        self._pending: EnrollmentOperatorConfirmation | None = None

    def confirm_classification_only(
        self, *, operator_confirmation: str, task_authority_sha256: str
    ) -> EnrollmentOperatorConfirmation:
        """Host operator explicitly confirms only version + fixed system identity reads."""
        if (
            operator_confirmation != OPERATOR_CONFIRMATION
            or not isinstance(task_authority_sha256, str)
            or not re.fullmatch(_HEX, task_authority_sha256)
        ):
            raise EnrollmentProposalError("ENROLLMENT_OPERATOR_CONFIRMATION_REQUIRED")
        try:
            alias = self._settings.require_operator_alias()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", alias):
                raise ValueError
            now = self._now()
            token = EnrollmentOperatorConfirmation(
                alias=alias,
                task_authority_sha256=task_authority_sha256,
                issued_at=now,
                expires_at=now + timedelta(seconds=_VALIDITY_SECONDS),
                host_settings_sha256=self._settings_identity(),
                _owner=self._owner,
            )
        except Exception:
            raise EnrollmentProposalError("ENROLLMENT_HOST_CONFIGURATION_INVALID") from None
        with self._lock:
            self._pending = token
        return token

    def propose(self, confirmation: EnrollmentOperatorConfirmation) -> BaselineEnrollmentProposal:
        """One fixed task; no caller alias, query, route, path, operation or scope input."""
        with self._lock:
            if (
                type(confirmation) is not EnrollmentOperatorConfirmation
                or confirmation is not self._pending
            ):
                raise EnrollmentProposalError("ENROLLMENT_OPERATOR_CONFIRMATION_REQUIRED")
            self._pending = None  # Consume before capture/dispatch, including every failure path.
        try:
            self._require_current(confirmation)
            compilation = self._capture_compilation()
            compilation = CandidateTargetCompilation.model_validate(
                compilation.model_dump(mode="python")
            )
            if set(compilation.blocking_reason_codes) - {"LOCAL_SOURCE_VALIDATION_REQUIRED"}:
                raise EnrollmentProposalError("ENROLLMENT_SOURCE_BLOCKED")
            profile, profile_bytes, policy, policy_bytes = self._policy_snapshot()
            expires = min(
                confirmation.expires_at,
                datetime.fromisoformat(
                    compilation.verified_scope.valid_until.replace("Z", "+00:00")
                ),
                datetime.fromisoformat(policy.valid_until.replace("Z", "+00:00")),
            )
            implementation = enrollment_proposal_implementation_sha256()

            def current() -> None:
                self._require_current(confirmation)
                if self._now() >= expires:
                    raise EnrollmentProposalError("ENROLLMENT_PROPOSAL_EXPIRED")
                if self._policy_snapshot() != (profile, profile_bytes, policy, policy_bytes):
                    raise EnrollmentProposalError("ENROLLMENT_POLICY_CHANGED")
                if enrollment_proposal_implementation_sha256() != implementation:
                    raise EnrollmentProposalError("ENROLLMENT_IMPLEMENTATION_CHANGED")

            def invoke(arguments: tuple[str, ...], maximum: int) -> bytes:
                current()
                completed = self._runner.run(
                    CliInvocation(
                        arguments, min(30, (expires - self._now()).total_seconds()), maximum
                    )
                )
                current()
                if not completed.quiescent:
                    raise EnrollmentProposalError("ENROLLMENT_PROCESS_NOT_QUIESCENT")
                if completed.timed_out:
                    raise EnrollmentProposalError("ENROLLMENT_TIMEOUT")
                if completed.output_exceeded or len(completed.stdout) > maximum:
                    raise EnrollmentProposalError("ENROLLMENT_OUTPUT_LIMIT")
                if completed.returncode != 0:
                    raise EnrollmentProposalError("ENROLLMENT_COMMAND_FAILED")
                return completed.stdout

            version_output = invoke(("--version",), 1024)
            match = _CLI_VERSION_OUTPUT.match(version_output)
            if match is None:
                raise EnrollmentProposalError("ENROLLMENT_CLI_VERSION_INVALID")
            version = match.group(1).decode("ascii")
            if tuple(map(int, version.split("."))) < tuple(
                map(int, self._settings.sf_min_cli_version.split("."))
            ):
                raise EnrollmentProposalError("ENROLLMENT_CLI_VERSION_UNSUPPORTED")

            def call(arguments: tuple[str, ...]) -> dict[str, Any]:
                payload = _strict_json(invoke(arguments, _MAXIMUM_BYTES))
                if type(payload.get("status")) is not int or payload["status"] != 0:
                    raise ClassificationError("CLASSIFICATION_RESPONSE_INVALID")
                return payload

            pins, _ = _capture_classification_pins(alias=confirmation.alias, call=call)
            current()
            if pins.environment_class not in policy.allowed_environment_classes:
                raise EnrollmentProposalError("ENROLLMENT_ENVIRONMENT_DENIED")
            classification = ClassificationAuthority(
                schema_version="1.0.0",
                authority_class="FIXED_SYSTEM_CLASSIFICATION_ONLY",
                task_authority_sha256=confirmation.task_authority_sha256,
                pins_sha256=pins.pins_sha256,
                operation_plan_sha256=CLASSIFICATION_OPERATION_PLAN_SHA256,
                issued_at=confirmation.issued_at,
                expires_at=expires,
                maximum_response_bytes=_MAXIMUM_BYTES,
            )
            configuration = HostBaselineConfiguration.model_validate(
                {
                    "schema_version": "1.0.0",
                    "authority_class": "FIXED_READ_ONLY_BASELINE",
                    "task_authority_sha256": confirmation.task_authority_sha256,
                    "issued_at": confirmation.issued_at,
                    "valid_until": expires,
                    "maximum_campaign_seconds": int(
                        (expires - confirmation.issued_at).total_seconds()
                    ),
                    "maximum_bootstrap_bytes": _MAXIMUM_BYTES,
                    "live_read": {
                        **pins.model_dump(),
                        "classification_authority": classification,
                        "salesforce_cli_version": version,
                        "assertion_producer_id": self._settings.live_product_receipt_issuer_id
                        or "product-receipt-issuer",
                    },
                }
            )
            scope = compilation.verified_scope
            source = EnrollmentSourceIdentity(
                compilation_sha256=compilation.compilation_sha256,
                candidate_bundle_sha256=compilation.candidate_bundle_sha256,
                source_contract_sha256=compilation.source_contract_sha256,
                source_snapshot_sha256=scope.source_snapshot_sha256,
                verified_change_sha256=scope.verified_change_sha256,
                semantic_graph_sha256=scope.semantic_graph_sha256,
                source_profile_sha256=scope.source_profile_sha256,
                ontology_sha256=scope.ontology_sha256,
                source_operation_profile_bytes_sha256=hashlib.sha256(
                    compilation.source_operation_profile_bytes
                ).hexdigest(),
                candidate_tree_sha256s=tuple(
                    sorted(
                        {
                            item.candidate_tree_sha256
                            for item in compilation.required_local_validations
                        }
                    )
                ),
            )
            body = BaselineEnrollmentProposal.model_construct(
                issued_at=confirmation.issued_at,
                expires_at=expires,
                classification_pins=pins,
                classification_operation_plan_sha256=CLASSIFICATION_OPERATION_PLAN_SHA256,
                source=source,
                acceptance_profile_sha256=profile,
                acceptance_profile_bytes_sha256=profile_bytes,
                target_policy_sha256=policy.sha256,
                target_policy_bytes_sha256=policy_bytes,
                target_policy_implementation_sha256=policy.producer_implementation_sha256,
                proposal_implementation_sha256=implementation,
                installed_salesforce_cli_version=version,
                unresolved_local_obligation_sha256s=tuple(
                    sorted(
                        stable_sha256(item.model_dump(mode="json"))
                        for item in compilation.required_local_validations
                    )
                ),
                configuration_gaps=(
                    ("TARGET_POLICY_EXTERNAL_PIN_REQUIRED",)
                    if self._settings.live_target_policy_sha256 is None
                    else ()
                ),
                draft_configuration=configuration,
                operator_checklist=_CHECKLIST,
            ).model_dump(mode="json", by_alias=True)
            body["proposal_sha256"] = stable_sha256(body)
            proposal = BaselineEnrollmentProposal.model_validate(body)
            current()
            return proposal
        except (EnrollmentProposalError, ClassificationError):
            raise
        except Exception:
            raise EnrollmentProposalError("ENROLLMENT_PROPOSAL_FAILED") from None

    def _now(self) -> datetime:
        try:
            return aware_utc(self._clock())
        except ValueError:
            raise EnrollmentProposalError("ENROLLMENT_CLOCK_INVALID") from None

    def _settings_identity(self) -> str:
        # Exact relevant host settings only. Secret values never enter this hash or output.
        return stable_sha256(
            {
                name: str(getattr(self._settings, name))
                for name in (
                    "sf_operator_alias",
                    "sf_min_cli_version",
                    "live_acceptance_profile_path",
                    "live_acceptance_profile_sha256",
                    "live_target_policy_path",
                    "live_target_policy_sha256",
                    "live_product_receipt_issuer_id",
                    "salesforce_repository_root",
                    "salesforce_app_root",
                )
            }
        )

    def _require_current(self, confirmation: EnrollmentOperatorConfirmation) -> None:
        if (
            confirmation._owner is not self._owner
            or confirmation.purpose != _PURPOSE
            or confirmation.operation_plan_sha256 != CLASSIFICATION_OPERATION_PLAN_SHA256
            or confirmation.local_tool_operation != ("--version",)
            or confirmation.permits_application_access is not False
            or confirmation.permits_writes is not False
            or confirmation.alias != self._settings.require_operator_alias()
            or confirmation.host_settings_sha256 != self._settings_identity()
            or not confirmation.issued_at <= self._now() < confirmation.expires_at
        ):
            raise EnrollmentProposalError("ENROLLMENT_CONFIRMATION_INVALID_OR_EXPIRED")

    def _policy_snapshot(self) -> tuple[str, str, LiveTargetPolicy, str]:
        settings = self._settings
        profile_bytes = _read_nofollow(
            settings.resolved_live_acceptance_profile_path(self._root), maximum_bytes=2097152
        )
        profile = parse_pinned_profile(
            _strict_json(profile_bytes),
            expected_profile_sha256=settings.require_live_acceptance_profile_sha256(),
        )
        policy_bytes = _read_nofollow(
            settings._resolved_repository_path(settings.live_target_policy_path, self._root),
            maximum_bytes=2097152,
        )
        policy_sha = hashlib.sha256(policy_bytes).hexdigest()
        if settings.live_target_policy_sha256 not in (None, policy_sha):
            raise EnrollmentProposalError("ENROLLMENT_POLICY_PIN_MISMATCH")
        policy = LiveTargetPolicy.model_validate(_strict_json(policy_bytes))
        if policy.acceptance_profile_sha256 != profile.profile_sha256:
            raise EnrollmentProposalError("ENROLLMENT_POLICY_PROFILE_MISMATCH")
        return profile.profile_sha256, hashlib.sha256(profile_bytes).hexdigest(), policy, policy_sha


def serialize_baseline_enrollment_proposal(
    proposal: BaselineEnrollmentProposal, *, observed_at: datetime
) -> bytes:
    """Pure sanitized wrapper serialization, not an install/enable operation."""
    proposal = BaselineEnrollmentProposal.model_validate(proposal.model_dump(mode="python"))
    try:
        observed_at = aware_utc(observed_at)
    except ValueError:
        raise EnrollmentProposalError("ENROLLMENT_PROPOSAL_EXPIRED") from None
    if not proposal.issued_at <= observed_at < proposal.expires_at:
        raise EnrollmentProposalError("ENROLLMENT_PROPOSAL_EXPIRED")
    return json.dumps(
        proposal.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
