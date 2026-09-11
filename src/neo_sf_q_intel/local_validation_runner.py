"""Host-owned, isolated execution of compiler-declared local Node validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from neo_sf_q_intel.candidate_target_compiler import CandidateTargetCompilation
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.live_receipt_ledger import LiveReceiptLedger, serialize_live_receipt
from neo_sf_q_intel.live_receipts import (
    ReceiptOutcome,
    ReceiptProvenance,
    ReceiptScope,
    SupportingReceiptPayload,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
    sign_live_receipt,
)
from neo_sf_q_intel.local_validation import (
    MAX_LOCAL_VALIDATION_ARTIFACT_BYTES,
    ROLE,
    LocalValidationArtifact,
    LocalValidationArtifactStore,
    LocalValidationEvidence,
    LocalValidationFileRoot,
    LocalValidationGeneratedOutput,
    LocalValidationInvocation,
    LocalValidationRunnerPins,
    LocalValidationVerificationError,
    StoredLocalValidationArtifact,
    serialize_local_validation_artifact,
    verify_local_validation_evidence,
)
from neo_sf_q_intel.safety import require_safe_repository_locator
from neo_sf_q_intel.subprocess_environment import build_subprocess_environment
from neo_sf_q_intel.temporal import aware_utc

_NODE_VERSION = re.compile(rb"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_HEX = re.compile(r"^[a-f0-9]{64}$")
_MAX_PROCESS_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024 * 1024
_NODE_TEST_PROTOCOL = "neo-node-test-events-v1"
_NODE_TEST_DRIVER = b"""\
import { createHmac } from 'node:crypto';
import { writeSync } from 'node:fs';
import { run } from 'node:test';
const safeWrite = writeSync.bind(undefined, 1);
const file = process.argv[2];
const authenticationKey = process.env.NEO_LOCAL_RESULT_KEY;
const stage = process.argv[3];
const normalized = file.replaceAll('\\\\', '/');
const terminal = [];
const childEnv = {};
const allowedEnv = [
  'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC',
  'TMP', 'TEMP', 'TMPDIR', 'LANG'
];
for (const name of allowedEnv) {
  if (process.env[name] !== undefined) childEnv[name] = process.env[name];
}
const stream = run({
  files: [file],
  isolation: 'process',
  concurrency: 1,
  execArgv: ['--permission', `--allow-fs-read=${stage}`],
  argv: [],
  env: childEnv
});
let summary = null;
const isWrapper = (event) => {
  const name = String(event.name).replaceAll('\\\\', '/');
  return event.nesting === 0 && (name === normalized || name.endsWith('/' + normalized));
};
stream.on('test:pass', (event) => {
  if (!isWrapper(event) && event.details?.type !== 'suite') terminal.push(['pass', event]);
});
stream.on('test:fail', (event) => {
  if (!isWrapper(event) && event.details?.type !== 'suite') terminal.push(['fail', event]);
});
stream.on('test:summary', (event) => { summary = event; });
stream.on('error', () => { process.exitCode = 1; });
stream.on('end', () => {
  if (summary === null) { process.exitCode = 1; return; }
  let passed = 0, failed = 0, skipped = 0, todo = 0;
  for (const [kind, event] of terminal) {
    if (event.skip) skipped += 1;
    else if (event.todo) todo += 1;
    else if (kind === 'pass') passed += 1;
    else failed += 1;
  }
  const value = {
    protocol: 'neo-node-test-events-v1',
    complete: summary.success === true,
    tests: terminal.length,
    pass: passed,
    fail: failed,
    cancelled: summary.counts.cancelled,
    skipped,
    todo
  };
  const document = Buffer.from(JSON.stringify(value), 'utf8');
  const mac = createHmac('sha256', Buffer.from(authenticationKey, 'hex'))
    .update(document).digest('hex');
  safeWrite(JSON.stringify({ document: document.toString('base64'), mac }));
});
stream.resume();
"""


class LocalValidationRunError(RuntimeError):
    """A sanitized refusal; process output and host paths never cross this boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class NodeInvocation:
    arguments: tuple[str, ...]
    working_directory: Path
    timeout_seconds: float
    maximum_output_bytes: int
    result_authentication_key: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class NodeCompleted:
    returncode: int
    stdout: bytes
    timed_out: bool = False
    output_exceeded: bool = False
    quiescent: bool = True


class NodeRunner(Protocol):
    def version(self, *, timeout_seconds: float) -> str: ...

    def run(self, invocation: NodeInvocation) -> NodeCompleted: ...


