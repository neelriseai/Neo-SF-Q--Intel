"""Host-owned, fixed-scope SF-L01–L05 composition; no caller execution parameters.

The source describes facts, the independently pinned host configuration/policy grants a
bounded read-only task, and this broker alone handles transient identity/dataset values.
API and MCP use the same zero-argument service. No raw org results leave this boundary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import ConfigDict, Field, model_validator

from neo_sf_q_intel.candidate_assurance import CandidateAssuranceBundle
from neo_sf_q_intel.candidate_target_compiler import (
    CandidateTargetCompilation,
    ExpectedExecutionContract,
    ExpectedRunnerProvenance,
    HostOrganizationClassification,
    HostOwnedSourceContractPort,
    ProductionCandidateTargetCompiler,
    compose_candidate_live_plan,
)
from neo_sf_q_intel.classification_bootstrap import ClassificationError, capture_classified_identity
from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import (
    AssertionOutcome,
    AssertionSubjectKind,
    ExecutionAssertion,
    ExecutionAssertionArtifact,
    ExecutionAssertionStore,
    ExecutionAssertionStoreSelection,
    ExecutionToolVersion,
    StoredExecutionAssertion,
    TrustedExecutionRunnerKey,
    build_execution_artifact_reference,
    execution_artifact_index_sha256,
    open_execution_assertion_store,
    replay_execution_assertion,
    serialize_execution_assertion,
    sign_execution_assertion,
)
from neo_sf_q_intel.live_campaign_status import _load_pinned_profile
from neo_sf_q_intel.live_read_evidence import (
    _CLI_VERSION_OUTPUT,
    CliInvocation,
    CliRunner,
    HostLiveReadCapture,
    HostLiveReadConfig,
    HostOwnedLiveReadExecutor,
    LiveReadError,
    LiveReadResult,
    ResolvedDatasetScope,
    ResolvedTargetVariables,
    ResolvedVariableRow,
    SubprocessCliRunner,
    _canonical_bytes,
    _checked_identity,
    _classification_pins,
    _dataset_scope_digest,
    _digest,
    _metadata_target,
    _read_nofollow,
    _safe_process_arguments,
    _strict_json,
)
from neo_sf_q_intel.live_receipt_ledger import (
    LiveReceiptLedgerSelection,
    open_live_receipt_ledger,
    serialize_live_receipt,
)
from neo_sf_q_intel.live_receipt_producer import GateExecutionEvidence, TrustedLiveReceiptProducer
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    HostEnrollmentBinding,
    PinnedLiveAcceptanceProfile,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptScope,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    sign_live_receipt,
    validate_live_campaign,
)
from neo_sf_q_intel.live_target_plan import LiveTargetPolicy, PlannedTarget, TargetPartition
from neo_sf_q_intel.local_validation import ROLE as LOCAL_VALIDATION_RECEIPT_ROLE
from neo_sf_q_intel.local_validation import (
    LocalValidationEvidence,
    LocalValidationRunnerPins,
    LocalValidationVerificationError,
    verify_local_validation_evidence,
)
from neo_sf_q_intel.local_validation_phase import (
    READ_ONLY_BASELINE_GATES,
    HostLocalValidationPhasePolicy,
    LocalValidationPhaseError,
    classify_local_validation_phase,
)
from neo_sf_q_intel.local_validation_runner import (
    HostLocalValidationService,
    LocalValidationRunError,
    SQLiteLocalValidationArtifactStore,
)
from neo_sf_q_intel.ontology import SourceGraphProfile
from neo_sf_q_intel.postgres_schema import (
    DEFAULT_POSTGRES_SCHEMA,
    initialize_postgres_schema,
    scoped_connection_string,
)
from neo_sf_q_intel.temporal import UtcModel, aware_utc

_HEX = r"^[a-f0-9]{64}$"
_API_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
_SF_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")
_SUPPORT_ROLES = frozenset(
    {
        "LIVE_TARGET_PLAN_RECEIPT",
        "LIVE_DATASET_SCOPE_RECEIPT",
        "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
    }
)
_READ_GATES = frozenset({"SF-L03", "SF-L04", "SF-L05"})
_BASELINE_GATES = frozenset({"SF-L01", "SF-L02", *_READ_GATES})
_LOGGER = logging.getLogger("neo_sf_q_intel.live_baseline")


class LiveBaselineError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostBaselineConfiguration(_Model):
    schema_version: Literal["1.0.0"]
    authority_class: Literal["FIXED_READ_ONLY_BASELINE"]
    task_authority_sha256: str = Field(pattern=_HEX)
    issued_at: datetime
    valid_until: datetime
    live_read: HostLiveReadConfig
    maximum_campaign_seconds: int = Field(ge=1, le=3600)
    maximum_bootstrap_bytes: int = Field(ge=1024, le=1048576)
    local_validation_phase_policy: HostLocalValidationPhasePolicy | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_validity(self) -> HostBaselineConfiguration:
        if (
            self.issued_at.tzinfo is None
            or self.valid_until.tzinfo is None
            or self.issued_at >= self.valid_until
            or self.live_read.classification_authority.task_authority_sha256
            != self.task_authority_sha256
        ):
            raise ValueError("Host baseline authority requires an explicit bounded validity")
        return self


class LiveBaselineResult(_Model):
    campaign_id: str | None = Field(default=None, pattern=r"^baseline:[a-f0-9]{32}$")
    state: Literal["BLOCKED", "IN_PROGRESS", "COMPLETED"]
    release_eligible: Literal[False] = False
    evidence_completeness: Literal["INCOMPLETE"] = "INCOMPLETE"
    ledger_mode: Literal["POSTGRESQL", "SQLITE", "UNAVAILABLE"]
    assertion_store_mode: Literal["POSTGRESQL", "SQLITE", "UNAVAILABLE"]
    gap_codes: tuple[str, ...]
    degradation_codes: tuple[str, ...] = ()
    read_result: LiveReadResult | None = None

    @model_validator(mode="after")
    def validate_result(self) -> LiveBaselineResult:
        if len(self.gap_codes) + len(self.degradation_codes) > 32 or any(
            not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", item)
            for item in (*self.gap_codes, *self.degradation_codes)
        ):
            raise ValueError("Baseline result requires bounded sanitized codes")
        if (self.state == "COMPLETED") != (self.read_result is not None):
            raise ValueError("Only completed baseline results carry read evidence")
        if self.state in {"COMPLETED", "IN_PROGRESS"} and self.campaign_id is None:
            raise ValueError("Active baseline results require a durable campaign identity")
        return self


class _BaselineEvidenceIndex(_Model):
    """Internal replay material; never accepted from or returned to callers."""

    expected_contract_json: str = Field(min_length=2, max_length=2097152)
    assertion_artifact_ids: dict[str, str]

    @model_validator(mode="after")
    def validate_index(self) -> _BaselineEvidenceIndex:
        if set(self.assertion_artifact_ids) != _BASELINE_GATES or any(
            not re.fullmatch(r"execution-assertion:[a-f0-9]{64}", value)
            for value in self.assertion_artifact_ids.values()
        ):
            raise ValueError("Baseline replay needs all five exact assertion artifacts")
        return self


@dataclass
class _RecordingAssertionStore:
    delegate: ExecutionAssertionStore
    campaign_id: str
    profile: PinnedLiveAcceptanceProfile
    recorded: dict[str, str] = field(default_factory=dict)

    def append(self, document: bytes) -> StoredExecutionAssertion:
        result = self.delegate.append(document)
        if result.campaign_id != self.campaign_id or result.gate_id not in _BASELINE_GATES:
            raise LiveBaselineError("LIVE_BASELINE_ASSERTION_SCOPE_MISMATCH")
        prior = self.recorded.setdefault(result.gate_id, result.assertion_artifact_id)
        if prior != result.assertion_artifact_id:
            raise LiveBaselineError("LIVE_BASELINE_ASSERTION_CONFLICT")
        artifact = result.parsed_artifact()
        _LOGGER.info(
            _canonical_bytes(
                {
                    "event": "live_baseline_stage",
                    "campaign_id": self.campaign_id,
                    "capability_ids": self.profile.profile.gates_by_id[
                        result.gate_id
                    ].capabilityIds,
                    "stage": result.gate_id,
                    "status": "ASSERTION_DURABLE",
                    "runner_id": artifact.runner_id,
                    "input_evidence_ids": (artifact.expected_contract_bytes_sha256,),
                    "output_artifact_ids": (result.assertion_artifact_id,),
                    "policy_sha256": artifact.scope.policy_sha256,
                    "profile_sha256": artifact.scope.profile_sha256,
                    "duration_ms": max(
                        0, int((artifact.terminal_at - artifact.started_at).total_seconds() * 1000)
                    ),
                    "gap_codes": artifact.gap_codes,
                }
            ).decode()
        )
        return result

    def get(self, artifact_id: str) -> StoredExecutionAssertion:
        return self.delegate.get(artifact_id)


@dataclass(frozen=True, repr=False)
class BaselineCampaignStore:
    """A durable one-shot claim; abandoned work is never silently redispatched."""

    mode: str
    sqlite_path: Path
    database_url: str | None = None
    postgres_schema: str = DEFAULT_POSTGRES_SCHEMA

    @contextmanager
    def connection(self):
        if self.mode == "POSTGRESQL" and self.database_url:
            with psycopg.connect(
                scoped_connection_string(self.database_url, self.postgres_schema),
                row_factory=dict_row,
            ) as connection:
                yield connection
        elif self.mode == "SQLITE":
            with closing(sqlite3.connect(self.sqlite_path, timeout=10)) as connection:
                connection.row_factory = sqlite3.Row
                with connection:
                    yield connection
        else:
            raise LiveBaselineError("LIVE_BASELINE_STORAGE_UNAVAILABLE")

    def setup(self) -> None:
        if self.mode == "POSTGRESQL" and self.database_url:
            with psycopg.connect(self.database_url) as connection:
                initialize_postgres_schema(connection, self.postgres_schema)
        else:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS live_baseline_campaigns (
                request_sha256 TEXT PRIMARY KEY, campaign_id TEXT NOT NULL UNIQUE,
                result_document TEXT, result_sha256 TEXT,
                CHECK ((result_document IS NULL) = (result_sha256 IS NULL)))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS live_baseline_evidence (
                campaign_id TEXT PRIMARY KEY,
                evidence_document TEXT NOT NULL, evidence_sha256 TEXT NOT NULL)""")

    def save_evidence(self, campaign_id: str, evidence: _BaselineEvidenceIndex) -> None:
        document = _canonical_bytes(evidence.model_dump(mode="json")).decode()
        digest = _digest(document)
        placeholder = "%s" if self.mode == "POSTGRESQL" else "?"
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO live_baseline_evidence "
                f"VALUES ({placeholder},{placeholder},{placeholder}) "
                "ON CONFLICT (campaign_id) DO NOTHING",
                (campaign_id, document, digest),
            )
        if self.load_evidence(campaign_id) != evidence:
            raise LiveBaselineError("LIVE_BASELINE_REPLAY_CORRUPT")

    def load_evidence(self, campaign_id: str) -> _BaselineEvidenceIndex:
        placeholder = "%s" if self.mode == "POSTGRESQL" else "?"
        with self.connection() as connection:
            row = connection.execute(
                f"SELECT * FROM live_baseline_evidence WHERE campaign_id={placeholder}",
                (campaign_id,),
            ).fetchone()
            if row is None or _digest(row["evidence_document"]) != row["evidence_sha256"]:
                raise LiveBaselineError("LIVE_BASELINE_REPLAY_CORRUPT")
            return _BaselineEvidenceIndex.model_validate_json(row["evidence_document"])

    def claim(
        self, request_sha256: str, campaign_id: str
    ) -> tuple[bool, str, LiveBaselineResult | None]:
        placeholder = "%s" if self.mode == "POSTGRESQL" else "?"
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO live_baseline_campaigns (request_sha256,campaign_id) "
                f"VALUES ({placeholder},{placeholder}) "
                "ON CONFLICT (request_sha256) DO NOTHING",
                (request_sha256, campaign_id),
            )
            inserted = cursor.rowcount == 1
            row = connection.execute(
                f"SELECT * FROM live_baseline_campaigns WHERE request_sha256={placeholder}",
                (request_sha256,),
            ).fetchone()
            if row is None:
                raise LiveBaselineError("LIVE_BASELINE_CLAIM_FAILED")
            result = None
            if row["result_document"] is not None:
                document = row["result_document"].encode()
                if hashlib.sha256(document).hexdigest() != row["result_sha256"]:
                    raise LiveBaselineError("LIVE_BASELINE_REPLAY_CORRUPT")
                result = LiveBaselineResult.model_validate_json(document)
                if result.campaign_id != row["campaign_id"]:
                    raise LiveBaselineError("LIVE_BASELINE_REPLAY_CORRUPT")
            return inserted, row["campaign_id"], result

    def finish(self, request_sha256: str, result: LiveBaselineResult) -> None:
        document = _canonical_bytes(result.model_dump(mode="json"))
        if len(document) > 131072:
            raise LiveBaselineError("LIVE_BASELINE_RESULT_CAPACITY")
        placeholder = "%s" if self.mode == "POSTGRESQL" else "?"
        with self.connection() as connection:
            updated = connection.execute(
                f"UPDATE live_baseline_campaigns SET result_document={placeholder},"
                f"result_sha256={placeholder} WHERE request_sha256={placeholder} "
                f"AND campaign_id={placeholder} AND result_document IS NULL",
                (
                    document.decode(),
                    hashlib.sha256(document).hexdigest(),
                    request_sha256,
                    result.campaign_id,
                ),
            )
            if updated.rowcount != 1:
                raise LiveBaselineError("LIVE_BASELINE_RESULT_CONFLICT")

    def reclaim_local_failure(self, request_sha256: str, result: LiveBaselineResult) -> bool:
        """Atomically resume only a prior local-gate refusal for the same roots."""

        if (
            result.state != "BLOCKED"
            or result.campaign_id is None
            or not result.gap_codes
            or any(not code.startswith("LOCAL_") for code in result.gap_codes)
        ):
            return False
        document = _canonical_bytes(result.model_dump(mode="json"))
        digest = hashlib.sha256(document).hexdigest()
        placeholder = "%s" if self.mode == "POSTGRESQL" else "?"
        with self.connection() as connection:
            updated = connection.execute(
                f"UPDATE live_baseline_campaigns SET result_document=NULL,result_sha256=NULL "
                f"WHERE request_sha256={placeholder} AND campaign_id={placeholder} "
                f"AND result_sha256={placeholder} AND result_document={placeholder}",
                (request_sha256, result.campaign_id, digest, document.decode()),
            )
        return updated.rowcount == 1


