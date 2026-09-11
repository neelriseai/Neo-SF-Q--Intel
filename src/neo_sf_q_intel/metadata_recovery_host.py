"""Host-only enrollment/factory for one source-compiled metadata recovery transaction.

No API/MCP input, auto-enrollment, fake runner, or signing key is accepted from a document.
Construction is offline. Calling the returned service is an explicit live operation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field, StrictBool, model_validator

from neo_sf_q_intel.classification_bootstrap import (
    ClassificationAuthority,
    ClassificationPins,
    capture_classified_identity,
)
from neo_sf_q_intel.live_read_evidence import (
    CliCompleted,
    CliInvocation,
    PinnedCliLaunch,
    SubprocessCliRunner,
    _checked_identity,
    _DirectoryGuards,
    _read_nofollow,
    _resolve_launch,
)
from neo_sf_q_intel.live_receipt_ledger import LiveReceiptLedger, serialize_live_receipt
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptScope,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
)
from neo_sf_q_intel.metadata_recovery import (
    Digest,
    HostMetadataRecoveryAdapter,
    MetadataPropertyIntent,
    MetadataRecoveryConfig,
    MetadataRecoveryLease,
    MetadataRecoveryReport,
    _canonical,
    _root,
    _sha,
    _write_exclusive,
    metadata_execution_binding,
)
from neo_sf_q_intel.temporal import UtcModel, aware_utc

_AUTHORITY = "INDEPENDENT_MUTATION_AUTHORIZATION_RECEIPT"
_RECOVERY = "CAMPAIGN_RECOVERY_PERMIT_RECEIPT"
_CHECK = "METADATA_CHECK_ONLY_PREFLIGHT_RECEIPT"
_RESTORE = "INDEPENDENT_RESTORE_REHEARSAL_RECEIPT"
_ROLES = frozenset(
    {
        _AUTHORITY,
        _RECOVERY,
        _CHECK,
        _RESTORE,
        "HOST_ENROLLMENT_RECEIPT",
        "CLI_AUTHENTICATION_RECEIPT",
    }
)
_OPERATIONS = frozenset(
    {
        "PREPARE",
        "PREPARE_RECOVERY",
        "READ_PREIMAGE",
        "READ_RECOVERY",
        "CHECK_ONLY",
        "DEPLOY",
        "RESTORE",
        "REPORT_JOB",
        "CANCEL_FORWARD_JOB",
    }
)


class MetadataHostError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _closed_json(document: bytes):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise MetadataHostError("METADATA_HOST_DUPLICATE_KEY")
            result[key] = value
        return result

    return json.loads(document, object_pairs_hook=pairs)


def _sanitize_failure(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except MetadataHostError:
            raise
        except Exception:
            raise MetadataHostError("METADATA_HOST_CONFIGURATION_INVALID") from None

    return wrapped


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetadataCliPins(_Model):
    """Logical roles only; resolved machine paths are never serialized into enrollment."""

    node_sha256: Digest
    entrypoint_sha256: Digest
    package_sha256: Digest
    version: str = Field(pattern=r"^2\.[0-9]+\.[0-9]+$")


class MetadataHostSettings(_Model):
    enabled: StrictBool = False
    enrollment_path: str = ".runtime/metadata-host-enrollment.json"
    enrollment_sha256: Digest | None = None
    expected_task_authority_sha256: Digest | None = None
    state_path: str = ".runtime/host-authority/metadata-fences.sqlite3"

    @model_validator(mode="after")
    def closed_paths(self):
        for value, suffix in ((self.enrollment_path, ".json"), (self.state_path, ".sqlite3")):
            parts = value.split("/")
            if (
                not value.startswith(".runtime/")
                or not value.endswith(suffix)
                or any(part in {"", ".", ".."} for part in parts)
                or "\\" in value
                or ":" in value
                or any(ord(item) < 32 for item in value)
            ):
                raise ValueError("Host paths must be exact relative private runtime locators")
        if self.enabled and (
            self.enrollment_sha256 is None or self.expected_task_authority_sha256 is None
        ):
            raise ValueError("Activation requires independent enrollment and task pins")
        return self


class MetadataHostEnrollment(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_class: Literal["ONE_SOURCE_PROPERTY_METADATA_TRANSACTION"]
    task_authority_sha256: Digest
    exclusive_change_window_sha256: Digest
    state_store_id: Digest
    host_implementation_sha256: Digest
    intent: MetadataPropertyIntent
    lease: MetadataRecoveryLease
    scope: ReceiptScope
    classification_pins: ClassificationPins
    classification_authority: ClassificationAuthority
    cli: MetadataCliPins
    receipts: tuple[SignedLiveReceipt, ...] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def exact_bindings(self):
        pins, intent, lease = self.classification_pins, self.intent, self.lease
        if (
            pins.environment_class not in {"SANDBOX", "DEVELOPER_EDITION"}
            or pins.api_version != "v" + intent.api_version
            or pins.org_fingerprint_sha256 != lease.org_fingerprint_sha256
            or pins.actor_fingerprint_sha256 != lease.actor_fingerprint_sha256
            or lease.intent_sha256 != _root(intent)
            or intent.scope_sha256 != _root(self.scope)
            or intent.source_contract_sha256 != self.scope.source_contract_sha256
            or intent.target_plan_sha256 != self.scope.operation_plan_sha256
            or self.scope.org_fingerprint_sha256 != pins.org_fingerprint_sha256
            or self.scope.actor_fingerprint_sha256 != pins.actor_fingerprint_sha256
            or self.scope.recovery_deadline != lease.recovery_deadline
            or self.classification_authority.pins_sha256 != pins.pins_sha256
            or self.classification_authority.task_authority_sha256 != self.task_authority_sha256
            or self.classification_authority.issued_at > lease.issued_at
            or self.classification_authority.expires_at < lease.recovery_deadline
            or {receipt.receipt_role for receipt in self.receipts} != _ROLES
        ):
            raise ValueError("Enrollment, source, classification, campaign and lease differ")
        expected = {
            "HOST_ENROLLMENT_RECEIPT": lease.classification_receipt_sha256,
            _AUTHORITY: lease.mutation_authority_receipt_sha256,
            _RECOVERY: lease.recovery_authority_receipt_sha256,
            _RESTORE: lease.restore_rehearsal_receipt_sha256,
        }
        if any(
            _sha(serialize_live_receipt(item)) != expected[item.receipt_role]
            for item in self.receipts
            if item.receipt_role in expected
        ):
            raise ValueError("Lease receipt roots differ from exact enrollment receipts")
        if lease.one_use_claim_sha256 != _sha(("metadata-one-use:" + _claim(self)).encode()):
            raise ValueError("One-use lease differs from the exact signed task claim")
        return self


def metadata_authorization_claim_sha256(
    *,
    intent: MetadataPropertyIntent,
    execution_binding_sha256: str,
    scope: ReceiptScope,
    task_authority_sha256: str,
    exclusive_change_window_sha256: str,
    state_store_id: str,
    host_implementation_sha256: str,
    cli: MetadataCliPins,
    classification_pins: ClassificationPins,
    issued_at: datetime,
    expires_at: datetime,
    recovery_deadline: datetime,
) -> str:
    """Pure pre-signing root; does not include receipts, avoiding a self-signature cycle."""
    return _sha(
        _canonical(
            {
                "schema_version": "1.0.0",
                "intent_sha256": _root(intent),
                "execution_binding_sha256": execution_binding_sha256,
                "scope_sha256": _root(scope),
                "task_authority_sha256": task_authority_sha256,
                "exclusive_change_window_sha256": exclusive_change_window_sha256,
                "state_store_id": state_store_id,
                "host_implementation_sha256": host_implementation_sha256,
                "cli_sha256": _root(cli),
                "classification_pins_sha256": classification_pins.pins_sha256,
                "operations": sorted(_OPERATIONS),
                "issued_at": aware_utc(issued_at).isoformat(),
                "expires_at": aware_utc(expires_at).isoformat(),
                "recovery_deadline": aware_utc(recovery_deadline).isoformat(),
            }
        )
    )


def _claim(enrollment: MetadataHostEnrollment) -> str:
    lease = enrollment.lease
    return metadata_authorization_claim_sha256(
        intent=enrollment.intent,
        execution_binding_sha256=lease.execution_binding_sha256,
        scope=enrollment.scope,
        task_authority_sha256=enrollment.task_authority_sha256,
        exclusive_change_window_sha256=enrollment.exclusive_change_window_sha256,
        state_store_id=enrollment.state_store_id,
        host_implementation_sha256=enrollment.host_implementation_sha256,
        cli=enrollment.cli,
        classification_pins=enrollment.classification_pins,
        issued_at=lease.issued_at,
        expires_at=lease.expires_at,
        recovery_deadline=lease.recovery_deadline,
    )


def metadata_preflight_artifact_sha256(intent: MetadataPropertyIntent, role: str) -> str:
    """Exact artifact binding expected from the independent product preflight/rehearsal producer."""
    if role not in {_CHECK, _RESTORE}:
        raise MetadataHostError("METADATA_HOST_PREFLIGHT_ROLE_INVALID")
    return _sha(
        _canonical(
            {
                "role": role,
                "intent_sha256": _root(intent),
                "preimage_sha256": intent.expected_preimage_sha256,
                "candidate_sha256": intent.expected_candidate_sha256,
                "test_classes": intent.test_classes,
                "methods": intent.tests,
                "expected_test_count": intent.expected_test_count,
                "complete": True,
                "restoration": "PREIMAGE_EXACT",
                "residue_count": 0,
            }
        )
    )


def verify_metadata_enrollment(
    enrollment: MetadataHostEnrollment,
    *,
    registry: TrustedIssuerRegistry,
    ledger: LiveReceiptLedger,
    now: datetime,
    recovery: bool = False,
) -> None:
    """Replay exact durable evidence; receipt presence/signatures alone are not sufficient."""
    now = aware_utc(now)
    lease = enrollment.lease
    if not lease.issued_at <= now < (lease.recovery_deadline if recovery else lease.expires_at):
        raise MetadataHostError("METADATA_HOST_AUTHORITY_EXPIRED")
    durable = {
        item.receipt_id: item.receipt_document
        for item in ledger.replay(campaign_id=enrollment.scope.campaign_id)
    }
    for receipt in enrollment.receipts:
        role, payload = receipt.receipt_role, receipt.payload
        issuer = (
            TrustedIssuerClass.HOST_AUTHORITY
            if role in {"HOST_ENROLLMENT_RECEIPT", _AUTHORITY, _RECOVERY}
            else TrustedIssuerClass.PRODUCT_EXECUTION
        )
        if (
            registry.verify(receipt, expected_issuer_class=issuer) is not None
            or durable.get(receipt.receipt_id) != serialize_live_receipt(receipt)
            or payload.scope != enrollment.scope
            or not payload.issued_at <= payload.terminal_at <= lease.issued_at <= now
            or payload.expires_at < lease.expires_at
            or payload.provenance
            != (
                ReceiptProvenance.HOST_AUTHORITY
                if issuer is TrustedIssuerClass.HOST_AUTHORITY
                else ReceiptProvenance.PRODUCT_OWNED
            )
        ):
            raise MetadataHostError("METADATA_HOST_RECEIPT_INVALID")
        current_required = not recovery or role not in {_AUTHORITY, _CHECK}
        if current_required and (
            now >= payload.expires_at or (recovery and payload.expires_at < lease.recovery_deadline)
        ):
            raise MetadataHostError("METADATA_HOST_RECEIPT_EXPIRED")
        if role in {"HOST_ENROLLMENT_RECEIPT", "CLI_AUTHENTICATION_RECEIPT"}:
            expected_gate = "SF-L01" if role == "HOST_ENROLLMENT_RECEIPT" else "SF-L02"
            if (
                not isinstance(payload, GateReceiptPayload)
                or payload.gate_id != expected_gate
                or payload.outcome != ReceiptOutcome.PASSED
                or payload.evidence_phase != EvidencePhase.LIVE_BASELINE
                or payload.effect_class
                != (
                    "HOST_CONTROL_PLANE"
                    if role == "HOST_ENROLLMENT_RECEIPT"
                    else "CLASSIFICATION_ONLY_READ"
                )
            ):
                raise MetadataHostError("METADATA_HOST_CLASSIFICATION_EVIDENCE_REQUIRED")
            if role == "HOST_ENROLLMENT_RECEIPT":
                observed = payload.enrollment
                pins = enrollment.classification_pins
                if observed is None or (
                    observed.alias_reference_sha256 != _sha(pins.alias.encode())
                    or observed.organization_id_sha256 != pins.org_fingerprint_sha256
                    or observed.instance_host_sha256 != pins.instance_host_sha256
                    or observed.edition_sha256 != pins.edition_sha256
                    or observed.environment_class != pins.environment_class
                    or observed.persona_fingerprint_sha256 != pins.actor_fingerprint_sha256
                ):
                    raise MetadataHostError("METADATA_HOST_CLASSIFICATION_EVIDENCE_REQUIRED")
        else:
            if not isinstance(payload, SupportingReceiptPayload):
                raise MetadataHostError("METADATA_HOST_RECEIPT_INVALID")
            expected_artifact = (
                _claim(enrollment)
                if role in {_AUTHORITY, _RECOVERY}
                else metadata_preflight_artifact_sha256(enrollment.intent, role)
            )
            if payload.artifact_sha256 != expected_artifact:
                raise MetadataHostError("METADATA_HOST_PREFLIGHT_BINDING_INVALID")
            if role in {_CHECK, _RESTORE} and payload.outcome != ReceiptOutcome.PASSED:
                raise MetadataHostError("METADATA_HOST_PREFLIGHT_REQUIRED")
            if role in {_AUTHORITY, _RECOVERY}:
                if payload.authorized_gate_ids != (
                    ("SF-C03",) if role == _AUTHORITY else ("SF-C06",)
                ) or payload.authorized_effect_classes != (
                    ("METADATA_MUTATION",) if role == _AUTHORITY else ("RESTORE_MUTATION",)
                ):
                    raise MetadataHostError("METADATA_HOST_OPERATION_SCOPE_INVALID")
                if role == _RECOVERY:
                    permit = payload.recovery_permit
                    if permit is None or (
                        permit.deployment_gate_id != "SF-C03"
                        or permit.recovery_gate_id != "SF-C06"
                        or permit.restore_scope_sha256 != enrollment.scope.restore_scope_sha256
                        or permit.operation_plan_sha256 != enrollment.scope.operation_plan_sha256
                        or permit.valid_through != lease.recovery_deadline
                    ):
                        raise MetadataHostError("METADATA_HOST_RECOVERY_PERMIT_INVALID")


def resolve_metadata_cli_pins(expected: MetadataCliPins) -> PinnedCliLaunch:
    """Offline installed-file inspection; pins exact native launcher and CLI package version."""
    launch = tuple(_resolve_launch("sf", ("--version",)))
    if (
        os.name != "nt"
        or len(launch) != 4
        or launch[1] != "--no-deprecation"
        or launch[-1] != "--version"
    ):
        raise MetadataHostError("METADATA_HOST_NATIVE_LAUNCH_UNSUPPORTED")
    node, entrypoint = Path(launch[0]), Path(launch[2])
    package = entrypoint.parent.parent / "package.json"
    content = _read_nofollow(package, maximum_bytes=1_048_576)
    document = _closed_json(content)
    if document.get("name") != "@salesforce/cli" or document.get("version") != expected.version:
        raise MetadataHostError("METADATA_HOST_CLI_VERSION_MISMATCH")
    pin = PinnedCliLaunch(
        prefix=launch[:-1],
        files=(
            (node, expected.node_sha256),
            (entrypoint, expected.entrypoint_sha256),
            (package, expected.package_sha256),
        ),
    )
    pin.require(launch)
    return pin


class MetadataFenceStore:
    """Trusted independent SQLite authority state, never under the rollbackable journal.

    Initialization/registration are explicit host enrollment actions. Factory and recovery
    never recreate a missing database or lease row. HMAC-protected state is atomically replaced
    with synchronous FULL transactions. Protect this store/key separately from candidate data.
    """

    def __init__(self, path: Path, *, store_id: str, key: bytes):
        if not re.fullmatch(r"[a-f0-9]{64}", store_id) or len(key) < 32:
            raise MetadataHostError("METADATA_HOST_STATE_INVALID")
        self.path, self.store_id, self.key = path, store_id, key

    def _seal(self, value: dict) -> str:
        return hmac.new(self.key, _canonical(value), hashlib.sha256).hexdigest()

    def initialize(self) -> None:
        _write_exclusive(self.path, b"")
        with closing(sqlite3.connect(self.path)) as database, database:
            database.execute("PRAGMA synchronous=FULL")
            database.execute(
                "CREATE TABLE metadata_host_identity "
                "(identity TEXT PRIMARY KEY, seal TEXT NOT NULL)"
            )
            database.execute(
                "CREATE TABLE metadata_host_leases "
                "(lease TEXT PRIMARY KEY, body TEXT NOT NULL, seal TEXT NOT NULL)"
            )
            database.execute(
                "INSERT INTO metadata_host_identity VALUES (?,?)",
                (self.store_id, self._seal({"store_id": self.store_id})),
            )

    def register(self, lease: MetadataRecoveryLease) -> None:
        body = {
            "lease": _root(lease),
            "store_id": self.store_id,
            "claims": {},
            "quiescent": True,
            "pending": None,
            "sequence": 0,
        }
        with self._database() as database:
            database.execute(
                "INSERT INTO metadata_host_leases VALUES (?,?,?)",
                (_root(lease), _canonical(body).decode(), self._seal(body)),
            )

    def _database(self):
        return _FenceConnection(self)

    def _update(self, lease: MetadataRecoveryLease, action: Callable[[dict], object]):
        with self._database() as database:
            row = database.execute(
                "SELECT body,seal FROM metadata_host_leases WHERE lease=?", (_root(lease),)
            ).fetchone()
            if row is None:
                raise MetadataHostError("METADATA_HOST_STATE_NOT_ENROLLED")
            body = json.loads(row[0])
            if (
                self._seal(body) != row[1]
                or body.get("lease") != _root(lease)
                or body.get("store_id") != self.store_id
            ):
                raise MetadataHostError("METADATA_HOST_STATE_INVALID")
            result = action(body)
            body["sequence"] += 1
            database.execute(
                "UPDATE metadata_host_leases SET body=?,seal=? WHERE lease=?",
                (_canonical(body).decode(), self._seal(body), _root(lease)),
            )
            return result

    def claim(self, lease: MetadataRecoveryLease, operation: str, package: str) -> str:
        def action(body):
            if operation in body["claims"]:
                raise MetadataHostError("METADATA_HOST_DISPATCH_ALREADY_CLAIMED")
            root = _sha(
                _canonical({"lease": _root(lease), "operation": operation, "package": package})
            )
            body["claims"][operation] = root
            return root

        return self._update(lease, action)

    def fence(self, lease: MetadataRecoveryLease, operation: str) -> str | None:
        return self._update(lease, lambda body: body["claims"].get(operation))

    def require_quiescent(self, lease: MetadataRecoveryLease) -> None:
        def action(body):
            if body["quiescent"] is not True or body["pending"] is not None:
                raise MetadataHostError("METADATA_HOST_PROCESS_NOT_QUIESCENT")

        self._update(lease, action)

    def begin_process(self, lease: MetadataRecoveryLease, operation: str) -> str:
        def action(body):
            if body["quiescent"] is not True or body["pending"] is not None:
                raise MetadataHostError("METADATA_HOST_PROCESS_NOT_QUIESCENT")
            nonce = uuid4().hex
            body.update(quiescent=None, pending={"operation": operation, "nonce": nonce})
            return nonce

        return self._update(lease, action)

    def finish_process(
        self, lease: MetadataRecoveryLease, operation: str, nonce: str, quiescent: bool
    ) -> None:
        def action(body):
            if (
                type(quiescent) is not bool
                or body["pending"] != {"operation": operation, "nonce": nonce}
                or body["quiescent"] is not None
            ):
                raise MetadataHostError("METADATA_HOST_PROCESS_PROOF_INVALID")
            body.update(quiescent=quiescent, pending=None)

        self._update(lease, action)


class _FenceConnection:
    def __init__(self, store: MetadataFenceStore):
        self.store, self.database, self.guards = store, None, None

    def __enter__(self):
        self.guards = _DirectoryGuards(self.store.path.parent)
        try:
            before = _checked_identity(self.store.path, directory=False)
            self.database = sqlite3.connect(
                f"{self.store.path.as_uri()}?mode=rw", uri=True, timeout=2
            )
            self.database.execute("PRAGMA synchronous=FULL")
            self.database.execute("BEGIN IMMEDIATE")
            if _checked_identity(self.store.path, directory=False)[:2] != before[:2]:
                raise MetadataHostError("METADATA_HOST_STATE_INVALID")
            rows = self.database.execute(
                "SELECT identity,seal FROM metadata_host_identity"
            ).fetchall()
            if rows != [(self.store.store_id, self.store._seal({"store_id": self.store.store_id}))]:
                raise MetadataHostError("METADATA_HOST_STATE_INVALID")
            return self.database
        except Exception:
            self.__exit__(Exception, None, None)
            raise MetadataHostError("METADATA_HOST_STATE_UNAVAILABLE") from None

    def __exit__(self, kind, _value, _traceback):
        try:
            if self.database is not None:
                self.database.rollback() if kind else self.database.commit()
        finally:
            if self.database is not None:
                self.database.close()
            if self.guards is not None:
                self.guards.close()


class _MetadataAuthority:
    def __init__(
        self,
        *,
        enrollment,
        settings,
        repository_root,
        registry,
        ledger,
        store,
        runner,
        source_intent,
        clock,
    ):
        self.enrollment, self.settings, self.root = enrollment, settings, repository_root
        self.registry, self.ledger, self.store, self.runner = registry, ledger, store, runner
        self.source_intent, self.clock = source_intent, clock
        self.pending = None

    def _current(self, *, recovery=False):
        document = _read_nofollow(self.root / self.settings.enrollment_path, maximum_bytes=262_144)
        if (
            _sha(document) != self.settings.enrollment_sha256
            or self.source_intent() != self.enrollment.intent
            or _sha(_read_nofollow(Path(__file__), maximum_bytes=262_144))
            != self.enrollment.host_implementation_sha256
        ):
            raise MetadataHostError("METADATA_HOST_ENROLLMENT_OR_SOURCE_CHANGED")
        verify_metadata_enrollment(
            self.enrollment,
            registry=self.registry,
            ledger=self.ledger,
            now=self.clock(),
            recovery=recovery,
        )

    def verify(self, intent, lease, binding, operation, package_sha256, now):
        recovery = operation in {
            "PREPARE_RECOVERY",
            "READ_RECOVERY",
            "RESTORE",
            "CANCEL_FORWARD_JOB",
        } or (operation == "REPORT_JOB" and aware_utc(now) >= lease.expires_at)
        self._current(recovery=recovery)
        if (
            intent != self.enrollment.intent
            or lease != self.enrollment.lease
            or operation not in _OPERATIONS
            or _root(binding) != lease.execution_binding_sha256
        ):
            raise MetadataHostError("METADATA_HOST_SCOPE_MISMATCH")
        expected_package = (
            intent.expected_preimage_sha256
            if operation == "RESTORE"
            else intent.expected_candidate_sha256
            if operation in {"CHECK_ONLY", "DEPLOY"}
            else None
        )
        if package_sha256 != expected_package:
            raise MetadataHostError("METADATA_HOST_PACKAGE_SCOPE_MISMATCH")
        self.store.require_quiescent(lease)

    def claim_dispatch_once(self, intent, lease, operation, package_sha256, now):
        self._current(recovery=operation == "RESTORE")
        if (
            intent != self.enrollment.intent
            or lease != self.enrollment.lease
            or operation not in {"CHECK_ONLY", "DEPLOY", "RESTORE"}
        ):
            raise MetadataHostError("METADATA_HOST_SCOPE_MISMATCH")
        return self.store.claim(lease, operation, package_sha256)

    def read_dispatch_fence(self, intent, lease, operation):
        if intent != self.enrollment.intent or lease != self.enrollment.lease:
            raise MetadataHostError("METADATA_HOST_SCOPE_MISMATCH")
        return self.store.fence(lease, operation)

    def note_process_state(self, intent, lease, operation, quiescent):
        if intent != self.enrollment.intent or lease != self.enrollment.lease:
            raise MetadataHostError("METADATA_HOST_SCOPE_MISMATCH")
        if quiescent is None:
            self.pending = (operation, self.store.begin_process(lease, operation))
        elif self.pending is not None and self.pending[0] == operation:
            self.store.finish_process(lease, operation, self.pending[1], quiescent)
            self.pending = None
        else:
            raise MetadataHostError("METADATA_HOST_PROCESS_PROOF_INVALID")

    def require_recovery_quiescence(self, intent, lease):
        if intent != self.enrollment.intent or lease != self.enrollment.lease:
            raise MetadataHostError("METADATA_HOST_SCOPE_MISMATCH")
        self.store.require_quiescent(lease)


class MetadataRecoveryHost:
    """Zero-argument live entry points; all scope and ports are fixed by the host factory."""

    def __init__(
        self,
        adapter: HostMetadataRecoveryAdapter,
        enrollment: MetadataHostEnrollment,
        candidate_window=None,
    ):
        self._adapter, self._enrollment = adapter, enrollment
        self._candidate_window = candidate_window

    def run(self) -> MetadataRecoveryReport:
        return self._adapter.run(
            self._enrollment.intent, self._enrollment.lease, candidate_window=self._candidate_window
        )

    def recover(self) -> MetadataRecoveryReport:
        return self._adapter.recover(self._enrollment.intent, self._enrollment.lease)


class _ClassifiedMetadataRunner:
    """Fixed factory-owned wrapper; one classification group shares the operation timeout."""

    def __init__(self, authority: _MetadataAuthority):
        self.authority = authority
        if type(authority.runner) is not SubprocessCliRunner or authority.runner.launch_pin is None:
            raise MetadataHostError("METADATA_HOST_REAL_PINNED_RUNNER_REQUIRED")

    def run(self, invocation: CliInvocation) -> CliCompleted:
        authority = self.authority
        lease = authority.enrollment.lease
        quiescent = True
        if authority.pending is None:
            return CliCompleted(-1, b"")
        operation = authority.pending[0]
        recovery = operation in {"READ_RECOVERY", "RESTORE", "CANCEL_FORWARD_JOB"} or (
            operation == "REPORT_JOB" and aware_utc(authority.clock()) >= lease.expires_at
        )
        deadline = min(
            aware_utc(authority.clock()) + timedelta(seconds=invocation.timeout_seconds),
            lease.recovery_deadline if recovery else lease.expires_at,
        )

        def run(arguments, maximum, extra_deadline=deadline):
            nonlocal quiescent
            authority._current(recovery=recovery)
            timeout = (min(deadline, extra_deadline) - aware_utc(authority.clock())).total_seconds()
            if timeout <= 0 or not quiescent:
                raise MetadataHostError("METADATA_HOST_PROCESS_OR_AUTHORITY_BLOCKED")
            quiescent = False
            result = authority.runner.run(CliInvocation(arguments, timeout, maximum))
            quiescent = result.quiescent
            if not quiescent or result.output_exceeded or len(result.stdout) > maximum:
                raise MetadataHostError("METADATA_HOST_PROCESS_OR_OUTPUT_BLOCKED")
            return result

        def classify(arguments, authority_deadline, maximum):
            result = run(arguments, maximum, authority_deadline)
            if result.returncode or result.timed_out:
                raise MetadataHostError("METADATA_HOST_CLASSIFICATION_FAILED")
            return _closed_json(result.stdout)

        try:
            capture_classified_identity(
                pins=authority.enrollment.classification_pins,
                authority=authority.enrollment.classification_authority,
                invoke=classify,
                clock=authority.clock,
            )
            return run(invocation.arguments, invocation.maximum_stdout_bytes)
        except Exception:
            return CliCompleted(-1, b"", quiescent=quiescent)


@_sanitize_failure
def create_metadata_recovery_host(
    *,
    repository_root: Path,
    settings: MetadataHostSettings,
    registry: TrustedIssuerRegistry,
    ledger: LiveReceiptLedger,
    source_intent: Callable[[], MetadataPropertyIntent],
    state_key: bytes,
    journal_key: bytes,
    candidate_window: Callable[[str], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MetadataRecoveryHost:
    """Offline factory. Does not create an enrollment/store, sign proof or call Salesforce."""
    settings = MetadataHostSettings.model_validate_json(settings.model_dump_json(warnings=False))
    if not settings.enabled:
        raise MetadataHostError("METADATA_HOST_DISABLED")
    document = _read_nofollow(repository_root / settings.enrollment_path, maximum_bytes=262_144)
    if _sha(document) != settings.enrollment_sha256:
        raise MetadataHostError("METADATA_HOST_ENROLLMENT_PIN_MISMATCH")
    enrollment = MetadataHostEnrollment.model_validate(_closed_json(document))
    if enrollment.host_implementation_sha256 != _sha(
        _read_nofollow(Path(__file__), maximum_bytes=262_144)
    ):
        raise MetadataHostError("METADATA_HOST_IMPLEMENTATION_PIN_MISMATCH")
    if (
        enrollment.task_authority_sha256 != settings.expected_task_authority_sha256
        or source_intent() != enrollment.intent
    ):
        raise MetadataHostError("METADATA_HOST_ENROLLMENT_OR_SOURCE_CHANGED")
    verify_metadata_enrollment(
        enrollment, registry=registry, ledger=ledger, now=clock(), recovery=True
    )
    config = MetadataRecoveryConfig(
        alias=enrollment.classification_pins.alias,
        expected_cli_version=enrollment.cli.version,
        mutation_enabled=True,
    )
    if (
        _root(metadata_execution_binding(config, enrollment.intent.api_version))
        != enrollment.lease.execution_binding_sha256
    ):
        raise MetadataHostError("METADATA_HOST_IMPLEMENTATION_PIN_MISMATCH")
    launch_pin = resolve_metadata_cli_pins(enrollment.cli)
    runner = SubprocessCliRunner(launch_pin=launch_pin)
    store_path = repository_root / settings.state_path
    if config.journal_root == settings.state_path or store_path.is_relative_to(
        repository_root / config.journal_root
    ):
        raise MetadataHostError("METADATA_HOST_INDEPENDENT_STATE_REQUIRED")
    store = MetadataFenceStore(store_path, store_id=enrollment.state_store_id, key=state_key)
    store.require_quiescent(enrollment.lease)
    authority = _MetadataAuthority(
        enrollment=enrollment,
        settings=settings,
        repository_root=repository_root,
        registry=registry,
        ledger=ledger,
        store=store,
        runner=runner,
        source_intent=source_intent,
        clock=clock,
    )
    adapter = HostMetadataRecoveryAdapter(
        repository_root,
        config,
        authority,
        runner=_ClassifiedMetadataRunner(authority),
        journal_authentication_key=journal_key,
        now=clock,
    )
    return MetadataRecoveryHost(adapter, enrollment, candidate_window)


def serialize_reviewed_metadata_enrollment(
    enrollment: MetadataHostEnrollment,
    *,
    registry: TrustedIssuerRegistry,
    ledger: LiveReceiptLedger,
    now: datetime,
) -> bytes:
    """Produce private enrollment bytes only after independent live evidence replay.

    The operator separately writes/pins this document and enables host Settings. This function
    neither writes files nor issues/replaces any prerequisite receipt or authority.
    """
    enrollment = MetadataHostEnrollment.model_validate_json(
        enrollment.model_dump_json(warnings=False)
    )
    verify_metadata_enrollment(enrollment, registry=registry, ledger=ledger, now=now)
    return enrollment.model_dump_json(warnings=False).encode()