@dataclass(frozen=True, slots=True)
class SubprocessNodeRunner:
    """Run one fixed Node executable without a shell and terminate its whole process tree."""

    executable_locator: str = "node"

    def version(self, *, timeout_seconds: float) -> str:
        completed = self.run(
            NodeInvocation(
                arguments=("--version",),
                working_directory=Path(tempfile.gettempdir()).resolve(),
                timeout_seconds=timeout_seconds,
                maximum_output_bytes=128,
            )
        )
        raw = completed.stdout.strip()
        if (
            completed.returncode != 0
            or completed.timed_out
            or completed.output_exceeded
            or not completed.quiescent
            or _NODE_VERSION.fullmatch(raw) is None
        ):
            raise LocalValidationRunError("LOCAL_NODE_RUNTIME_INVALID")
        return raw[1:].decode("ascii")

    def run(self, invocation: NodeInvocation) -> NodeCompleted:
        # Candidate-controlled Node test output is not an authority channel.  The
        # native test runner parses child stdout, so authenticating its parsed
        # summary cannot establish that any tests actually ran.  Keep the real
        # production path closed until a host-owned validator replaces it.
        if invocation.result_authentication_key is not None:
            raise LocalValidationRunError("LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED")
        if any(value.split("=", 1)[0] == "--allow-child-process" for value in invocation.arguments):
            raise LocalValidationRunError("LOCAL_CHILD_PROCESS_PERMISSION_FORBIDDEN")
        if (
            self.executable_locator != "node"
            or not invocation.arguments
            or len(invocation.arguments) > 128
            or not invocation.working_directory.is_absolute()
            or invocation.timeout_seconds <= 0
            or not 1 <= invocation.maximum_output_bytes <= _MAX_PROCESS_OUTPUT_BYTES
            or any(not _safe_process_argument(value) for value in invocation.arguments)
        ):
            raise LocalValidationRunError("LOCAL_COMMAND_CONTRACT_INVALID")
        executable = shutil.which(self.executable_locator)
        if executable is None:
            raise LocalValidationRunError("LOCAL_NODE_RUNTIME_UNAVAILABLE")
        launch = _node_launch(Path(executable).resolve(), invocation.arguments)
        deadline = time.monotonic() + invocation.timeout_seconds
        containment = _ProcessContainment()
        process: subprocess.Popen[bytes] | None = None
        try:
            containment.prepare()
            environment = _node_environment()
            process = subprocess.Popen(
                launch,
                cwd=invocation.working_directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=environment,
                creationflags=(0x00000004 | 0x08000000) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            containment.attach_and_resume(process)
            return _collect_process(process, containment, invocation, deadline)
        except LocalValidationRunError:
            raise
        except OSError:
            if process is not None:
                with suppress(OSError):
                    process.kill()
            raise LocalValidationRunError("LOCAL_PROCESS_START_FAILED") from None
        finally:
            if process is not None:
                with suppress(OSError):
                    containment.terminate_and_wait(process, seconds=5)
            containment.close()


class SQLiteLocalValidationArtifactStore:
    """Immutable exact-byte artifact storage; it grants no execution or acceptance authority."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def setup(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_validation_artifacts (
                    artifact_sha256 TEXT PRIMARY KEY,
                    document_sha256 TEXT NOT NULL UNIQUE,
                    document_bytes BLOB NOT NULL,
                    appended_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def append(self, artifact_document: bytes) -> StoredLocalValidationArtifact:
        parsed = _parse_artifact_document(artifact_document)
        digest = hashlib.sha256(artifact_document).hexdigest()
        now = datetime.now(UTC)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT document_sha256,document_bytes,appended_at "
                "FROM local_validation_artifacts WHERE artifact_sha256=?",
                (parsed.artifact_sha256,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO local_validation_artifacts "
                    "(artifact_sha256,document_sha256,document_bytes,appended_at) "
                    "VALUES (?,?,?,?)",
                    (parsed.artifact_sha256, digest, artifact_document, now.isoformat()),
                )
                connection.commit()
                row = (digest, artifact_document, now.isoformat())
            elif row[0] != digest or bytes(row[1]) != artifact_document:
                connection.rollback()
                raise LocalValidationRunError("LOCAL_ARTIFACT_IMMUTABILITY_CONFLICT")
            else:
                connection.commit()
        return StoredLocalValidationArtifact(
            artifact_sha256=parsed.artifact_sha256,
            document_sha256=row[0],
            document_bytes=bytes(row[1]),
            appended_at=datetime.fromisoformat(row[2]),
        )

    def read(self, *, artifact_sha256: str) -> StoredLocalValidationArtifact:
        if _HEX.fullmatch(artifact_sha256) is None:
            raise LocalValidationRunError("LOCAL_ARTIFACT_ID_INVALID")
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT document_sha256,document_bytes,appended_at "
                "FROM local_validation_artifacts WHERE artifact_sha256=?",
                (artifact_sha256,),
            ).fetchone()
        if row is None:
            raise LocalValidationRunError("LOCAL_ARTIFACT_NOT_FOUND")
        try:
            return StoredLocalValidationArtifact(
                artifact_sha256=artifact_sha256,
                document_sha256=row[0],
                document_bytes=bytes(row[1]),
                appended_at=datetime.fromisoformat(row[2]),
            )
        except Exception:
            raise LocalValidationRunError("LOCAL_ARTIFACT_CORRUPT") from None


class HostLocalValidationService:
    """Execute every compiler-required local obligation against one exact private snapshot."""

    def __init__(
        self,
        *,
        repository_root: Path,
        pins: LocalValidationRunnerPins,
        ledger: LiveReceiptLedger,
        artifact_store: LocalValidationArtifactStore,
        product_issuer: TrustedIssuer,
        runner: NodeRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        root = repository_root.resolve()
        if not root.is_absolute() or not root.is_dir() or _is_reparse(root.lstat()):
            raise ValueError("Local validation repository root is invalid")
        if (
            product_issuer.issuer_class is not TrustedIssuerClass.PRODUCT_EXECUTION
            or ROLE not in product_issuer.allowed_receipt_roles
        ):
            raise ValueError("Local validation requires the exact product execution issuer")
        self.repository_root = root
        self.pins = LocalValidationRunnerPins.model_validate(pins.model_dump(mode="python"))
        if self.pins.implementation_sha256 != local_validation_runner_implementation_sha256():
            raise ValueError("Local validation runner implementation pin is stale")
        self.ledger = ledger
        self.artifact_store = artifact_store
        self.product_issuer = product_issuer
        self.runner = runner or SubprocessNodeRunner(self.pins.node_executable_locator)
        self.clock = clock or (lambda: datetime.now(UTC))

    def recover(
        self,
        compilation: CandidateTargetCompilation,
        *,
        scope: ReceiptScope,
    ) -> tuple[LocalValidationEvidence, ...] | None:
        """Recover only a complete, current, independently replay-verified proof set."""

        try:
            required_ids = {
                item.obligation.obligation_id for item in compilation.required_local_validations
            }
            if not required_ids:
                return ()
            by_obligation: dict[str, LocalValidationEvidence] = {}
            records = self.ledger.replay(campaign_id=scope.campaign_id)
            receipts = sorted(
                (item.parsed_receipt() for item in records),
                key=lambda item: item.payload.terminal_at,
                reverse=True,
            )
            for receipt in receipts:
                payload = receipt.payload
                if (
                    not isinstance(payload, SupportingReceiptPayload)
                    or receipt.receipt_role != ROLE
                    or payload.scope != scope
                    or payload.artifact_sha256 is None
                ):
                    continue
                artifact = _parse_artifact_document(
                    self.artifact_store.read(artifact_sha256=payload.artifact_sha256).document_bytes
                )
                if artifact.obligation_id in required_ids:
                    by_obligation.setdefault(
                        artifact.obligation_id,
                        LocalValidationEvidence(artifact=artifact, receipt=receipt),
                    )
            if set(by_obligation) != required_ids:
                return None
            evidence = tuple(by_obligation[key] for key in sorted(by_obligation))
            verify_local_validation_evidence(
                compilation,
                evidence,
                scope=scope,
                registry=TrustedIssuerRegistry((self.product_issuer,)),
                ledger=self.ledger,
                artifact_store=self.artifact_store,
                runner_pins=self.pins,
                observed_at=_utc_now(self.clock),
            )
            return evidence
        except Exception:
            return None

    def run(
        self,
        compilation: CandidateTargetCompilation,
        *,
        scope: ReceiptScope,
    ) -> tuple[LocalValidationEvidence, ...]:
        """Run the complete closed obligation set; callers cannot select a subset or command."""

        try:
            current = CandidateTargetCompilation.model_validate(
                compilation.model_dump(mode="python")
            )
            if not current.required_local_validations:
                return ()
            if (
                scope.candidate_sha256 != current.candidate_bundle_sha256
                or scope.project_id != current.verified_scope.project_id
                or scope.source_contract_sha256 != current.source_contract_sha256
            ):
                raise LocalValidationRunError("LOCAL_VALIDATION_SCOPE_MISMATCH")
            requirements = current.required_local_validations
            canonical_files = requirements[0].bound_files
            tree_sha256 = requirements[0].candidate_tree_sha256
            if any(
                item.bound_files != canonical_files or item.candidate_tree_sha256 != tree_sha256
                for item in requirements
            ):
                raise LocalValidationRunError("LOCAL_CANDIDATE_MANIFEST_MISMATCH")
            manifest = _capture_manifest(self.repository_root, canonical_files)
            started_global = time.monotonic()
            valid_until = min(
                datetime.fromisoformat(current.verified_scope.valid_until.replace("Z", "+00:00")),
                scope.recovery_deadline,
            )
            remaining_authority = (valid_until - _utc_now(self.clock)).total_seconds()
            if remaining_authority <= 0:
                raise LocalValidationRunError("LOCAL_CANDIDATE_CAPTURE_EXPIRED")
            node_version = self.runner.version(timeout_seconds=min(5, remaining_authority))
            _require_node_version(node_version, self.pins)
            observations = tuple(
                self._run_one(
                    current,
                    required,
                    manifest=manifest,
                    node_version=node_version,
                    global_started=started_global,
                    valid_until=valid_until,
                )
                for required in requirements
            )
            aggregate_output = sum(
                len(
                    json.dumps(
                        [value.model_dump(mode="json") for value in observation["invocations"]],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                + sum(value.size_bytes for value in observation["regenerated_outputs"])
                for observation in observations
            )
            if aggregate_output > self.pins.maximum_output_bytes:
                raise LocalValidationRunError("LOCAL_VALIDATION_OUTPUT_EXCEEDED")
            _require_source_unchanged(self.repository_root, canonical_files, manifest)
            # All observations receive one host/source-capped expiry only after the
            # complete batch succeeds. An early obligation cannot expire while a
            # later authorized obligation runs, and this never extends campaign time.
            if _utc_now(self.clock) >= valid_until:
                raise LocalValidationRunError("LOCAL_CANDIDATE_CAPTURE_EXPIRED")
            artifacts = tuple(
                LocalValidationArtifact(
                    **(body := {**observation, "expires_at": valid_until}),
                    artifact_sha256=stable_sha256(_jsonable_artifact_body(body)),
                )
                for observation in observations
            )
            # Persist/sign only after the complete closed set passes. A later
            # obligation failure therefore cannot leave a partial authoritative
            # proof set that a retry might accidentally treat as complete.
            evidence = tuple(self._persist(artifact, scope=scope) for artifact in artifacts)
            verify_local_validation_evidence(
                current,
                evidence,
                scope=scope,
                registry=TrustedIssuerRegistry((self.product_issuer,)),
                ledger=self.ledger,
                artifact_store=self.artifact_store,
                runner_pins=self.pins,
                observed_at=_utc_now(self.clock),
            )
            return evidence
        except LocalValidationRunError:
            raise
        except LocalValidationVerificationError:
            raise LocalValidationRunError("LOCAL_VALIDATION_EVIDENCE_INVALID") from None
        except Exception:
            raise LocalValidationRunError("LOCAL_VALIDATION_EXECUTION_FAILED") from None

    def _run_one(
        self,
        compilation: CandidateTargetCompilation,
        required: Any,
        *,
        manifest: Mapping[str, bytes],
        node_version: str,
        global_started: float,
        valid_until: datetime,
    ) -> dict[str, Any]:
        obligation = required.obligation
        if obligation.runner_kind not in self.pins.allowed_runner_kinds:
            raise LocalValidationRunError("LOCAL_RUNNER_KIND_NOT_ALLOWED")
        obligation_started = time.monotonic()
        if self.pins.maximum_global_timeout_seconds - (obligation_started - global_started) <= 0:
            raise LocalValidationRunError("LOCAL_VALIDATION_TIMEOUT")
        started_at = _utc_now(self.clock)
        with tempfile.TemporaryDirectory(prefix="neo-local-validation-") as raw_stage:
            stage = Path(raw_stage).resolve()
            _stage_manifest(stage, manifest)
            driver = (
                _create_node_test_driver(stage) if obligation.runner_kind == "NODE_TEST" else None
            )
            for locator in obligation.regenerated_locators:
                _remove_generated_output(stage, locator)
            try:
                invocations = []
                for locator in obligation.test_locators:
                    now = time.monotonic()
                    remaining = min(
                        float(obligation.maximum_seconds) - (now - obligation_started),
                        self.pins.maximum_global_timeout_seconds - (now - global_started),
                        (valid_until - _utc_now(self.clock)).total_seconds(),
                    )
                    if remaining <= 0:
                        raise LocalValidationRunError("LOCAL_VALIDATION_TIMEOUT")
                    completed = self.runner.run(
                        invocation := _node_invocation(
                            stage,
                            obligation,
                            locator,
                            node_test_driver=driver,
                            timeout_seconds=remaining,
                            maximum_output_bytes=min(
                                self.pins.maximum_output_bytes, _MAX_PROCESS_OUTPUT_BYTES
                            ),
                        )
                    )
                    invocations.append(
                        _invocation_result(
                            locator,
                            obligation.runner_kind,
                            completed,
                            authentication_key=invocation.result_authentication_key,
                        )
                    )
            finally:
                if driver is not None:
                    try:
                        driver_is_current = driver.read_bytes() == _NODE_TEST_DRIVER
                        driver.chmod(stat.S_IREAD | stat.S_IWRITE)
                        driver.unlink()
                    except OSError:
                        with suppress(OSError):
                            driver.chmod(stat.S_IREAD | stat.S_IWRITE)
                            driver.unlink()
                        raise LocalValidationRunError("LOCAL_TEST_DRIVER_CLEANUP_FAILED") from None
                    if not driver_is_current:
                        raise LocalValidationRunError("LOCAL_TEST_DRIVER_CHANGED")
            regenerated = tuple(
                _capture_generated_output(stage, locator, manifest)
                for locator in obligation.regenerated_locators
            )
            if _capture_stage_manifest(stage) != manifest:
                raise LocalValidationRunError("LOCAL_STAGED_TREE_CHANGED")
        _require_source_unchanged(self.repository_root, required.bound_files, manifest)
        terminal_at = _utc_now(self.clock)
        if valid_until <= terminal_at:
            raise LocalValidationRunError("LOCAL_CANDIDATE_CAPTURE_EXPIRED")
        roots = tuple(LocalValidationFileRoot(**item.model_dump()) for item in required.bound_files)
        invocation_tuple = tuple(invocations)
        return {
            "candidate_bundle_sha256": compilation.candidate_bundle_sha256,
            "candidate_tree_sha256": required.candidate_tree_sha256,
            "obligation_id": obligation.obligation_id,
            "command_contract_sha256": required.command_contract_sha256,
            "bound_files": roots,
            "runner_kind": obligation.runner_kind,
            "runner_id": self.pins.runner_id,
            "runner_version": self.pins.runner_version,
            "runner_implementation_sha256": self.pins.implementation_sha256,
            "node_executable_locator": self.pins.node_executable_locator,
            "node_version": node_version,
            "working_directory": obligation.working_directory,
            "test_locators": obligation.test_locators,
            "regenerated_outputs": regenerated,
            "invocations": invocation_tuple,
            "started_at": started_at,
            "terminal_at": terminal_at,
            "result_sha256": stable_sha256(
                [value.model_dump(mode="json") for value in invocation_tuple]
            ),
        }

    def _persist(
        self, artifact: LocalValidationArtifact, *, scope: ReceiptScope
    ) -> LocalValidationEvidence:
        artifact_document = serialize_local_validation_artifact(artifact)
        stored = self.artifact_store.append(artifact_document)
        if stored.document_bytes != artifact_document:
            raise LocalValidationRunError("LOCAL_ARTIFACT_NOT_DURABLE")
        receipt = sign_live_receipt(
            SupportingReceiptPayload(
                receipt_role=ROLE,
                scope=scope,
                provenance=ReceiptProvenance.PRODUCT_OWNED,
                outcome=ReceiptOutcome.PASSED,
                issued_at=artifact.started_at,
                terminal_at=artifact.terminal_at,
                expires_at=artifact.expires_at,
                artifact_sha256=artifact.artifact_sha256,
            ),
            issuer_id=self.product_issuer.issuer_id,
            hmac_key=self.product_issuer.hmac_key,
        )
        stored_receipt = self.ledger.append(serialize_live_receipt(receipt))
        if stored_receipt.receipt_id != receipt.receipt_id:
            raise LocalValidationRunError("LOCAL_RECEIPT_NOT_DURABLE")
        return LocalValidationEvidence(artifact=artifact, receipt=receipt)


def local_validation_runner_implementation_sha256() -> str:
    """Return the host-pin value for these exact runner implementation bytes."""

    return hashlib.sha256(
        Path(__file__).read_bytes() + b"\x00" + Path(__file__).with_name("temporal.py").read_bytes()
    ).hexdigest()


def _parse_artifact_document(document: bytes) -> LocalValidationArtifact:
    if (
        not isinstance(document, bytes)
        or not 2 <= len(document) <= MAX_LOCAL_VALIDATION_ARTIFACT_BYTES
    ):
        raise LocalValidationRunError("LOCAL_ARTIFACT_INVALID")
    try:
        artifact = LocalValidationArtifact.model_validate_json(document)
        if serialize_local_validation_artifact(artifact) != document:
            raise ValueError
        return artifact
    except Exception:
        raise LocalValidationRunError("LOCAL_ARTIFACT_INVALID") from None


def _strict_json_object(document: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in values:
            if key in parsed:
                raise ValueError("duplicate JSON key")
            parsed[key] = value
        return parsed

    parsed = json.loads(document.decode("utf-8", "strict"), object_pairs_hook=pairs)
    if not isinstance(parsed, dict):
        raise ValueError("JSON object required")
    return parsed


def _capture_manifest(root: Path, bindings: Any) -> dict[str, bytes]:
    captured: dict[str, bytes] = {}
    total = 0
    for binding in bindings:
        locator = binding.locator
        require_safe_repository_locator(locator)
        if locator in captured:
            raise LocalValidationRunError("LOCAL_CANDIDATE_MANIFEST_MISMATCH")
        content = _read_exact_file(root, locator)
        total += len(content)
        if (
            total > _MAX_MANIFEST_BYTES
            or len(content) != binding.size_bytes
            or hashlib.sha256(content).hexdigest() != binding.content_sha256
        ):
            raise LocalValidationRunError("LOCAL_CANDIDATE_MANIFEST_MISMATCH")
        captured[locator] = content
    if tuple(captured) != tuple(sorted(captured)):
        raise LocalValidationRunError("LOCAL_CANDIDATE_MANIFEST_MISMATCH")
    return captured


def _require_source_unchanged(root: Path, bindings: Any, expected: Mapping[str, bytes]) -> None:
    try:
        observed = _capture_manifest(root, bindings)
    except LocalValidationRunError:
        raise LocalValidationRunError("LOCAL_CANDIDATE_CHANGED_DURING_VALIDATION") from None
    if observed != dict(expected):
        raise LocalValidationRunError("LOCAL_CANDIDATE_CHANGED_DURING_VALIDATION")


def _read_exact_file(root: Path, locator: str) -> bytes:
    parts = PurePosixPath(locator).parts
    target = root.joinpath(*parts)
    _verify_parent_chain(root, parts[:-1])
    try:
        before = target.lstat()
        if _is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise LocalValidationRunError("LOCAL_SOURCE_ENTRY_UNSAFE")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                _is_reparse(opened)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise LocalValidationRunError("LOCAL_SOURCE_ENTRY_UNSAFE")
            chunks = []
            observed = 0
            while chunk := os.read(descriptor, 65_536):
                observed += len(chunk)
                if observed > _MAX_MANIFEST_BYTES:
                    raise LocalValidationRunError("LOCAL_MANIFEST_CAPACITY_EXCEEDED")
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ):
                raise LocalValidationRunError("LOCAL_SOURCE_CHANGED_DURING_READ")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except LocalValidationRunError:
        raise
    except OSError:
        raise LocalValidationRunError("LOCAL_SOURCE_ENTRY_UNSAFE") from None


def _verify_parent_chain(root: Path, parts: tuple[str, ...]) -> None:
    current = root
    root_metadata = root.lstat()
    if _is_reparse(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise LocalValidationRunError("LOCAL_SOURCE_ENTRY_UNSAFE")
    for part in parts:
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            raise LocalValidationRunError("LOCAL_SOURCE_ENTRY_UNSAFE") from None
        if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise LocalValidationRunError("LOCAL_SOURCE_ENTRY_UNSAFE")


def _stage_manifest(stage: Path, manifest: Mapping[str, bytes]) -> None:
    os.chmod(stage, 0o700)
    for locator, content in manifest.items():
        target = stage.joinpath(*PurePosixPath(locator).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        _verify_parent_chain(stage, PurePosixPath(locator).parts[:-1])
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        observed = target.lstat()
        if _is_reparse(observed) or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise LocalValidationRunError("LOCAL_STAGING_UNSAFE")
    if _capture_stage_manifest(stage) != dict(manifest):
        raise LocalValidationRunError("LOCAL_STAGING_MISMATCH")


def _remove_generated_output(stage: Path, locator: str) -> None:
    require_safe_repository_locator(locator)
    target = stage.joinpath(*PurePosixPath(locator).parts)
    metadata = target.lstat()
    if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise LocalValidationRunError("LOCAL_STAGING_UNSAFE")
    target.unlink()


def _capture_generated_output(
    stage: Path, locator: str, manifest: Mapping[str, bytes]
) -> LocalValidationGeneratedOutput:
    expected = manifest.get(locator)
    if expected is None:
        raise LocalValidationRunError("LOCAL_GENERATED_OUTPUT_UNDECLARED")
    content = _read_exact_file(stage, locator)
    if content != expected:
        raise LocalValidationRunError("LOCAL_GENERATED_OUTPUT_STALE")
    content.decode("utf-8", "strict")
    return LocalValidationGeneratedOutput(
        locator=locator,
        size_bytes=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=content,
    )


def _capture_stage_manifest(stage: Path) -> dict[str, bytes]:
    files = []
    for directory, names, filenames in os.walk(stage, topdown=True, followlinks=False):
        parent = Path(directory)
        for name in names:
            metadata = (parent / name).lstat()
            if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise LocalValidationRunError("LOCAL_STAGING_UNSAFE")
        for name in filenames:
            path = parent / name
            locator = path.relative_to(stage).as_posix()
            files.append(locator)
    return {locator: _read_exact_file(stage, locator) for locator in sorted(files)}


def _create_node_test_driver(stage: Path) -> Path:
    driver = stage / ".neo-host-node-test-driver.mjs"
    try:
        descriptor = os.open(driver, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_NODE_TEST_DRIVER)
            stream.flush()
            os.fsync(stream.fileno())
        if driver.read_bytes() != _NODE_TEST_DRIVER:
            raise LocalValidationRunError("LOCAL_TEST_DRIVER_INVALID")
        return driver
    except LocalValidationRunError:
        raise
    except Exception:
        raise LocalValidationRunError("LOCAL_TEST_DRIVER_INVALID") from None


def _node_invocation(
    stage: Path,
    obligation: Any,
    locator: str,
    *,
    node_test_driver: Path | None,
    timeout_seconds: float,
    maximum_output_bytes: int,
) -> NodeInvocation:
    require_safe_repository_locator(locator)
    work = stage.joinpath(*PurePosixPath(obligation.working_directory).parts)
    test = stage.joinpath(*PurePosixPath(locator).parts)
    try:
        test_argument = test.relative_to(work).as_posix()
    except ValueError:
        raise LocalValidationRunError("LOCAL_TEST_OUTSIDE_WORKING_DIRECTORY") from None
    if obligation.runner_kind == "NODE_TEST":
        if node_test_driver is None:
            raise LocalValidationRunError("LOCAL_TEST_DRIVER_INVALID")
        authentication_key = secrets.token_bytes(32)
        arguments = (
            "--permission",
            f"--allow-fs-read={stage}",
            str(node_test_driver),
            test_argument,
            str(stage),
        )
    elif obligation.runner_kind == "NODE_GENERATED_CHECK":
        write_roots = tuple(
            sorted(
                {
                    str(stage.joinpath(*PurePosixPath(value).parts).parent)
                    for value in obligation.regenerated_locators
                }
            )
        )
        arguments = (
            "--permission",
            f"--allow-fs-read={stage}",
            *(f"--allow-fs-write={value}" for value in write_roots),
            test_argument,
        )
    else:
        raise LocalValidationRunError("LOCAL_RUNNER_KIND_NOT_ALLOWED")
    return NodeInvocation(
        arguments,
        work,
        timeout_seconds,
        maximum_output_bytes,
        authentication_key if obligation.runner_kind == "NODE_TEST" else None,
    )


def _invocation_result(
    locator: str,
    runner_kind: str,
    completed: NodeCompleted,
    *,
    authentication_key: bytes | None = None,
) -> LocalValidationInvocation:
    if completed.timed_out:
        raise LocalValidationRunError("LOCAL_VALIDATION_TIMEOUT")
    if completed.output_exceeded:
        raise LocalValidationRunError("LOCAL_VALIDATION_OUTPUT_EXCEEDED")
    if not completed.quiescent:
        raise LocalValidationRunError("LOCAL_PROCESS_TREE_NOT_QUIESCENT")
    if completed.returncode != 0:
        raise LocalValidationRunError("LOCAL_VALIDATION_FAILED")
    if runner_kind == "NODE_GENERATED_CHECK":
        counts = {"tests": 1, "pass": 1, "fail": 0, "cancelled": 0, "skipped": 0, "todo": 0}
    else:
        try:
            if authentication_key is None or len(authentication_key) != 32:
                raise ValueError
            envelope = _strict_json_object(completed.stdout)
            if set(envelope) != {"document", "mac"}:
                raise ValueError
            document = base64.b64decode(envelope["document"], validate=True)
            if (
                not 2 <= len(document) <= 4096
                or not isinstance(envelope["mac"], str)
                or re.fullmatch(r"[a-f0-9]{64}", envelope["mac"]) is None
                or not hmac.compare_digest(
                    envelope["mac"],
                    hmac.new(authentication_key, document, hashlib.sha256).hexdigest(),
                )
            ):
                raise ValueError
            result = _strict_json_object(document)
            expected_keys = {
                "protocol",
                "complete",
                "tests",
                "pass",
                "fail",
                "cancelled",
                "skipped",
                "todo",
            }
            if (
                not isinstance(result, dict)
                or set(result) != expected_keys
                or result["protocol"] != _NODE_TEST_PROTOCOL
                or result["complete"] is not True
                or any(
                    not isinstance(result[name], int)
                    or isinstance(result[name], bool)
                    or not 0 <= result[name] <= 100000
                    for name in expected_keys - {"protocol", "complete"}
                )
            ):
                raise ValueError
            counts = {name: result[name] for name in expected_keys - {"protocol", "complete"}}
        except Exception:
            raise LocalValidationRunError("LOCAL_TEST_RESULT_INVALID") from None
    if (
        counts["tests"] < 1
        or counts["pass"] != counts["tests"]
        or any(counts[name] != 0 for name in ("fail", "cancelled", "skipped", "todo"))
    ):
        raise LocalValidationRunError("LOCAL_TEST_NOT_COMPLETE_PASS_NO_SKIP")
    return LocalValidationInvocation(
        test_locator=locator,
        complete=True,
        exit_code=completed.returncode,
        total_count=counts["tests"],
        passed_count=counts["pass"],
        failed_count=counts["fail"],
        skipped_count=counts["skipped"],
        cancelled_count=counts["cancelled"],
        todo_count=counts["todo"],
    )


def _require_node_version(version: str, pins: LocalValidationRunnerPins) -> None:
    try:
        parts = tuple(int(value) for value in version.split("."))
        minimum = tuple(int(value) for value in pins.node_minimum_version.split("."))
    except (TypeError, ValueError):
        raise LocalValidationRunError("LOCAL_NODE_RUNTIME_INVALID") from None
    if len(parts) != 3 or parts[0] != pins.node_expected_major or parts < minimum:
        raise LocalValidationRunError("LOCAL_NODE_RUNTIME_PIN_MISMATCH")


def _jsonable_artifact_body(body: Mapping[str, Any]) -> dict[str, Any]:
    draft = LocalValidationArtifact.model_construct(**body, artifact_sha256="0" * 64)
    return draft.model_dump(mode="json", exclude={"artifact_sha256"})


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    try:
        return aware_utc(clock())
    except ValueError:
        raise LocalValidationRunError("LOCAL_CLOCK_INVALID") from None


def _safe_process_argument(value: str) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 32_768
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value
    )


def _node_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NODE_EXTRA_CA_CERTS",
    }
    return {
        name: value for name, value in build_subprocess_environment().items() if name in allowed
    }


def _node_launch(executable: Path, arguments: tuple[str, ...]) -> list[str]:
    if executable.suffix.casefold() in {".cmd", ".bat"}:
        raise LocalValidationRunError("LOCAL_NODE_RUNTIME_UNAVAILABLE")
    return [str(executable), *arguments]


def _collect_process(
    process: subprocess.Popen[bytes],
    containment: _ProcessContainment,
    invocation: NodeInvocation,
    deadline: float,
) -> NodeCompleted:
    streams = (process.stdout, process.stderr)
    buffers = (bytearray(), bytearray())
    exceeded = threading.Event()

    def drain(index: int) -> None:
        stream = streams[index]
        if stream is None:
            return
        while chunk := stream.read(65_536):
            if len(buffers[index]) + len(chunk) > invocation.maximum_output_bytes:
                exceeded.set()
                return
            buffers[index].extend(chunk)

    threads = tuple(
        threading.Thread(target=drain, args=(index,), daemon=True) for index in range(2)
    )
    for thread in threads:
        thread.start()
    timed_out = False
    while process.poll() is None and not exceeded.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=min(0.05, remaining))
    quiescent = containment.terminate_and_wait(process, seconds=5)
    for thread in threads:
        thread.join(timeout=1)
    quiescent = quiescent and not any(thread.is_alive() for thread in threads)
    return NodeCompleted(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=b"" if timed_out or exceeded.is_set() or not quiescent else bytes(buffers[0]),
        timed_out=timed_out,
        output_exceeded=exceeded.is_set(),
        quiescent=quiescent,
    )


class _ProcessContainment:
    def __init__(self) -> None:
        self.job: Any = None
        self.kernel: Any = None
        self.accounting_type: Any = None

    def prepare(self) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(f"counter_{index}", ctypes.c_ulonglong) for index in range(6)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", IoCounters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("user", ctypes.c_longlong),
                ("kernel", ctypes.c_longlong),
                ("period_user", ctypes.c_longlong),
                ("period_kernel", ctypes.c_longlong),
                ("faults", wintypes.DWORD),
                ("total", wintypes.DWORD),
                ("active", wintypes.DWORD),
                ("terminated", wintypes.DWORD),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            (
                "SetInformationJobObject",
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            (
                "QueryInformationJobObject",
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
                wintypes.BOOL,
            ),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(kernel, name)
            function.argtypes = arguments
            function.restype = result
        self.kernel = kernel
        self.accounting_type = Accounting
        self.job = kernel.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000
        if not self.job or not kernel.SetInformationJobObject(
            self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise OSError("process containment unavailable")

    def attach_and_resume(self, process: subprocess.Popen[bytes]) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        handle = wintypes.HANDLE(int(process._handle))
        if not self.kernel.AssignProcessToJobObject(self.job, handle):
            raise OSError("process containment unavailable")
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = wintypes.LONG
        if resume(handle) != 0:
            raise OSError("contained process could not start")

    def terminate_and_wait(self, process: subprocess.Popen[bytes], *, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        if os.name == "nt":
            import ctypes

            if self.job is None or not self.kernel.TerminateJobObject(self.job, 1):
                return False
            while time.monotonic() < deadline:
                accounting = self.accounting_type()
                if not self.kernel.QueryInformationJobObject(
                    self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
                ):
                    return False
                if accounting.active == 0:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                    return True
                time.sleep(0.01)
            return False
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        return False

    def close(self) -> None:
        if self.job is not None:
            self.kernel.CloseHandle(self.job)
            self.job = None


def _is_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


__all__ = [
    "HostLocalValidationService",
    "LocalValidationRunError",
    "NodeCompleted",
    "NodeInvocation",
    "NodeRunner",
    "SQLiteLocalValidationArtifactStore",
    "SubprocessNodeRunner",
    "local_validation_runner_implementation_sha256",
]