@dataclass(frozen=True, repr=False)
class HostAuthorityBroker:
    configuration: HostBaselineConfiguration
    configuration_sha256: str
    policy: LiveTargetPolicy
    profile: PinnedLiveAcceptanceProfile
    host_issuer: TrustedIssuer
    product_issuer: TrustedIssuer
    registry: TrustedIssuerRegistry
    ledger: LiveReceiptLedgerSelection
    assertions: ExecutionAssertionStoreSelection
    repository_root: Path
    runner: CliRunner = field(default_factory=SubprocessCliRunner)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    configuration_path: Path | None = None
    policy_path: Path | None = None
    policy_bytes_sha256: str | None = None
    phase_policy_path: Path | None = None
    phase_policy_bytes_sha256: str | None = None

    def require_current(self, deadline: datetime) -> float:
        try:
            now = aware_utc(self.clock())
            deadline = aware_utc(deadline)
        except ValueError:
            raise LiveBaselineError("LIVE_BASELINE_AUTHORITY_EXPIRED") from None
        if self.configuration_path is not None:
            _pinned_document(self.configuration_path, self.configuration_sha256)
        if self.policy_path is not None:
            _pinned_document(self.policy_path, self.policy_bytes_sha256)
        phase_policy = self.configuration.local_validation_phase_policy
        if phase_policy is not None:
            if self.phase_policy_path is None or self.phase_policy_bytes_sha256 is None:
                raise LiveBaselineError("LOCAL_VALIDATION_PHASE_POLICY_NOT_PINNED")
            document, _ = _pinned_document(self.phase_policy_path, self.phase_policy_bytes_sha256)
            if (
                HostLocalValidationPhasePolicy.model_validate(document) != phase_policy
                or not phase_policy.issued_at <= now < phase_policy.valid_until
            ):
                raise LiveBaselineError("LOCAL_VALIDATION_PHASE_POLICY_INVALID")
        if datetime.fromisoformat(self.policy.valid_until.replace("Z", "+00:00")) <= now:
            raise LiveBaselineError("LIVE_BASELINE_POLICY_EXPIRED")
        bootstrap = self.configuration.live_read.classification_authority
        if (
            bootstrap.pins_sha256 != _classification_pins(self.configuration.live_read).pins_sha256
            or not bootstrap.issued_at <= now < bootstrap.expires_at
        ):
            raise LiveBaselineError("CLASSIFICATION_AUTHORITY_INVALID")
        if now.tzinfo is None or not self.configuration.issued_at <= now < min(
            self.configuration.valid_until, deadline
        ):
            raise LiveBaselineError("LIVE_BASELINE_AUTHORITY_EXPIRED")
        return min(
            self.configuration.live_read.command_timeout_seconds,
            (bootstrap.expires_at - now).total_seconds(),
            (min(self.configuration.valid_until, deadline) - now).total_seconds(),
        )

    def invoke(
        self, arguments: tuple[str, ...], deadline: datetime, *, maximum: int | None = None
    ) -> bytes:
        timeout = self.require_current(deadline)
        output_limit = maximum or self.configuration.maximum_bootstrap_bytes
        if not _safe_process_arguments(arguments):
            raise LiveBaselineError("LIVE_BASELINE_ARGUMENTS_UNSUPPORTED")
        result = self.runner.run(CliInvocation(arguments, timeout, output_limit))
        self.require_current(deadline)
        if not result.quiescent:
            raise LiveBaselineError("LIVE_BASELINE_PROCESS_NOT_QUIESCENT")
        if result.timed_out:
            raise LiveBaselineError("LIVE_BASELINE_TIMEOUT")
        if result.output_exceeded or len(result.stdout) > output_limit:
            raise LiveBaselineError("LIVE_BASELINE_OUTPUT_LIMIT")
        if result.returncode != 0:
            raise LiveBaselineError("LIVE_BASELINE_CLI_FAILED")
        return result.stdout

    def identity(self, deadline: datetime) -> tuple[dict[str, str], str]:
        config = self.configuration.live_read
        try:
            identity = capture_classified_identity(
                pins=_classification_pins(config),
                authority=config.classification_authority,
                invoke=lambda args, auth_deadline, maximum: _strict_json(
                    self.invoke(args, min(deadline, auth_deadline), maximum=maximum)
                ),
                clock=self.clock,
            )
        except ClassificationError as exc:
            raise LiveBaselineError(exc.code) from None
        return identity.safe_observation(), identity.user_id

    def runner_binding(
        self, issuer: TrustedIssuer, gate_ids: frozenset[str]
    ) -> TrustedExecutionRunnerKey:
        key_id = f"{issuer.issuer_id}:baseline-runner"
        read = self.configuration.live_read
        return TrustedExecutionRunnerKey(
            key_id=read.assertion_runner_key_id if gate_ids == _READ_GATES else key_id,
            producer_id=issuer.issuer_id,
            runner_id=read.assertion_runner_id if gate_ids == _READ_GATES else key_id,
            allowed_gate_ids=gate_ids,
            allowed_receipt_roles=frozenset(
                self.profile.profile.gates_by_id[gate].receiptType for gate in gate_ids
            ),
            hmac_key=hmac.new(
                issuer.hmac_key, b"neo-live-baseline-runner-v1", hashlib.sha256
            ).digest(),
        )

    def producer(
        self,
        issuer: TrustedIssuer,
        scope: ReceiptScope,
        binding: TrustedExecutionRunnerKey,
        contract: bytes | None = None,
    ) -> TrustedLiveReceiptProducer:
        return TrustedLiveReceiptProducer(
            pinned_profile=self.profile,
            expected_scope=scope,
            issuer=issuer,
            issuer_registry=self.registry,
            ledger=self.ledger.ledger,
            execution_assertion_store=self.assertions.store,
            trusted_execution_runner_keys={binding.key_id: binding},
            expected_execution_contract_document=contract,
            clock=self.clock,
        )

    def issue_classification(
        self,
        gate: str,
        scope: ReceiptScope,
        observed: dict[str, str],
        started_at: datetime,
        *,
        enrollment: SignedLiveReceipt | None = None,
    ) -> SignedLiveReceipt:
        issuer = self.host_issuer if gate == "SF-L01" else self.product_issuer
        binding = self.runner_binding(issuer, frozenset({gate}))
        producer = self.producer(issuer, scope, binding)
        authority = producer.bind_gate(
            gate_id=gate,
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            dependency_receipts=() if enrollment is None else (enrollment,),
            input_receipts=() if enrollment is None else (enrollment,),
        )
        result = build_execution_artifact_reference(
            artifact_role="SANITIZED_IDENTITY",
            media_type="application/json",
            content=_canonical_bytes(observed),
        )
        target = stable_sha256({"configuration": self.configuration_sha256, "gate": gate})
        assertion_id = f"identity:{gate}"
        artifact = ExecutionAssertionArtifact(
            expected_contract_bytes_sha256=self.configuration_sha256,
            producer_id=issuer.issuer_id,
            runner_id=binding.runner_id,
            runner_key_id=binding.key_id,
            execution_id=f"{scope.campaign_id}:{gate}",
            runner_version="1.0.0",
            adapter_version="1.0.0",
            tool_versions=(
                ExecutionToolVersion(
                    tool_id="salesforce-cli",
                    version=self.configuration.live_read.salesforce_cli_version,
                ),
            ),
            gate_id=gate,
            evidence_phase=EvidencePhase.LIVE_BASELINE,
            scope=scope,
            expected_assertion_ids=(assertion_id,),
            assertions=(
                ExecutionAssertion(
                    assertion_id=assertion_id,
                    subject_kind=AssertionSubjectKind.HTTP_ASSERTION,
                    subject_id=f"host:{gate}",
                    target_sha256=target,
                    predicate="EXACT_HOST_IDENTITY",
                    outcome=AssertionOutcome.PASSED,
                    expected_sha256=stable_sha256(observed),
                    observed_sha256=result.content_sha256,
                    result_artifact_sha256=result.content_sha256,
                    result_artifact_role=result.artifact_role,
                    predicate_sha256=target,
                    observed_cardinality=1,
                    observed_projection=tuple(sorted(observed)),
                    duration_ms=0,
                ),
            ),
            result_artifacts=(result,),
            runner_result_sha256=execution_artifact_index_sha256((result,)),
            started_at=started_at,
            terminal_at=self.clock(),
            expires_at=scope.recovery_deadline,
            runner_signature_sha256="0" * 64,
        )
        stored = self.assertions.store.append(
            serialize_execution_assertion(
                sign_execution_assertion(artifact, runner_key=binding.hmac_key)
            )
        )
        config = self.configuration.live_read
        enrollment_binding = (
            HostEnrollmentBinding(
                alias_reference_sha256=_digest(config.alias),
                organization_id_sha256=config.org_fingerprint_sha256,
                instance_host_sha256=config.instance_host_sha256,
                edition_sha256=config.edition_sha256,
                environment_class=config.environment_class,
                persona_fingerprint_sha256=config.actor_fingerprint_sha256,
            )
            if gate == "SF-L01"
            else None
        )
        return producer.append_gate_receipt(
            authority,
            GateExecutionEvidence(
                assertion_artifact_id=stored.assertion_artifact_id, enrollment=enrollment_binding
            ),
        ).parsed_receipt()

    def support(self, role: str, artifact: str, scope: ReceiptScope) -> SignedLiveReceipt:
        now = self.clock()
        self.require_current(scope.recovery_deadline)
        if role not in _SUPPORT_ROLES:
            raise LiveBaselineError("LIVE_BASELINE_SUPPORT_ROLE_INVALID")
        receipt = sign_live_receipt(
            SupportingReceiptPayload(
                receipt_role=role,
                scope=scope,
                evidence_phase=EvidencePhase.LIVE_BASELINE,
                provenance=ReceiptProvenance.HOST_AUTHORITY,
                outcome=ReceiptOutcome.RECORDED,
                issued_at=now,
                terminal_at=now,
                expires_at=scope.recovery_deadline,
                artifact_sha256=artifact,
            ),
            issuer_id=self.host_issuer.issuer_id,
            hmac_key=self.host_issuer.hmac_key,
        )
        if self.registry.verify(receipt) is not None:
            raise LiveBaselineError("LIVE_BASELINE_ISSUER_INVALID")
        document = serialize_live_receipt(receipt)
        if self.ledger.ledger.append(document).receipt_document != document:
            raise LiveBaselineError("LIVE_BASELINE_APPEND_FAILED")
        return receipt

    def require_bootstrap_authority(
        self, scope: ReceiptScope, receipts: tuple[SignedLiveReceipt, ...], target: PlannedTarget
    ) -> None:
        self.require_current(scope.recovery_deadline)
        if (
            target.partition is not TargetPartition.SYNTHETIC_DATASET
            or target.target_sha256 not in self.policy.authorized_target_sha256s
        ):
            raise LiveBaselineError("LIVE_BASELINE_DATASET_NOT_AUTHORIZED")
        durable = {
            item.receipt_id: item.receipt_document
            for item in self.ledger.ledger.replay(campaign_id=scope.campaign_id)
        }
        for receipt in receipts:
            if (
                self.registry.verify(receipt) is not None
                or receipt.payload.scope != scope
                or receipt.payload.terminal_at > self.clock()
                or receipt.payload.expires_at <= self.clock()
                or durable.get(receipt.receipt_id) != serialize_live_receipt(receipt)
            ):
                raise LiveBaselineError("LIVE_BASELINE_BOOTSTRAP_AUTHORITY_INVALID")

    def resolve_datasets(
        self, plan: Any, scope: ReceiptScope, receipts: tuple[SignedLiveReceipt, ...]
    ) -> tuple[tuple[ResolvedTargetVariables, ...], tuple[ResolvedDatasetScope, ...]]:
        datasets: dict[str, tuple[PlannedTarget, tuple[dict[str, Any], ...]]] = {}
        for target in plan.targets:
            if target.partition is not TargetPartition.SYNTHETIC_DATASET:
                continue
            self.require_bootstrap_authority(scope, receipts, target)
            _, actor_id = self.identity(scope.recovery_deadline)
            query = _dataset_query(target, actor_id, self.policy.maximum_dataset_records)
            self.require_bootstrap_authority(scope, receipts, target)
            raw = self.invoke(
                (
                    "data",
                    "query",
                    "--query",
                    query,
                    "--target-org",
                    self.configuration.live_read.alias,
                    "--json",
                ),
                scope.recovery_deadline,
            )
            self.identity(scope.recovery_deadline)
            self.require_bootstrap_authority(scope, receipts, target)
            records = _dataset_records(raw, target, actor_id)
            dataset_id = target.specification["datasetId"]
            if dataset_id in datasets:
                raise LiveBaselineError("LIVE_BASELINE_DATASET_AMBIGUOUS")
            datasets[dataset_id] = (target, records)
        variables: list[ResolvedTargetVariables] = []
        scopes: list[ResolvedDatasetScope] = []
        for target in plan.targets:
            if target.partition not in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}:
                continue
            source, records = datasets[target.specification["datasetId"]]
            spec = source.specification
            identity_field = spec["identityField"]
            ids = tuple(record[identity_field] for record in records)
            predicate_fields = {item["field"] for item in spec["predicates"]}
            variables.append(
                ResolvedTargetVariables(
                    target_sha256=target.target_sha256,
                    rows=tuple(
                        ResolvedVariableRow(
                            record_id=record[identity_field],
                            values={
                                variable["name"]: record[variable["datasetField"]]
                                for variable in target.specification["variables"]
                                if variable["valueSource"] == "SYNTHETIC_DATASET_RECEIPT"
                            },
                        )
                        for record in records
                    ),
                )
            )
            scopes.append(
                ResolvedDatasetScope(
                    target_sha256=target.target_sha256,
                    dataset_target_sha256=source.target_sha256,
                    object_api_name=spec["objectApiName"],
                    record_id_field=identity_field,
                    record_ids=ids,
                    ownership_marker_field=spec["ownershipMarkerField"],
                    field_projection=tuple(sorted(spec["fieldProjection"])),
                    member_predicate_values={
                        record[identity_field]: {
                            name: record[name] for name in sorted(predicate_fields)
                        }
                        for record in records
                    },
                    expected_records=len(records),
                    response_record_path=target.specification["responseRecordPath"],
                    field_paths=target.specification["datasetFieldPaths"],
                )
            )
        return tuple(sorted(variables, key=lambda item: item.target_sha256)), tuple(
            sorted(scopes, key=lambda item: item.target_sha256)
        )


def _dataset_query(target: PlannedTarget, actor_id: str, maximum_records: int) -> str:
    """Compile only closed equality/set predicates; no caller SOQL or identifier text."""
    spec = target.specification
    names = [
        spec["objectApiName"],
        *spec["fieldProjection"],
        *(item["field"] for item in spec["predicates"]),
    ]
    if any(not _API_NAME.fullmatch(name) for name in names) or not _SF_ID.fullmatch(actor_id):
        raise LiveBaselineError("LIVE_BASELINE_DATASET_BOOTSTRAP_UNSUPPORTED")
    count = spec["maximumRecords"]
    if type(count) is not int or not 1 <= count <= min(maximum_records, 1000):
        raise LiveBaselineError("LIVE_BASELINE_DATASET_CAPACITY")
    clauses = []
    for predicate in sorted(spec["predicates"], key=lambda item: item["field"]):
        source = predicate["valueSource"]
        values = [actor_id] if source == "CURRENT_ENROLLED_ACTOR" else predicate["values"]
        if source not in {"CURRENT_ENROLLED_ACTOR", "SOURCE_LITERAL_SET"} or not values:
            raise LiveBaselineError("LIVE_BASELINE_DATASET_BOOTSTRAP_UNSUPPORTED")
        encoded = []
        for value in values:
            if not isinstance(value, str) or not value or len(value) > 1000:
                raise LiveBaselineError("LIVE_BASELINE_DATASET_BOOTSTRAP_UNSUPPORTED")
            encoded.append("'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'")
        if predicate["operator"] == "EQUALS" and len(encoded) == 1:
            clauses.append(predicate["field"] + " = " + encoded[0])
        elif predicate["operator"] == "IN_SET":
            clauses.append(predicate["field"] + " IN (" + ",".join(encoded) + ")")
        else:
            raise LiveBaselineError("LIVE_BASELINE_DATASET_BOOTSTRAP_UNSUPPORTED")
    query = (
        "SELECT "
        + ",".join(sorted(spec["fieldProjection"]))
        + " FROM "
        + spec["objectApiName"]
        + " WHERE "
        + " AND ".join(clauses)
        + f" LIMIT {count + 1}"
    )
    if not clauses or not _safe_process_arguments((query,)):
        raise LiveBaselineError("LIVE_BASELINE_DATASET_BOOTSTRAP_UNSUPPORTED")
    return query


def _dataset_records(
    raw: bytes, target: PlannedTarget, actor_id: str
) -> tuple[dict[str, Any], ...]:
    try:
        payload = _strict_json(raw)
        result = payload["result"]
        spec = target.specification
        records = result["records"]
        if (
            type(payload["status"]) is not int
            or payload["status"] != 0
            or set(result) != {"done", "totalSize", "records"}
            or result["done"] is not True
            or type(result["totalSize"]) is not int
            or result["totalSize"] != spec["maximumRecords"]
            or not isinstance(records, list)
            or len(records) != spec["maximumRecords"]
        ):
            raise ValueError
        ids: list[str] = []
        markers: list[str] = []
        for record in records:
            if not isinstance(record, dict) or set(record) != set(spec["fieldProjection"]) | {
                "attributes"
            }:
                raise ValueError
            identity = record[spec["identityField"]]
            attributes = record["attributes"]
            if (
                not isinstance(identity, str)
                or not _SF_ID.fullmatch(identity)
                or not isinstance(attributes, dict)
                or set(attributes) != {"type", "url"}
                or attributes["type"] != spec["objectApiName"]
            ):
                raise ValueError
            if not isinstance(attributes["url"], str) or not re.fullmatch(
                r"/services/data/v[1-9][0-9]{0,2}\.0/sobjects/"
                + re.escape(spec["objectApiName"])
                + "/"
                + re.escape(identity),
                attributes["url"],
            ):
                raise ValueError
            if any(
                value is not None and not isinstance(value, (str, int, float, bool))
                for name, value in record.items()
                if name != "attributes"
            ):
                raise ValueError
            for predicate in spec["predicates"]:
                allowed = (
                    [actor_id]
                    if predicate["valueSource"] == "CURRENT_ENROLLED_ACTOR"
                    else predicate["values"]
                )
                if not any(
                    type(record[predicate["field"]]) is type(value)
                    and record[predicate["field"]] == value
                    for value in allowed
                ):
                    raise ValueError
            ids.append(identity)
            markers.append(record[spec["ownershipMarkerField"]])
        marker_predicate = next(
            item for item in spec["predicates"] if item["field"] == spec["ownershipMarkerField"]
        )
        if len(set(ids)) != len(ids) or tuple(sorted(markers)) != tuple(marker_predicate["values"]):
            raise ValueError
        return tuple(sorted(records, key=lambda record: record[spec["identityField"]]))
    except Exception:
        raise LiveBaselineError("LIVE_BASELINE_DATASET_MEMBERSHIP_MISMATCH") from None


@dataclass(frozen=True, repr=False)
class HostOwnedLiveBaselineService:
    broker: HostAuthorityBroker | None
    campaign_store: BaselineCampaignStore | None
    capture_compilation: Callable[[], CandidateTargetCompilation]
    configuration_code: str | None = None
    local_validation: HostLocalValidationService | None = None

    def _result(
        self,
        state: str,
        campaign_id: str | None,
        codes: tuple[str, ...],
        read_result: LiveReadResult | None = None,
    ) -> LiveBaselineResult:
        return LiveBaselineResult(
            campaign_id=campaign_id,
            state=state,
            ledger_mode=self.broker.ledger.mode if self.broker else "UNAVAILABLE",
            assertion_store_mode=self.broker.assertions.mode if self.broker else "UNAVAILABLE",
            gap_codes=codes,
            degradation_codes=tuple(
                sorted(
                    {
                        code
                        for code in (
                            self.broker.ledger.degradation_code if self.broker else None,
                            self.broker.assertions.degradation_code if self.broker else None,
                        )
                        if code
                    }
                )
            ),
            read_result=read_result,
        )

    def run(self) -> LiveBaselineResult:
        if self.configuration_code or self.broker is None or self.campaign_store is None:
            return self._result(
                "BLOCKED", None, (self.configuration_code or "LIVE_BASELINE_NOT_CONFIGURED",)
            )
        broker = self.broker
        campaign_id: str | None = None
        request_sha: str | None = None
        owns_claim = False
        stage = "PREPARE"
        try:
            started_at = aware_utc(broker.clock())
            deadline = min(
                started_at + timedelta(seconds=broker.configuration.maximum_campaign_seconds),
                broker.configuration.valid_until,
                broker.configuration.live_read.classification_authority.expires_at,
                datetime.fromisoformat(broker.policy.valid_until.replace("Z", "+00:00")),
            ).replace(microsecond=0)
            broker.require_current(deadline)
            stage = "CANDIDATE_CAPTURE"
            compilation = self.capture_compilation()
            nonlocal_blocks = tuple(
                code
                for code in compilation.blocking_reason_codes
                if code != "LOCAL_SOURCE_VALIDATION_REQUIRED"
            )
            if nonlocal_blocks:
                raise LiveBaselineError("LIVE_BASELINE_TARGET_COMPILATION_BLOCKED")
            if (
                "LOCAL_SOURCE_VALIDATION_REQUIRED" in compilation.blocking_reason_codes
                and not compilation.required_local_validations
            ):
                raise LiveBaselineError("LOCAL_VALIDATION_REQUIRED")
            stage = "PHASE_POLICY"
            phase_policy = broker.configuration.local_validation_phase_policy
            try:
                deferred_locals = classify_local_validation_phase(
                    compilation,
                    host_phase=EvidencePhase.LIVE_BASELINE,
                    policy=phase_policy,
                    observed_at=broker.clock(),
                )
            except LocalValidationPhaseError:
                raise LiveBaselineError("LOCAL_VALIDATION_PHASE_POLICY_INVALID") from None
            all_locals_deferred = bool(compilation.required_local_validations) and {
                value.obligation_id for value in deferred_locals
            } == {
                value.obligation.obligation_id for value in compilation.required_local_validations
            }
            if phase_policy is not None:
                deadline = min(deadline, phase_policy.valid_until).replace(microsecond=0)
                broker.require_current(deadline)
            if (
                compilation.required_local_validations
                and not all_locals_deferred
                and self.local_validation is None
            ):
                raise LiveBaselineError("LOCAL_VALIDATION_NOT_CONFIGURED")
            # The configured policy is independent of this compiler. Never populate its
            # allowlist from the targets just produced by untrusted source content.
            stage = "TARGET_POLICY"
            target_ids = {
                item.target_sha256 for item in compilation.derivations if item.target_sha256
            }
            if target_ids != set(broker.policy.authorized_target_sha256s):
                raise LiveBaselineError("LIVE_BASELINE_TARGET_POLICY_DENIED")
            request_sha = stable_sha256(
                {
                    "authority": broker.configuration_sha256,
                    "local_phase_policy": broker.phase_policy_bytes_sha256,
                    "policy": broker.policy.sha256,
                    # Analysis receipts carry fresh timestamps/IDs on recapture.
                    # Idempotency instead uses immutable, host-verified source roots.
                    "source_contract": compilation.source_contract_sha256,
                    "source_snapshot": compilation.verified_scope.source_snapshot_sha256,
                    "verified_change": compilation.verified_scope.verified_change_sha256,
                    "source_profile": compilation.verified_scope.source_profile_sha256,
                    "ontology": compilation.verified_scope.ontology_sha256,
                    "project": compilation.verified_scope.project_id,
                    "target_ids": sorted(target_ids),
                    "source_files": [
                        item.model_dump(mode="json") for item in compilation.captured_source_files
                    ],
                    "local_validations": [
                        {
                            "obligation": item.obligation.model_dump(mode="json"),
                            "candidate_tree": item.candidate_tree_sha256,
                            "command_contract": item.command_contract_sha256,
                            "bound_files": [
                                value.model_dump(mode="json") for value in item.bound_files
                            ],
                        }
                        for item in compilation.required_local_validations
                    ],
                }
            )
            stage = "CAMPAIGN_CLAIM"
            campaign_id = "baseline:" + uuid.uuid4().hex
            claimed, campaign_id, cached = self.campaign_store.claim(request_sha, campaign_id)
            owns_claim = claimed
            if not claimed:
                # Results remain non-authorizing, but never return a cached positive if
                # current durable receipt bytes have gone missing or changed.
                if cached is not None:
                    if self.campaign_store.reclaim_local_failure(request_sha, cached):
                        owns_claim = True
                    else:
                        if cached.read_result:
                            _validate_cached_baseline(broker, self.campaign_store, cached)
                            if all_locals_deferred:
                                expected = ExpectedExecutionContract.model_validate_json(
                                    self.campaign_store.load_evidence(
                                        cached.campaign_id
                                    ).expected_contract_json
                                )
                                if (
                                    expected.deferred_local_validations != deferred_locals
                                    or expected.local_validation_phase_policy_sha256
                                    != phase_policy.policy_sha256
                                    or expected.verified_local_validations
                                ):
                                    raise LiveBaselineError("LOCAL_VALIDATION_PHASE_POLICY_INVALID")
                            elif compilation.required_local_validations:
                                assert self.local_validation is not None
                                expected = ExpectedExecutionContract.model_validate_json(
                                    self.campaign_store.load_evidence(
                                        cached.campaign_id
                                    ).expected_contract_json
                                )
                                recovered = self.local_validation.recover(
                                    compilation, scope=expected.scope
                                )
                                if not recovered:
                                    raise LiveBaselineError("LOCAL_VALIDATION_EVIDENCE_INVALID")
                                try:
                                    verified = verify_local_validation_evidence(
                                        compilation,
                                        recovered,
                                        scope=expected.scope,
                                        registry=broker.registry,
                                        ledger=broker.ledger.ledger,
                                        artifact_store=self.local_validation.artifact_store,
                                        runner_pins=self.local_validation.pins,
                                        observed_at=broker.clock(),
                                    )
                                except LocalValidationVerificationError:
                                    raise LiveBaselineError(
                                        "LOCAL_VALIDATION_EVIDENCE_INVALID"
                                    ) from None
                                if verified != expected.verified_local_validations:
                                    raise LiveBaselineError("LOCAL_VALIDATION_EVIDENCE_INVALID")
                        return cached
                else:
                    return self._result(
                        "IN_PROGRESS", campaign_id, ("LIVE_BASELINE_ALREADY_CLAIMED",)
                    )
            recorded_assertions = _RecordingAssertionStore(
                broker.assertions.store, campaign_id, broker.profile
            )
            broker = replace(
                broker, assertions=replace(broker.assertions, store=recorded_assertions)
            )
            scope = ReceiptScope(
                campaign_id=campaign_id,
                project_id=compilation.verified_scope.project_id,
                source_contract_sha256=compilation.source_contract_sha256,
                candidate_sha256=compilation.candidate_bundle_sha256,
                build_sha256=stable_sha256({"phase": "LIVE_BASELINE", "build": "NOT_APPLICABLE"}),
                operation_plan_sha256=compilation.compilation_sha256,
                restore_scope_sha256=stable_sha256(
                    {"phase": "LIVE_BASELINE", "restore": "NO_MUTATION"}
                ),
                policy_sha256=broker.policy.sha256,
                profile_sha256=broker.profile.profile_sha256,
                org_fingerprint_sha256=broker.configuration.live_read.org_fingerprint_sha256,
                actor_fingerprint_sha256=broker.configuration.live_read.actor_fingerprint_sha256,
                recovery_deadline=deadline,
            )
            stage = "LOCAL_VALIDATION"
            local_evidence: tuple[LocalValidationEvidence, ...] = ()
            if compilation.required_local_validations and not all_locals_deferred:
                assert self.local_validation is not None
                local_evidence = self.local_validation.recover(compilation, scope=scope) or ()
                if not local_evidence:
                    local_evidence = self.local_validation.run(compilation, scope=scope)
                if len(local_evidence) != len(compilation.required_local_validations):
                    raise LiveBaselineError("LOCAL_VALIDATION_INCOMPLETE")
                # A return value/count is not evidence. Re-read exact durable bytes,
                # signatures, complete membership and current expiry before even the
                # local sf version subprocess. Composition independently replays again.
                try:
                    verify_local_validation_evidence(
                        compilation,
                        local_evidence,
                        scope=scope,
                        registry=broker.registry,
                        ledger=broker.ledger.ledger,
                        artifact_store=self.local_validation.artifact_store,
                        runner_pins=self.local_validation.pins,
                        observed_at=broker.clock(),
                    )
                except LocalValidationVerificationError:
                    raise LiveBaselineError("LOCAL_VALIDATION_EVIDENCE_INVALID") from None
            stage = "CLI_VERSION"
            version_output = broker.invoke(("--version",), deadline, maximum=1024)
            version = _CLI_VERSION_OUTPUT.match(version_output)
            if (
                version is None
                or version.group(1).decode()
                != broker.configuration.live_read.salesforce_cli_version
            ):
                raise LiveBaselineError("LIVE_BASELINE_CLI_VERSION_MISMATCH")
            stage = "CLASSIFICATION_ENROLLMENT"
            observation, _ = broker.identity(deadline)
            enrollment = broker.issue_classification("SF-L01", scope, observation, started_at)
            stage = "CLASSIFICATION_AUTHENTICATION"
            cli_started_at = broker.clock()
            observation, _ = broker.identity(deadline)
            cli = broker.issue_classification(
                "SF-L02", scope, observation, cli_started_at, enrollment=enrollment
            )
            read_config = broker.configuration.live_read
            read_binding = broker.runner_binding(broker.product_issuer, _READ_GATES)
            composition_kwargs: dict[str, Any] = {}
            if local_evidence:
                assert self.local_validation is not None
                composition_kwargs = {
                    "local_validation_evidence": local_evidence,
                    "local_validation_runner_pins": self.local_validation.pins,
                    "local_validation_artifact_store": self.local_validation.artifact_store,
                    "issuer_registry": broker.registry,
                    "receipt_ledger": broker.ledger.ledger,
                }
            if phase_policy is not None:
                composition_kwargs.update(
                    host_phase=EvidencePhase.LIVE_BASELINE,
                    local_validation_phase_policy=phase_policy,
                )
            stage = "PLAN_COMPOSITION"
            composition = compose_candidate_live_plan(
                compilation,
                HostOrganizationClassification(
                    receiptSha256=hashlib.sha256(serialize_live_receipt(enrollment)).hexdigest(),
                    environmentClass=read_config.environment_class,
                    validUntil=deadline.isoformat(),
                    executionScope=scope,
                ),
                broker.policy,
                ExpectedRunnerProvenance(
                    producerId=broker.product_issuer.issuer_id,
                    runnerId=read_binding.runner_id,
                    runnerKeyId=read_binding.key_id,
                    runnerVersion=read_config.runner_version,
                    adapterVersion=read_config.adapter_version,
                    toolVersions=(
                        ExecutionToolVersion(
                            tool_id="salesforce-cli", version=read_config.salesforce_cli_version
                        ),
                    ),
                ),
                clock=broker.clock,
                **composition_kwargs,
            )
            if (
                composition.evaluation.plan is None
                or composition.expected_execution_contract_bytes is None
            ):
                raise LiveBaselineError("LIVE_BASELINE_PLAN_BLOCKED")
            plan = composition.evaluation.plan
            for planned_target in plan.targets:
                if planned_target.partition is TargetPartition.METADATA:
                    _metadata_target(planned_target)
            stage = "SUPPORT_RECEIPTS"
            target_receipt = broker.support("LIVE_TARGET_PLAN_RECEIPT", plan.plan_sha256, scope)
            expected_document = composition.expected_execution_contract_bytes
            expected_sha = hashlib.sha256(expected_document).hexdigest()
            expected_receipt = broker.support(
                "EXPECTED_EXECUTION_CONTRACT_RECEIPT", expected_sha, scope
            )
            stage = "DATASET_RESOLUTION"
            variables, dataset_scopes = broker.resolve_datasets(
                plan, scope, (enrollment, cli, target_receipt, expected_receipt)
            )
            provisional = HostLiveReadCapture(
                plan=plan,
                enrollment_receipt=enrollment,
                cli_authentication_receipt=cli,
                target_plan_receipt=target_receipt,
                resolved_variables=variables,
                resolved_dataset_scopes=dataset_scopes,
                expected_execution_contract_document=expected_document,
                expected_execution_contract_sha256=expected_sha,
                expected_execution_contract_receipt=expected_receipt,
            )
            dataset_receipt = broker.support(
                "LIVE_DATASET_SCOPE_RECEIPT", _dataset_scope_digest(provisional), scope
            )
            capture = provisional.model_copy(update={"dataset_scope_receipt": dataset_receipt})

            class _Port:
                def capture(self) -> HostLiveReadCapture:
                    return capture

            stage = "LIVE_READ_EXECUTION"
            execution = HostOwnedLiveReadExecutor(
                config=read_config,
                repository_root=broker.repository_root,
                input_port=_Port(),
                issuer_registry=broker.registry,
                receipt_producer=broker.producer(
                    broker.product_issuer, scope, read_binding, expected_document
                ),
                execution_assertion_store=broker.assertions.store,
                runner_authentication_key=read_binding.hmac_key,
                runner=broker.runner,
                clock=broker.clock,
                authority_revalidator=lambda: broker.require_current(deadline),
            ).execute()
            stage = "EVIDENCE_PERSISTENCE"
            self.campaign_store.save_evidence(
                campaign_id,
                _BaselineEvidenceIndex(
                    expected_contract_json=expected_document.decode("utf-8"),
                    assertion_artifact_ids=recorded_assertions.recorded,
                ),
            )
            gaps = ("LIVE_CAMPAIGN_INCOMPLETE",)
            if all_locals_deferred:
                gaps += ("LOCAL_VALIDATION_UNRESOLVED_NOT_REQUIRED_FOR_BASELINE",)
            result = self._result("COMPLETED", campaign_id, gaps, execution)
        except (LiveBaselineError, LiveReadError, LocalValidationRunError) as exc:
            code = exc.code.value if hasattr(exc.code, "value") else exc.code
            result = self._result("BLOCKED", campaign_id, (code,))
        except Exception as exc:
            _LOGGER.error(
                _canonical_bytes(
                    {
                        "event": "live_baseline_unexpected_failure",
                        "stage": stage,
                        "error_class": type(exc).__name__,
                        "campaign_id": campaign_id,
                    }
                ).decode("utf-8")
            )
            result = self._result("BLOCKED", campaign_id, (f"LIVE_BASELINE_UNEXPECTED_{stage}",))
        if owns_claim and request_sha and campaign_id:
            try:
                self.campaign_store.finish(request_sha, result)
            except Exception:
                return self._result("BLOCKED", campaign_id, ("LIVE_BASELINE_RESULT_NOT_DURABLE",))
        return result


def _validate_cached_baseline(
    broker: HostAuthorityBroker, campaigns: BaselineCampaignStore, cached: LiveBaselineResult
) -> None:
    """Revalidate exact stored bytes; cache possession alone never proves a gate."""
    try:
        if cached.campaign_id is None or cached.read_result is None:
            raise ValueError
        evidence = campaigns.load_evidence(cached.campaign_id)
        document = evidence.expected_contract_json.encode("utf-8")
        contract = ExpectedExecutionContract.model_validate_json(document)
        scope = contract.scope
        config = broker.configuration.live_read
        if (
            scope.campaign_id != cached.campaign_id
            or scope.org_fingerprint_sha256 != config.org_fingerprint_sha256
            or scope.actor_fingerprint_sha256 != config.actor_fingerprint_sha256
            or scope.policy_sha256 != broker.policy.sha256
            or scope.profile_sha256 != broker.profile.profile_sha256
            or contract.plan_sha256 != cached.read_result.plan_sha256
            or (
                contract.local_validation_phase_policy_sha256 is not None
                and contract.phase_read_only_gate_ids != READ_ONLY_BASELINE_GATES
            )
        ):
            raise ValueError
        broker.require_current(scope.recovery_deadline)
        receipts = tuple(
            item.parsed_receipt()
            for item in broker.ledger.ledger.replay(campaign_id=cached.campaign_id)
        )
        by_id = {item.receipt_id: item for item in receipts}
        if not set(cached.read_result.stored_receipt_ids).issubset(by_id):
            raise ValueError
        expected_receipts = [
            item
            for item in receipts
            if isinstance(item.payload, SupportingReceiptPayload)
            and item.receipt_role == "EXPECTED_EXECUTION_CONTRACT_RECEIPT"
        ]
        if len(expected_receipts) != 1 or (
            expected_receipts[0].payload.artifact_sha256 != hashlib.sha256(document).hexdigest()
        ):
            raise ValueError
        validation = validate_live_campaign(
            pinned_profile=broker.profile,
            expected_scope=scope,
            receipts=receipts,
            issuer_registry=broker.registry,
        )
        if not _BASELINE_GATES.issubset(validation.locally_valid_gate_ids):
            raise ValueError
        gate_receipts = {
            item.payload.gate_id: item
            for item in receipts
            if isinstance(item.payload, GateReceiptPayload)
        }
        if set(gate_receipts) != _BASELINE_GATES:
            raise ValueError
        signed_observations: list[dict[str, Any]] = []
        for gate in sorted(_BASELINE_GATES):
            receipt = gate_receipts[gate]
            issuer = broker.host_issuer if gate == "SF-L01" else broker.product_issuer
            binding = broker.runner_binding(
                issuer, _READ_GATES if gate in _READ_GATES else frozenset({gate})
            )
            artifact = replay_execution_assertion(
                broker.assertions.store,
                assertion_artifact_id=evidence.assertion_artifact_ids[gate],
                expected_scope=scope,
                expected_gate_id=gate,
                expected_evidence_phase=EvidencePhase.LIVE_BASELINE,
                trusted_runner_keys={binding.key_id: binding},
                expected_receipt_role=receipt.receipt_role,
                expected_contract_document=document if gate in _READ_GATES else None,
                now=broker.clock(),
            )
            if (
                artifact.derived_outcome is not ReceiptOutcome.PASSED
                or artifact.assertions_sha256 != receipt.payload.assertions_sha256
                or artifact.artifact_index_sha256 != receipt.payload.artifact_index_sha256
                or artifact.started_at != receipt.payload.issued_at
                or artifact.terminal_at != receipt.payload.terminal_at
                or artifact.expires_at != receipt.payload.expires_at
            ):
                raise ValueError
            if gate in _READ_GATES:
                signed_observations.extend(
                    _strict_json(base64.b64decode(item.content_base64, validate=True))
                    for item in artifact.result_artifacts
                )
            elif artifact.expected_contract_bytes_sha256 != broker.configuration_sha256:
                raise ValueError
        if sorted(signed_observations, key=lambda item: item["target_sha256"]) != sorted(
            [item.model_dump(mode="json") for item in cached.read_result.observations],
            key=lambda item: item["target_sha256"],
        ):
            raise ValueError
    except Exception:
        raise LiveBaselineError("LIVE_BASELINE_REPLAY_CORRUPT") from None


def _pinned_document(path: Path, expected_sha256: str | None) -> tuple[dict[str, Any], str]:
    if expected_sha256 is None or not re.fullmatch(_HEX, expected_sha256):
        raise LiveBaselineError("LIVE_BASELINE_CONFIGURATION_PIN_REQUIRED")
    absolute = path.absolute()
    for ancestor in absolute.parents:
        _checked_identity(ancestor, directory=True)
    raw = _read_nofollow(absolute, maximum_bytes=2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise LiveBaselineError("LIVE_BASELINE_CONFIGURATION_PIN_MISMATCH")
    return _strict_json(raw), expected_sha256


def create_live_baseline_service(
    settings: Settings,
    repository_root: Path,
    *,
    candidate_provider: Callable[[], CandidateAssuranceBundle],
    source_profile: SourceGraphProfile,
) -> HostOwnedLiveBaselineService:
    """Composition root only; construction performs no Salesforce operation."""

    def capture_compilation() -> CandidateTargetCompilation:
        salesforce_repository_root, salesforce_app_root = settings.resolved_salesforce_roots(
            repository_root
        )
        contract_locator = (
            (salesforce_app_root / "contracts/agent-interface.json")
            .relative_to(salesforce_repository_root)
            .as_posix()
        )
        source_port = HostOwnedSourceContractPort(
            salesforce_repository_root,
            contract_locator=contract_locator,
        )
        return ProductionCandidateTargetCompiler().compile(
            source_port.capture(candidate_provider(), source_profile)
        )

    if not settings.live_baseline_enabled:
        return HostOwnedLiveBaselineService(
            None, None, capture_compilation, "LIVE_BASELINE_NOT_ENABLED"
        )
    try:
        config_path = settings._resolved_repository_path(
            settings.live_baseline_config_path, repository_root
        )
        config_document, config_sha = _pinned_document(
            config_path, settings.live_baseline_config_sha256
        )
        configuration = HostBaselineConfiguration.model_validate(config_document)
        # Phase applicability has its own private, externally pinned configuration.
        # Embedding it in the general baseline document is not an alternate authority path.
        if configuration.local_validation_phase_policy is not None:
            raise LiveBaselineError("LOCAL_VALIDATION_PHASE_POLICY_REQUIRES_SEPARATE_PIN")
        phase_path = None
        phase_bytes_sha = None
        if settings.live_local_validation_phase_policy_enabled:
            # Keep the lexical path intact: resolve() would conceal a junction/symlink
            # before the pinned reader checks every directory and the file itself.
            phase_path = (
                repository_root.absolute() / settings.live_local_validation_phase_policy_path
            )
            phase_document, phase_bytes_sha = _pinned_document(
                phase_path, settings.live_local_validation_phase_policy_sha256
            )
            phase_policy = HostLocalValidationPhasePolicy.model_validate(phase_document)
            configuration = configuration.model_copy(
                update={
                    "local_validation_phase_policy": phase_policy,
                }
            )
        policy_document, _ = _pinned_document(
            settings._resolved_repository_path(settings.live_target_policy_path, repository_root),
            settings.live_target_policy_sha256,
        )
        policy = LiveTargetPolicy.model_validate(policy_document)
        profile = _load_pinned_profile(settings, repository_root)
        if policy.acceptance_profile_sha256 != profile.profile_sha256:
            raise LiveBaselineError("LIVE_BASELINE_POLICY_PROFILE_MISMATCH")
        if configuration.live_read.alias != settings.require_operator_alias():
            raise LiveBaselineError("LIVE_BASELINE_ALIAS_CONFIGURATION_MISMATCH")
        host_secret = settings.live_host_receipt_hmac_key
        product_secret = settings.live_product_receipt_hmac_key
        if (
            host_secret is None
            or product_secret is None
            or settings.live_host_receipt_issuer_id == settings.live_product_receipt_issuer_id
            or host_secret.get_secret_value() == product_secret.get_secret_value()
            or configuration.live_read.assertion_producer_id
            != settings.live_product_receipt_issuer_id
        ):
            raise LiveBaselineError("LIVE_BASELINE_INDEPENDENT_ISSUERS_REQUIRED")
        host = TrustedIssuer(
            issuer_id=settings.live_host_receipt_issuer_id or "",
            issuer_class=TrustedIssuerClass.HOST_AUTHORITY,
            hmac_key=host_secret.get_secret_value().encode(),
            allowed_receipt_roles=frozenset(
                item.strip() for item in settings.live_host_receipt_roles.split(",") if item.strip()
            ),
        )
        product = TrustedIssuer(
            issuer_id=settings.live_product_receipt_issuer_id or "",
            issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION,
            hmac_key=product_secret.get_secret_value().encode(),
            allowed_receipt_roles=frozenset(
                item.strip()
                for item in settings.live_product_receipt_roles.split(",")
                if item.strip()
            ),
        )
        if not _SUPPORT_ROLES.union({"HOST_ENROLLMENT_RECEIPT"}).issubset(
            host.allowed_receipt_roles
        ) or not {
            profile.profile.gates_by_id[gate].receiptType for gate in (*_READ_GATES, "SF-L02")
        }.union({LOCAL_VALIDATION_RECEIPT_ROLE}).issubset(product.allowed_receipt_roles):
            raise LiveBaselineError("LIVE_BASELINE_ISSUER_ROLES_INCOMPLETE")
        database_url = settings.database_url.get_secret_value() if settings.database_url else None
        ledger = open_live_receipt_ledger(
            database_url=database_url,
            sqlite_path=settings.resolved_live_receipt_sqlite_path(repository_root),
            postgres_schema=settings.postgres_schema,
        )
        assertions = open_execution_assertion_store(
            database_url=database_url,
            sqlite_path=settings._resolved_repository_path(
                settings.live_assertion_sqlite_path, repository_root
            ),
            postgres_schema=settings.postgres_schema,
        )
        if ledger.mode == "UNAVAILABLE" or assertions.mode != ledger.mode:
            raise LiveBaselineError("LIVE_BASELINE_STORAGE_UNAVAILABLE")
        campaigns = BaselineCampaignStore(
            mode=ledger.mode,
            database_url=database_url,
            sqlite_path=settings._resolved_repository_path(
                settings.live_baseline_sqlite_path, repository_root
            ),
            postgres_schema=settings.postgres_schema,
        )
        campaigns.setup()
        local_validation = None
        if (
            settings.live_local_validation_config_sha256 is not None
            or configuration.local_validation_phase_policy is None
        ):
            local_config_document, _ = _pinned_document(
                settings._resolved_repository_path(
                    settings.live_local_validation_config_path, repository_root
                ),
                settings.live_local_validation_config_sha256,
            )
            local_pins = LocalValidationRunnerPins.model_validate(local_config_document)
            local_artifacts = SQLiteLocalValidationArtifactStore(
                settings._resolved_repository_path(
                    settings.live_local_validation_artifact_sqlite_path, repository_root
                )
            )
            local_artifacts.setup()
            local_validation = HostLocalValidationService(
                repository_root=settings.resolved_salesforce_repository_root(repository_root),
                pins=local_pins,
                ledger=ledger.ledger,
                artifact_store=local_artifacts,
                product_issuer=product,
            )
        broker = HostAuthorityBroker(
            configuration,
            config_sha,
            policy,
            profile,
            host,
            product,
            TrustedIssuerRegistry((host, product)),
            ledger,
            assertions,
            repository_root,
            configuration_path=config_path,
            policy_path=settings._resolved_repository_path(
                settings.live_target_policy_path, repository_root
            ),
            policy_bytes_sha256=settings.live_target_policy_sha256,
            phase_policy_path=phase_path,
            phase_policy_bytes_sha256=phase_bytes_sha,
        )
        return HostOwnedLiveBaselineService(
            broker, campaigns, capture_compilation, local_validation=local_validation
        )
    except LiveBaselineError as exc:
        return HostOwnedLiveBaselineService(None, None, capture_compilation, exc.code)
    except Exception:
        return HostOwnedLiveBaselineService(
            None, None, capture_compilation, "LIVE_BASELINE_CONFIGURATION_INVALID"
        )
