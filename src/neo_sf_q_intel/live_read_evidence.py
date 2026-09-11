"""Strict read-only Salesforce executor and durable live-receipt bridge.

The caller never supplies an alias, route, metadata member, or test name.  A host-owned
input port supplies an already verified target plan and current enrollment receipts.  Raw
Salesforce values are reduced to bounded digests before leaving this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from neo_sf_q_intel.candidate_target_compiler import ExpectedExecutionContract
from neo_sf_q_intel.classification_bootstrap import (
    ClassificationAuthority,
    ClassificationError,
    ClassificationPins,
    capture_classified_identity,
    verify_pinned_display_identity,
    verify_pinned_subject_identity,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.execution_assertions import (
    AssertionOutcome,
    AssertionSubjectKind,
    ExecutionAssertion,
    ExecutionAssertionArtifact,
    ExecutionAssertionStore,
    ExecutionToolVersion,
    build_execution_artifact_reference,
    execution_artifact_index_sha256,
    serialize_execution_assertion,
    sign_execution_assertion,
)
from neo_sf_q_intel.live_receipt_ledger import StoredLiveReceipt, serialize_live_receipt
from neo_sf_q_intel.live_receipt_producer import (
    GateExecutionEvidence,
    TrustedGateAppendAuthority,
    TrustedLiveReceiptProducer,
)
from neo_sf_q_intel.live_receipts import (
    EvidencePhase,
    GateReceiptPayload,
    ReceiptOutcome,
    SignedLiveReceipt,
    SupportingReceiptPayload,
    TrustedIssuerRegistry,
)
from neo_sf_q_intel.live_target_plan import (
    LiveTargetPlan,
    PlannedTarget,
    ResponseField,
    TargetPartition,
)
from neo_sf_q_intel.subprocess_environment import build_subprocess_environment
from neo_sf_q_intel.temporal import aware_utc, parse_aware_utc

_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_API_VERSION = re.compile(r"^v?[1-9][0-9]{0,2}(?:\.0)?$")
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,256}$")
_VARIABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,99}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,499}$")
_METADATA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
_METADATA_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,499}$")
_CLI_VERSION_OUTPUT = re.compile(rb"^@salesforce/cli/([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)")
_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_CLASSES = frozenset({"DEVELOPER_EDITION", "SANDBOX", "SCRATCH_ORG"})
_DENIED_METADATA = frozenset(
    {
        "connectedapp",
        "authprovider",
        "namedcredential",
        "externalcredential",
        "certificate",
        "certificateorkeymaterial",
    }
)
_GATE_BY_PARTITION = {
    TargetPartition.STANDARD_REST: "SF-L03",
    TargetPartition.CUSTOM_REST: "SF-L04",
    TargetPartition.METADATA: "SF-L05",
}
_LOGGER = logging.getLogger("neo_sf_q_intel.live_read_evidence")
_MAX_OPERATIONS = 256
_MAX_STDERR_BYTES = 65_536
_MAX_STAGE_DIRECTORIES = 128
_METADATA_FILE_LAYOUTS = {
    "ApexClass": ("classes", ".cls"),
    "ApexTrigger": ("triggers", ".trigger"),
    "CustomMetadata": ("customMetadata", ".md"),
    "Flow": ("flows", ".flow"),
    "FlexiPage": ("flexipages", ".flexipage"),
    "PermissionSet": ("permissionsets", ".permissionset"),
    "CustomObject": ("objects", ".object"),
    "Layout": ("layouts", ".layout"),
}
_CONTAINED_METADATA_TYPES = {"CustomField"}


class LiveReadCode(StrEnum):
    AUTHORITY_INVALID = "LIVE_READ_AUTHORITY_INVALID"
    IDENTITY_MISMATCH = "LIVE_READ_IDENTITY_MISMATCH"
    TARGET_INVALID = "LIVE_READ_TARGET_INVALID"
    CLI_FAILED = "LIVE_READ_CLI_FAILED"
    CLI_TIMEOUT = "LIVE_READ_CLI_TIMEOUT"
    OUTPUT_LIMIT = "LIVE_READ_OUTPUT_LIMIT"
    RESPONSE_INVALID = "LIVE_READ_RESPONSE_INVALID"
    METADATA_STAGING_INVALID = "LIVE_READ_METADATA_STAGING_INVALID"
    EXECUTION_FAILED = "LIVE_READ_EXECUTION_FAILED"
    RECEIPT_FAILED = "LIVE_READ_RECEIPT_FAILED"
    PROCESS_NOT_QUIESCENT = "LIVE_READ_PROCESS_NOT_QUIESCENT"


class LiveReadError(RuntimeError):
    """Secret-safe live-read failure."""

    def __init__(self, code: LiveReadCode) -> None:
        self.code = code
        super().__init__(code.value)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostLiveReadConfig(_Model):
    classification_authority: ClassificationAuthority
    alias: str
    environment_class: str
    org_fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor_fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor_user_id_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instance_host_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    edition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    api_version: str
    assertion_producer_id: str = Field(
        default="product-receipt-issuer", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
    )
    assertion_runner_id: str = Field(
        default="salesforce-live-read-runner",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
    )
    assertion_runner_key_id: str = Field(
        default="salesforce-live-read-runner",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
    )
    runner_version: str = Field(default="1.0.0", pattern=r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
    adapter_version: str = Field(default="1.0.0", pattern=r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
    salesforce_cli_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    command_timeout_seconds: int = Field(default=60, ge=1, le=900)
    maximum_cli_output_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    maximum_parallel_metadata_reads: int = Field(default=1, ge=1, le=4)
    staging_root: Path = Path(".runtime/live-metadata")

    @field_validator("api_version", mode="before")
    @classmethod
    def normalize_api_version(cls, value: Any) -> str:
        """Store the version number only; the source route owns its literal v prefix."""
        if not isinstance(value, str) or not _API_VERSION.fullmatch(value):
            raise ValueError("host API version is invalid")
        version = value.removeprefix("v")
        return version if "." in version else version + ".0"

    @model_validator(mode="after")
    def validate_host_config(self) -> HostLiveReadConfig:
        if not _ALIAS.fullmatch(self.alias) or self.environment_class not in _ALLOWED_CLASSES:
            raise ValueError("host live-read configuration is invalid")
        if not _API_VERSION.fullmatch(self.api_version):
            raise ValueError("host API version is invalid")
        if (
            self.staging_root.is_absolute()
            or ".." in self.staging_root.parts
            or not self.staging_root.parts
            or self.staging_root.parts[0] != ".runtime"
        ):
            raise ValueError("metadata staging must use the ignored .runtime directory")
        return self


class ResolvedVariableRow(_Model):
    record_id: str = Field(pattern=r"^[A-Za-z0-9]{1,18}$")
    values: dict[str, str]

    @model_validator(mode="after")
    def validate_values(self) -> ResolvedVariableRow:
        if len(self.values) > 32:
            raise ValueError("too many target variables")
        for name, value in self.values.items():
            if not _VARIABLE.fullmatch(name) or not _SAFE_VALUE.fullmatch(value):
                raise ValueError("target variable is invalid")
        return self


class ResolvedTargetVariables(_Model):
    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rows: tuple[ResolvedVariableRow, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_rows(self) -> ResolvedTargetVariables:
        identities = tuple(row.record_id for row in self.rows)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("resolved variable rows must be sorted and unique")
        return self


class ResolvedDatasetScope(_Model):
    """Independently issued exact synthetic membership and response projection."""

    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    object_api_name: str
    record_id_field: str
    record_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    ownership_marker_field: str
    field_projection: tuple[str, ...] = Field(min_length=1, max_length=256)
    member_predicate_values: dict[str, dict[str, str | int | float | bool]]
    expected_records: int = Field(ge=1, le=1000)
    response_record_path: str = ""
    field_paths: dict[str, str]

    @model_validator(mode="after")
    def validate_exact_scope(self) -> ResolvedDatasetScope:
        if (
            self.record_ids != tuple(sorted(set(self.record_ids)))
            or self.field_projection != tuple(sorted(set(self.field_projection)))
            or self.expected_records != len(self.record_ids)
            or not set(self.field_paths).issubset(self.field_projection)
            or self.record_id_field not in self.field_paths
            or len(set(self.field_paths.values())) != len(self.field_paths)
            or self.record_id_field not in self.field_projection
            or self.ownership_marker_field not in self.field_projection
            or set(self.member_predicate_values) != set(self.record_ids)
            or any(
                not set(values).issubset(self.field_projection)
                or self.ownership_marker_field not in values
                or values[self.ownership_marker_field] in (None, "")
                for values in self.member_predicate_values.values()
            )
            or any(not _SAFE_VALUE.fullmatch(value) for value in self.record_ids)
            or any(not _response_path_valid(path) for path in self.field_paths.values())
            or (self.response_record_path and not _response_path_valid(self.response_record_path))
        ):
            raise ValueError("dataset response scope must be exact and closed")
        return self


class HostLiveReadCapture(_Model):
    plan: LiveTargetPlan
    enrollment_receipt: SignedLiveReceipt
    cli_authentication_receipt: SignedLiveReceipt
    target_plan_receipt: SignedLiveReceipt
    dataset_scope_receipt: SignedLiveReceipt | None = None
    resolved_variables: tuple[ResolvedTargetVariables, ...] = ()
    expected_execution_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_execution_contract_document: bytes = Field(min_length=1, max_length=2 * 1024 * 1024)
    expected_execution_contract_receipt: SignedLiveReceipt
    resolved_dataset_scopes: tuple[ResolvedDatasetScope, ...] = ()

    @model_validator(mode="after")
    def validate_capture(self) -> HostLiveReadCapture:
        keys = tuple(item.target_sha256 for item in self.resolved_variables)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("resolved target variables must be sorted and unique")
        dataset_keys = tuple(item.target_sha256 for item in self.resolved_dataset_scopes)
        if dataset_keys != tuple(sorted(set(dataset_keys))):
            raise ValueError("resolved dataset scopes must be sorted and unique")
        if (
            hashlib.sha256(self.expected_execution_contract_document).hexdigest()
            != self.expected_execution_contract_sha256
            or self.expected_execution_contract_receipt.receipt_role
            != "EXPECTED_EXECUTION_CONTRACT_RECEIPT"
            or not isinstance(
                self.expected_execution_contract_receipt.payload, SupportingReceiptPayload
            )
            or self.expected_execution_contract_receipt.payload.artifact_sha256
            != self.expected_execution_contract_sha256
        ):
            raise ValueError("expected execution contract is not independently receipt-bound")
        return self


class HostLiveReadInputPort(Protocol):
    def capture(self) -> HostLiveReadCapture: ...


@dataclass(frozen=True, slots=True)
class CliInvocation:
    arguments: tuple[str, ...]
    timeout_seconds: float
    maximum_stdout_bytes: int


@dataclass(frozen=True, slots=True)
class CliCompleted:
    returncode: int
    stdout: bytes
    timed_out: bool = False
    output_exceeded: bool = False
    quiescent: bool = True


class CliRunner(Protocol):
    def run(self, invocation: CliInvocation) -> CliCompleted: ...


@dataclass(frozen=True, slots=True, repr=False)
class PinnedCliLaunch:
    """Host-only resolved executable pins; never populated from an API/MCP request."""

    prefix: tuple[str, ...]
    files: tuple[tuple[Path, str], ...]

    def require(self, launch: Sequence[str]) -> None:
        if (
            tuple(launch[: len(self.prefix)]) != self.prefix
            or not self.prefix
            or not self.files
            or len(self.files) > 8
            or len({path for path, _ in self.files}) != len(self.files)
        ):
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        for path, expected in self.files:
            if (
                not path.is_absolute()
                or not re.fullmatch(r"[a-f0-9]{64}", expected)
                or hashlib.sha256(_read_nofollow(path, maximum_bytes=134_217_728)).hexdigest()
                != expected
            ):
                raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)


@dataclass(frozen=True, slots=True)
class SubprocessCliRunner:
    executable: str = "sf"
    launch_pin: PinnedCliLaunch | None = field(default=None, repr=False)

    def run(self, invocation: CliInvocation) -> CliCompleted:
        with ExitStack() as launch_guards:
            return self._run_with_launch_guards(invocation, launch_guards)

    def _run_with_launch_guards(
        self, invocation: CliInvocation, launch_guards: ExitStack
    ) -> CliCompleted:
        deadline = time.monotonic() + invocation.timeout_seconds
        if self.executable != "sf" or not _safe_process_arguments(invocation.arguments):
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        containment = _ProcessContainment()
        process: subprocess.Popen[bytes] | None = None
        try:
            launch = _resolve_launch(self.executable, invocation.arguments)
            if self.launch_pin is not None:
                if os.name != "nt":
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
                for path, _ in self.launch_pin.files:
                    launch_guards.callback(_DirectoryGuards(path.parent).close)
                    handle, kernel = _windows_open_no_follow(path, directory=False)
                    launch_guards.callback(kernel.CloseHandle, handle)
                self.launch_pin.require(launch)
                if time.monotonic() >= deadline:
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
            containment.prepare()
            process = subprocess.Popen(
                launch,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=build_subprocess_environment(
                    controls={"SF_AUTOUPDATE_DISABLE": "true", "SF_DISABLE_TELEMETRY": "true"}
                ),
                creationflags=(0x00000004 | 0x08000000) if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=5)
                containment.close()
                return CliCompleted(-1, b"", timed_out=True)
            containment.attach_and_resume(process)
        except (OSError, LiveReadError):
            if process is not None:
                process.kill()
                process.wait(timeout=5)
            containment.close()
            return CliCompleted(-1, b"", quiescent=process is None or process.poll() is not None)
        try:
            return self._collect(process, invocation, containment, deadline)
        finally:
            try:
                containment.terminate_and_wait(process, seconds=5)
            finally:
                containment.close()

    @staticmethod
    def _collect(
        process: subprocess.Popen[bytes],
        invocation: CliInvocation,
        containment: _ProcessContainment,
        deadline: float,
    ) -> CliCompleted:
        streams = (process.stdout, process.stderr)
        limits = (invocation.maximum_stdout_bytes, _MAX_STDERR_BYTES)
        buffers = (bytearray(), bytearray())
        exceeded = threading.Event()

        def drain(index: int) -> None:
            stream = streams[index]
            if stream is None:
                return
            while chunk := stream.read(65_536):
                if len(buffers[index]) + len(chunk) > limits[index]:
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
        # A shell exiting never proves that its Node/Java children have stopped. The
        # job/group is terminated on every exit, then verified empty before staging cleanup.
        quiescent = containment.terminate_and_wait(process, seconds=5)
        for thread in threads:
            thread.join(timeout=1)
        quiescent = quiescent and not any(thread.is_alive() for thread in threads)
        returncode = process.returncode if process.returncode is not None else -1
        output_exceeded = exceeded.is_set()
        return CliCompleted(
            returncode,
            b"" if output_exceeded or timed_out or not quiescent else bytes(buffers[0]),
            timed_out=timed_out,
            output_exceeded=output_exceeded,
            quiescent=quiescent,
        )


class _ProcessContainment:
    """Create suspended, assign a kill-on-close job, then release execution on Windows."""

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
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
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

            if not self.kernel.TerminateJobObject(self.job, 1):
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


def _safe_process_arguments(arguments: tuple[str, ...]) -> bool:
    if not arguments or len(arguments) > 64:
        return False
    return all(
        isinstance(item, str)
        and 0 < len(item) <= 4096
        and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in item)
        for item in arguments
    )


def _resolve_windows_sf_launcher(resolved_batch: Path, arguments: tuple[str, ...]) -> list[str]:
    """Bypass cmd.exe so internally compiled values remain literal argv elements."""

    installation_root = resolved_batch.parent.parent
    bundled = (
        installation_root / "client/bin/node.exe",
        installation_root / "client/bin/run.js",
    )
    candidates: list[tuple[Path, Path]] = []
    if any(item.is_file() for item in bundled):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        if local_app_data.is_absolute():
            candidates.append(
                (
                    local_app_data / "sf/client/bin/node.exe",
                    local_app_data / "sf/client/bin/run.js",
                )
            )
        candidates.extend(
            (
                (bundled[0], bundled[1]),
                (bundled[0], installation_root / "client/bin/run"),
            )
        )
    npm_entrypoints = (
        resolved_batch.parent / "node_modules/@salesforce/cli/bin/run.js",
        resolved_batch.parent / "node_modules/@salesforce/cli/bin/run",
    )
    npm_node = resolved_batch.parent / "node.exe"
    if not npm_node.is_file():
        resolved_node = shutil.which("node")
        npm_node = Path(resolved_node) if resolved_node else npm_node
    candidates.extend((npm_node, entrypoint) for entrypoint in npm_entrypoints)
    selected = next(
        (
            (node, entrypoint)
            for node, entrypoint in candidates
            if node.is_file() and entrypoint.is_file()
        ),
        None,
    )
    if selected is None:
        raise OSError("Direct Salesforce CLI launcher is unavailable")
    node, entrypoint = selected
    try:
        node = node.absolute()
        entrypoint = entrypoint.absolute()
        if (
            node.suffix.casefold() != ".exe"
            or node.resolve(strict=True) != node
            or entrypoint.resolve(strict=True) != entrypoint
            or not node.is_file()
            or not entrypoint.is_file()
        ):
            raise OSError
        _checked_identity(node, directory=False)
        _checked_identity(entrypoint, directory=False)
    except (OSError, LiveReadError):
        raise OSError("Direct Salesforce CLI launcher is unavailable") from None
    if not _safe_process_arguments((str(node), str(entrypoint), *arguments)):
        raise OSError("Salesforce CLI arguments are unsafe")
    return [str(node), "--no-deprecation", str(entrypoint), *arguments]


def _resolve_launch(executable: str, arguments: tuple[str, ...]) -> list[str]:
    resolved = shutil.which(executable)
    if resolved is None:
        raise OSError("Salesforce CLI is unavailable")
    resolved_path = Path(resolved).resolve()
    if os.name != "nt" or resolved_path.suffix.casefold() not in {".cmd", ".bat"}:
        return [str(resolved_path), *arguments]
    return _resolve_windows_sf_launcher(resolved_path, arguments)


class LiveReadObservation(_Model):
    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    partition: TargetPartition
    status: str
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    assertions_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    item_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    invocation_count: int = Field(ge=1, le=1000)
    member_root_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")


class LiveReadResult(_Model):
    authority_scope: Literal["READ_ONLY_LIVE_EVIDENCE"] = "READ_ONLY_LIVE_EVIDENCE"
    release_eligible: Literal[False] = False
    evidence_phase: Literal[EvidencePhase.LIVE_BASELINE] = EvidencePhase.LIVE_BASELINE
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observations: tuple[LiveReadObservation, ...]
    stored_receipt_ids: tuple[str, ...]
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_result(self) -> LiveReadResult:
        body = self.model_dump(mode="json")
        body.pop("result_sha256")
        if self.result_sha256 != stable_sha256(body):
            raise ValueError("live-read result digest mismatch")
        return self


@dataclass(frozen=True, slots=True)
class HostOwnedLiveReadExecutor:
    config: HostLiveReadConfig
    repository_root: Path
    input_port: HostLiveReadInputPort
    issuer_registry: TrustedIssuerRegistry
    receipt_producer: TrustedLiveReceiptProducer
    execution_assertion_store: ExecutionAssertionStore
    runner_authentication_key: bytes = field(repr=False)
    runner: CliRunner = field(default_factory=SubprocessCliRunner)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    authority_revalidator: Callable[[], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.runner_authentication_key, bytes)
            or len(self.runner_authentication_key) < 32
        ):
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)

    def execute(self) -> LiveReadResult:
        try:
            capture = HostLiveReadCapture.model_validate(
                self.input_port.capture().model_dump(mode="python")
            )
        except Exception:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID) from None
        now = self._now()
        try:
            self._validate_authority(capture, now)
            authorities = {
                partition: self._bind_authority(capture, partition)
                for partition in _GATE_BY_PARTITION
            }
        except LiveReadError as error:
            self._log_failure(capture, "AUTHORITY_BINDING", error)
            raise
        values = {item.target_sha256: item for item in capture.resolved_variables}
        datasets = {item.target_sha256: item for item in capture.resolved_dataset_scopes}
        targets = tuple(
            item for item in capture.plan.targets if item.partition in _GATE_BY_PARTITION
        )
        if not targets or len(targets) > _MAX_OPERATIONS:
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        if (
            sum(
                len(values[item.target_sha256].rows) if item.target_sha256 in values else 1
                for item in targets
            )
            > _MAX_OPERATIONS
        ):
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        try:
            self._preflight_targets(targets, values)
            observed_cli_version = self._verify_cli_version(capture)
        except LiveReadError as error:
            self._log_failure(capture, "TARGET_PREFLIGHT", error)
            raise

        grouped: dict[TargetPartition, list[LiveReadObservation]] = {
            partition: [] for partition in _GATE_BY_PARTITION
        }
        for partition in _GATE_BY_PARTITION:
            partition_targets = tuple(item for item in targets if item.partition is partition)
            if not partition_targets:
                raise LiveReadError(LiveReadCode.TARGET_INVALID)
            try:
                self._validate_authority(capture, self._now())
                self._bind_authority(capture, partition)
                self._verify_identity(capture)

                def execute_target(target: PlannedTarget) -> LiveReadObservation:
                    try:
                        self._validate_authority(capture, self._now())
                        self._verify_pinned_session(capture)
                        result = self._execute_target(
                            target,
                            values.get(target.target_sha256),
                            datasets.get(target.target_sha256),
                            capture,
                        )
                        self._verify_pinned_session(capture)
                        return result
                    except LiveReadError:
                        raise
                    except Exception as error:
                        _LOGGER.warning(
                            _canonical_bytes(
                                {
                                    "event": "live_read_target_failed",
                                    "partition": target.partition.value,
                                    "metadata_type": target.specification.get("metadataType"),
                                    "error_type": type(error).__name__,
                                }
                            ).decode()
                        )
                        raise

                if (
                    partition is TargetPartition.METADATA
                    and self.config.maximum_parallel_metadata_reads > 1
                    and len(partition_targets) > 1
                ):
                    workers = min(
                        self.config.maximum_parallel_metadata_reads,
                        len(partition_targets),
                    )
                    with ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix="neo-sf-metadata-read",
                    ) as pool:
                        for offset in range(0, len(partition_targets), workers):
                            batch = partition_targets[offset : offset + workers]
                            futures = tuple(pool.submit(execute_target, target) for target in batch)
                            try:
                                grouped[partition].extend(future.result() for future in futures)
                            except Exception:
                                for future in futures:
                                    future.cancel()
                                raise
                else:
                    grouped[partition].extend(map(execute_target, partition_targets))
                self._verify_identity(capture)
            except LiveReadError as error:
                self._log_failure(capture, f"{partition.value}_EXECUTION", error)
                raise
            except Exception as error:
                self._log_failure(
                    capture,
                    f"{partition.value}_EXECUTION",
                    LiveReadError(LiveReadCode.EXECUTION_FAILED),
                )
                _LOGGER.warning(
                    _canonical_bytes(
                        {
                            "event": "live_read_partition_exception",
                            "partition": partition.value,
                            "error_type": type(error).__name__,
                        }
                    ).decode()
                )
                raise LiveReadError(LiveReadCode.EXECUTION_FAILED) from None

        receipt_ids: list[str] = []
        for partition in _GATE_BY_PARTITION:
            try:
                self._validate_authority(capture, self._now())
                stored = self._append_receipt(
                    capture,
                    partition,
                    grouped[partition],
                    now,
                    authorities[partition],
                    observed_cli_version,
                )
            except LiveReadError as error:
                self._log_failure(capture, f"{partition.value}_RECEIPT", error)
                raise
            except Exception:
                raise LiveReadError(LiveReadCode.RECEIPT_FAILED) from None
            receipt_ids.append(stored.receipt_id)

        observations = tuple(
            item for partition in _GATE_BY_PARTITION for item in grouped[partition]
        )
        body = {
            "authority_scope": "READ_ONLY_LIVE_EVIDENCE",
            "release_eligible": False,
            "evidence_phase": EvidencePhase.LIVE_BASELINE,
            "plan_sha256": capture.plan.plan_sha256,
            "observations": [item.model_dump(mode="json") for item in observations],
            "stored_receipt_ids": receipt_ids,
        }
        return LiveReadResult(**body, result_sha256=stable_sha256(body))

    @staticmethod
    def _log_failure(capture: HostLiveReadCapture, stage: str, error: LiveReadError) -> None:
        _LOGGER.warning(
            _canonical_bytes(
                {
                    "event": "live_read_stage_failed",
                    "stage": stage,
                    "error_code": error.code.value,
                    "campaign_id": capture.enrollment_receipt.payload.scope.campaign_id,
                }
            ).decode()
        )

    def _validate_authority(self, capture: HostLiveReadCapture, now: datetime) -> None:
        def deny(reason: str) -> None:
            _LOGGER.warning(
                _canonical_bytes(
                    {
                        "event": "live_read_authority_rejected",
                        "reason_code": reason,
                        "campaign_id": capture.enrollment_receipt.payload.scope.campaign_id,
                    }
                ).decode()
            )
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)

        if self.authority_revalidator is not None:
            self.authority_revalidator()
        plan = capture.plan
        if (
            plan.authorizes_execution
            or plan.organization_classification_receipt_sha256
            != hashlib.sha256(serialize_live_receipt(capture.enrollment_receipt)).hexdigest()
            or plan.acceptance_profile_sha256
            != capture.enrollment_receipt.payload.scope.profile_sha256
            or _parse_time(plan.valid_until) <= now
            or plan.project_id != capture.enrollment_receipt.payload.scope.project_id
            or _parse_time(plan.evaluated_at) > now
            or capture.enrollment_receipt.payload.scope.recovery_deadline <= now
        ):
            deny("PLAN_SCOPE_INVALID")
        enrollment = capture.enrollment_receipt
        cli = capture.cli_authentication_receipt
        if not isinstance(enrollment.payload, GateReceiptPayload) or not isinstance(
            cli.payload, GateReceiptPayload
        ):
            deny("CLASSIFICATION_RECEIPT_SHAPE_INVALID")
        if (
            enrollment.payload.gate_id != "SF-L01"
            or cli.payload.gate_id != "SF-L02"
            or enrollment.payload.enrollment is None
            or cli.payload.scope != enrollment.payload.scope
            or self.config.org_fingerprint_sha256 != enrollment.payload.scope.org_fingerprint_sha256
            or self.config.actor_fingerprint_sha256
            != enrollment.payload.scope.actor_fingerprint_sha256
        ):
            deny("CLASSIFICATION_SCOPE_INVALID")
        if (
            capture.target_plan_receipt.receipt_role != "LIVE_TARGET_PLAN_RECEIPT"
            or not isinstance(capture.target_plan_receipt.payload, SupportingReceiptPayload)
            or capture.target_plan_receipt.payload.artifact_sha256 != plan.plan_sha256
        ):
            deny("TARGET_PLAN_RECEIPT_INVALID")
        if (
            capture.expected_execution_contract_receipt.receipt_role
            != "EXPECTED_EXECUTION_CONTRACT_RECEIPT"
            or not isinstance(
                capture.expected_execution_contract_receipt.payload,
                SupportingReceiptPayload,
            )
            or capture.expected_execution_contract_receipt.payload.artifact_sha256
            != hashlib.sha256(capture.expected_execution_contract_document).hexdigest()
            or capture.expected_execution_contract_sha256
            != capture.expected_execution_contract_receipt.payload.artifact_sha256
        ):
            deny("EXPECTED_CONTRACT_RECEIPT_INVALID")
        try:
            expected_contract = ExpectedExecutionContract.model_validate_json(
                capture.expected_execution_contract_document
            )
        except Exception:
            deny("EXPECTED_CONTRACT_DOCUMENT_INVALID")
        contract_targets = {
            item.target_sha256
            for item in expected_contract.assertions
            if item.gate_id in _GATE_BY_PARTITION.values()
        }
        live_read_targets = {
            item.target_sha256 for item in plan.targets if item.partition in _GATE_BY_PARTITION
        }
        provenance = expected_contract.runner_provenance
        if (
            expected_contract.local_validation_phase_policy_sha256
            != plan.local_validation_phase_policy_sha256
            or expected_contract.deferred_local_validations != plan.deferred_local_validations
            or expected_contract.phase_read_only_gate_ids != plan.phase_read_only_gate_ids
            or (
                plan.local_validation_phase_policy_sha256 is not None
                and not set(_GATE_BY_PARTITION.values()).issubset(plan.phase_read_only_gate_ids)
            )
        ):
            deny("LOCAL_PHASE_BINDING_INVALID")
        if (
            expected_contract.plan_sha256 != plan.plan_sha256
            or expected_contract.request_sha256 != plan.verified_change_sha256
            or expected_contract.candidate_bundle_sha256
            != enrollment.payload.scope.candidate_sha256
            or expected_contract.scope != enrollment.payload.scope
            or _parse_time(expected_contract.valid_until) > _parse_time(plan.valid_until)
            or _parse_time(expected_contract.valid_until) <= now
            or expected_contract.execution_id != f"candidate-live:{plan.plan_sha256[:32]}"
            or contract_targets != live_read_targets
            or provenance.producer_id != self.config.assertion_producer_id
            or provenance.runner_id != self.config.assertion_runner_id
            or provenance.runner_version != self.config.runner_version
            or provenance.adapter_version != self.config.adapter_version
            or provenance.tool_versions
            != (
                ExecutionToolVersion(
                    tool_id="salesforce-cli",
                    version=self.config.salesforce_cli_version,
                ),
            )
        ):
            deny("EXPECTED_CONTRACT_SCOPE_INVALID")
        synthetic_values_sha256 = _dataset_scope_digest(capture)
        dataset_target_ids = {
            target.target_sha256
            for target in plan.targets
            if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
            and any(
                isinstance(variable, dict)
                and variable.get("valueSource") == "SYNTHETIC_DATASET_RECEIPT"
                for variable in target.specification.get("variables", ())
            )
        }
        if set(item.target_sha256 for item in capture.resolved_variables) != dataset_target_ids:
            deny("RESOLVED_VARIABLE_SCOPE_INVALID")
        rest_ids = {
            target.target_sha256
            for target in plan.targets
            if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
        }
        if {item.target_sha256 for item in capture.resolved_dataset_scopes} != rest_ids:
            deny("RESOLVED_DATASET_SCOPE_INVALID")
        for dataset in capture.resolved_dataset_scopes:
            _validate_dataset_contract(
                dataset, plan, actor_user_id_sha256=self.config.actor_user_id_sha256
            )
            target = next(
                item for item in plan.targets if item.target_sha256 == dataset.target_sha256
            )
            variables = next(
                (
                    item
                    for item in capture.resolved_variables
                    if item.target_sha256 == target.target_sha256
                ),
                None,
            )
            _rest_invocation_bindings(self.config, target, variables, dataset)
        if rest_ids and (
            capture.dataset_scope_receipt is None
            or capture.dataset_scope_receipt.receipt_role != "LIVE_DATASET_SCOPE_RECEIPT"
            or not isinstance(capture.dataset_scope_receipt.payload, SupportingReceiptPayload)
            or capture.dataset_scope_receipt.payload.artifact_sha256 != synthetic_values_sha256
        ):
            deny("DATASET_SCOPE_RECEIPT_INVALID")
        for receipt in self._all_authority_receipts(capture):
            payload = receipt.payload
            if (
                self.issuer_registry.verify(receipt) is not None
                or payload.scope != enrollment.payload.scope
                or payload.outcome not in {ReceiptOutcome.PASSED, ReceiptOutcome.RECORDED}
                or payload.issued_at > now
                or payload.terminal_at > now
                or payload.expires_at <= now
            ):
                deny("AUTHORITY_RECEIPT_INVALID")
        binding = enrollment.payload.enrollment
        if (
            binding.alias_reference_sha256 != _digest(self.config.alias)
            or binding.organization_id_sha256 != self.config.org_fingerprint_sha256
            or binding.instance_host_sha256 != self.config.instance_host_sha256
            or binding.edition_sha256 != self.config.edition_sha256
            or binding.environment_class != self.config.environment_class
            or binding.persona_fingerprint_sha256 != self.config.actor_fingerprint_sha256
        ):
            deny("ENROLLMENT_BINDING_INVALID")

    @staticmethod
    def _all_authority_receipts(capture: HostLiveReadCapture) -> tuple[SignedLiveReceipt, ...]:
        values = [
            capture.enrollment_receipt,
            capture.cli_authentication_receipt,
            capture.target_plan_receipt,
            capture.expected_execution_contract_receipt,
        ]
        if capture.dataset_scope_receipt is not None:
            values.append(capture.dataset_scope_receipt)
        return tuple(values)

    def _verify_identity(self, capture: HostLiveReadCapture) -> None:
        try:
            capture_classified_identity(
                pins=_classification_pins(self.config),
                authority=self.config.classification_authority,
                invoke=lambda args, deadline, maximum: self._invoke_json(
                    args, capture=capture, maximum=maximum, additional_deadline=deadline
                ),
                clock=self._now,
            )
        except ClassificationError as exc:
            code = {
                "CLASSIFICATION_AUTHORITY_INVALID": LiveReadCode.AUTHORITY_INVALID,
                "CLASSIFICATION_AUTHORITY_EXPIRED": LiveReadCode.AUTHORITY_INVALID,
                "CLASSIFICATION_TIMEOUT": LiveReadCode.CLI_TIMEOUT,
                "CLASSIFICATION_OUTPUT_LIMIT": LiveReadCode.OUTPUT_LIMIT,
                "CLASSIFICATION_PROCESS_NOT_QUIESCENT": LiveReadCode.PROCESS_NOT_QUIESCENT,
                "CLASSIFICATION_COMMAND_FAILED": LiveReadCode.CLI_FAILED,
                "CLASSIFICATION_RESPONSE_INVALID": LiveReadCode.RESPONSE_INVALID,
            }.get(exc.code, LiveReadCode.IDENTITY_MISMATCH)
            raise LiveReadError(code) from None

    def _verify_pinned_session(self, capture: HostLiveReadCapture) -> None:
        try:
            payload = self._invoke_json(
                (
                    "org",
                    "display",
                    "--target-org",
                    self.config.alias,
                    "--json",
                ),
                capture=capture,
                maximum=self.config.classification_authority.maximum_response_bytes,
            )
            verify_pinned_display_identity(payload, _classification_pins(self.config))
            subject = self._invoke_json(
                (
                    "api",
                    "request",
                    "rest",
                    "/services/oauth2/userinfo",
                    "--method",
                    "GET",
                    "--target-org",
                    self.config.alias,
                    "--json",
                ),
                capture=capture,
                maximum=self.config.classification_authority.maximum_response_bytes,
            )
            verify_pinned_subject_identity(subject, _classification_pins(self.config))
        except ClassificationError:
            raise LiveReadError(LiveReadCode.IDENTITY_MISMATCH) from None

    def _verify_cli_version(self, capture: HostLiveReadCapture) -> str:
        self._validate_authority(capture, self._now())
        completed = self.runner.run(
            CliInvocation(
                arguments=("--version",),
                timeout_seconds=min(30, self._remaining_timeout(capture)),
                maximum_stdout_bytes=1024,
            )
        )
        self._validate_authority(capture, self._now())
        if not completed.quiescent:
            raise LiveReadError(LiveReadCode.PROCESS_NOT_QUIESCENT)
        if completed.timed_out:
            raise LiveReadError(LiveReadCode.CLI_TIMEOUT)
        if completed.output_exceeded or len(completed.stdout) > 1024:
            raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
        match = _CLI_VERSION_OUTPUT.match(completed.stdout)
        if completed.returncode != 0:
            raise LiveReadError(LiveReadCode.CLI_FAILED)
        if match is None:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        version = match.group(1).decode("ascii")
        if version != self.config.salesforce_cli_version:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        return version

    def _preflight_targets(
        self,
        targets: Sequence[PlannedTarget],
        values: Mapping[str, ResolvedTargetVariables],
    ) -> None:
        partitions = {target.partition for target in targets}
        if partitions != set(_GATE_BY_PARTITION):
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        for target in targets:
            target_values = values.get(target.target_sha256)
            if target.partition in {
                TargetPartition.STANDARD_REST,
                TargetPartition.CUSTOM_REST,
            }:
                if target_values is None:
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
                for row in target_values.rows:
                    _render_rest_target(self.config, target, row.values)
            elif target.partition is TargetPartition.METADATA:
                if target_values:
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
                _metadata_target(target)
            else:
                raise LiveReadError(LiveReadCode.TARGET_INVALID)

    def _execute_target(
        self,
        target: PlannedTarget,
        values: ResolvedTargetVariables | None,
        dataset: ResolvedDatasetScope | None,
        capture: HostLiveReadCapture,
    ) -> LiveReadObservation:
        started = time.monotonic()
        if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}:
            if dataset is None:
                raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
            invocations = _rest_invocation_bindings(self.config, target, values, dataset)
            member_results: list[dict[str, Any]] = []
            for row, member_scope in invocations:
                self._validate_authority(capture, self._now())
                self._verify_pinned_session(capture)
                before_membership = self._refresh_membership(target, row.record_id, capture)
                item_artifact, item_assertions, item_count, item_size = self._execute_rest(
                    target, row.values, member_scope, capture
                )
                after_membership = self._refresh_membership(target, row.record_id, capture)
                self._verify_pinned_session(capture)
                if (
                    not target.specification["minimumCardinality"]
                    <= item_count
                    <= target.specification["maximumCardinality"]
                ):
                    raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
                route, _ = _render_scoped_rest_target(self.config, target, row.values, member_scope)
                member_results.append(
                    {
                        "record_sha256": _digest(row.record_id),
                        "request_sha256": stable_sha256({"route": route, "variables": row.values}),
                        "artifact_sha256": item_artifact,
                        "assertions_sha256": item_assertions,
                        "item_count": item_count,
                        "byte_count": item_size,
                        "membership_before_sha256": before_membership,
                        "membership_after_sha256": after_membership,
                    }
                )
            if len(member_results) != dataset.expected_records:
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            artifact = stable_sha256(member_results)
            assertions = stable_sha256(
                {"members": member_results, "scope": dataset.model_dump(mode="json")}
            )
            count = sum(item["item_count"] for item in member_results)
            size = sum(item["byte_count"] for item in member_results)
            request_sha256 = stable_sha256(
                {
                    "target": target.target_sha256,
                    "requests": [item["request_sha256"] for item in member_results],
                    "variables": values.model_dump(mode="json") if values else None,
                    "datasetScope": dataset.model_dump(mode="json"),
                    "expectedExecutionContractSha256": capture.expected_execution_contract_sha256,
                }
            )
        elif target.partition is TargetPartition.METADATA:
            artifact, assertions, count, size = self._execute_metadata(target, capture)
            request_sha256 = stable_sha256(
                {
                    "target": target.target_sha256,
                    "metadata": _metadata_target(target),
                    "expectedExecutionContractSha256": capture.expected_execution_contract_sha256,
                }
            )
        else:
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        return LiveReadObservation(
            target_sha256=target.target_sha256,
            partition=target.partition,
            status="PASSED",
            artifact_sha256=artifact,
            assertions_sha256=assertions,
            request_sha256=request_sha256,
            item_count=count,
            byte_count=size,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            invocation_count=len(invocations)
            if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
            else 1,
            member_root_sha256=stable_sha256(member_results)
            if target.partition in {TargetPartition.STANDARD_REST, TargetPartition.CUSTOM_REST}
            else None,
        )

    def _refresh_membership(
        self, target: PlannedTarget, record_id: str, capture: HostLiveReadCapture
    ) -> str | None:
        if target.partition is TargetPartition.STANDARD_REST:
            return None
        candidates = [
            item
            for item in capture.plan.targets
            if item.partition is TargetPartition.STANDARD_REST
            and item.specification.get("datasetId") == target.specification.get("datasetId")
        ]
        if len(candidates) != 1:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        selected = candidates[0]
        dataset = next(
            item
            for item in capture.resolved_dataset_scopes
            if item.target_sha256 == selected.target_sha256
        )
        resolved = next(
            item
            for item in capture.resolved_variables
            if item.target_sha256 == selected.target_sha256
        )
        if set(dataset.field_paths) != set(dataset.field_projection):
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        row, member_scope = next(
            (row, member)
            for row, member in _rest_invocation_bindings(self.config, selected, resolved, dataset)
            if row.record_id == record_id
        )
        self._validate_authority(capture, self._now())
        self._verify_identity(capture)
        result = self._execute_rest(selected, row.values, member_scope, capture)
        self._verify_identity(capture)
        return stable_sha256(
            {"target": selected.target_sha256, "member": _digest(record_id), "result": result}
        )

    def _execute_rest(
        self,
        target: PlannedTarget,
        values: Mapping[str, str],
        dataset: ResolvedDatasetScope | None,
        capture: HostLiveReadCapture,
    ) -> tuple[str, str, int, int]:
        spec = target.specification
        if dataset is None:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        route, maximum = _render_scoped_rest_target(self.config, target, values, dataset)
        payload = self._invoke_json(
            (
                "api",
                "request",
                "rest",
                route,
                "--method",
                "GET",
                "--target-org",
                self.config.alias,
                "--json",
            ),
            maximum=min(maximum + 65_536, self.config.maximum_cli_output_bytes),
            capture=capture,
        )
        result = _unwrap_rest_response(payload, maximum=maximum)
        encoded = _canonical_bytes(result)
        if len(encoded) > maximum:
            raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
        schema_assertions, _ = _validate_response_fields(result, spec.get("responseFields"))
        dataset_assertions, count = _validate_dataset_response(
            result, dataset, parent_bindings=spec["parentBindings"]
        )
        if target.partition is TargetPartition.STANDARD_REST and "attributes" in result:
            expected_path = route.split("?", 1)[0]
            if result["attributes"] != {"type": dataset.object_api_name, "url": expected_path}:
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        assertions = stable_sha256({"schema": schema_assertions, "dataset": dataset_assertions})
        return hashlib.sha256(encoded).hexdigest(), assertions, count, len(encoded)

    def _execute_metadata(
        self, target: PlannedTarget, capture: HostLiveReadCapture
    ) -> tuple[str, str, int, int]:
        spec = target.specification
        metadata_type, member = _metadata_target(target)
        root = self.repository_root.absolute() / self.config.staging_root
        stage: Path | None = None
        stage_identity: tuple[int, int, int, int] | None = None
        root_guards: _DirectoryGuards | None = None
        stage_guards: _DirectoryGuards | None = None
        quiescent = True
        try:
            _prepare_staging_root(self.repository_root, root)
            root_guards = _DirectoryGuards(root)
            stage = Path(tempfile.mkdtemp(prefix="capture-", dir=root))
            stage_identity = _checked_identity(stage, directory=True)
            stage_guards = _DirectoryGuards(stage)
            response = self._invoke_json(
                (
                    "project",
                    "retrieve",
                    "start",
                    "--metadata",
                    f"{metadata_type}:{member}",
                    "--target-metadata-dir",
                    str(stage),
                    "--unzip",
                    "--wait",
                    str(max(1, math.ceil(self._remaining_timeout(capture) / 60))),
                    "--target-org",
                    self.config.alias,
                    "--json",
                ),
                capture=capture,
            )
            _require_identity(stage, stage_identity, directory=True)
            payload_root = _metadata_payload_root(stage)
            expected_paths = _metadata_expected_paths(response, metadata_type, member)
            entries, total = _scan_stage(
                payload_root,
                maximum_files=int(spec.get("maximumFiles", 0)),
                maximum_bytes=int(spec.get("maximumBytes", 0)),
                allowed_paths=expected_paths
                | {
                    path + "-meta.xml"
                    for path in expected_paths
                    if path.endswith((".cls", ".trigger"))
                },
            )
            _validate_metadata_completion(
                response,
                metadata_type,
                member,
                entries,
                payload_root=payload_root,
            )
            artifact = stable_sha256(entries)
            assertions = stable_sha256(
                {"target": target.target_sha256, "files": len(entries), "bytes": total}
            )
            result = (artifact, assertions, len(entries), total)
        except LiveReadError as error:
            if error.code is LiveReadCode.PROCESS_NOT_QUIESCENT:
                quiescent = False
            raise
        except Exception:
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID) from None
        finally:
            try:
                if stage is not None:
                    if not quiescent:
                        # Keep staging quarantined if a child can still write to it.
                        raise LiveReadError(LiveReadCode.PROCESS_NOT_QUIESCENT)
                    if stage_identity is None:
                        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
                    _require_identity(stage, stage_identity, directory=True)
                    _cleanup_stage(stage)
                    if stage_guards is not None:
                        stage_guards.close()
                    _require_identity(stage, stage_identity, directory=True)
                    stage.rmdir()
                    if os.path.lexists(stage):
                        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
            except OSError:
                raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID) from None
            finally:
                if stage_guards is not None:
                    stage_guards.close()
                if root_guards is not None:
                    root_guards.close()
        return result

    def _append_receipt(
        self,
        capture: HostLiveReadCapture,
        partition: TargetPartition,
        observations: Sequence[LiveReadObservation],
        now: datetime,
        authority: TrustedGateAppendAuthority,
        observed_cli_version: str,
    ) -> StoredLiveReceipt:
        gate_id = _GATE_BY_PARTITION[partition]
        try:
            terminal_at = self._now()
            expires_at = min(
                (receipt.payload.expires_at for receipt in self._all_authority_receipts(capture)),
                default=now + timedelta(minutes=5),
            )
            contract = ExpectedExecutionContract.model_validate_json(
                capture.expected_execution_contract_document
            )
            expectations = tuple(item for item in contract.assertions if item.gate_id == gate_id)
            observed_by_target = {item.target_sha256: item for item in observations}
            expected_by_target = {item.target_sha256: item for item in expectations}
            provenance = contract.runner_provenance
            expected_tool_versions = provenance.tool_versions
            if (
                not expectations
                or len(observed_by_target) != len(observations)
                or set(observed_by_target) != set(expected_by_target)
                or provenance.producer_id != self.config.assertion_producer_id
                or provenance.runner_id != self.config.assertion_runner_id
                or provenance.runner_version != self.config.runner_version
                or provenance.adapter_version != self.config.adapter_version
                or expected_tool_versions
                != (ExecutionToolVersion(tool_id="salesforce-cli", version=observed_cli_version),)
            ):
                raise LiveReadError(LiveReadCode.RECEIPT_FAILED)
            expires_at = min(expires_at, _parse_time(contract.valid_until))
            ordered_observations = tuple(
                observed_by_target[item.target_sha256] for item in expectations
            )
            result_artifacts = tuple(
                build_execution_artifact_reference(
                    artifact_role=f"SANITIZED_CAPTURE_{index:03d}",
                    media_type="application/json",
                    content=json.dumps(
                        item.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8"),
                )
                for index, item in enumerate(ordered_observations, 1)
            )
            assertion_ids = tuple(item.assertion_id for item in expectations)
            artifact = ExecutionAssertionArtifact(
                expected_contract_bytes_sha256=capture.expected_execution_contract_sha256,
                producer_id=self.config.assertion_producer_id,
                runner_id=self.config.assertion_runner_id,
                runner_key_id=self.config.assertion_runner_key_id,
                execution_id=contract.execution_id,
                runner_version=self.config.runner_version,
                adapter_version=self.config.adapter_version,
                tool_versions=expected_tool_versions,
                gate_id=gate_id,
                evidence_phase=EvidencePhase.LIVE_BASELINE,
                scope=capture.enrollment_receipt.payload.scope,
                expected_assertion_ids=assertion_ids,
                assertions=tuple(
                    ExecutionAssertion(
                        assertion_id=expectation.assertion_id,
                        subject_kind=(
                            AssertionSubjectKind.METADATA_ASSERTION
                            if observation.partition is TargetPartition.METADATA
                            else AssertionSubjectKind.HTTP_ASSERTION
                        ),
                        subject_id=f"target:{observation.target_sha256}",
                        target_sha256=expectation.target_sha256,
                        predicate=expectation.predicate.value,
                        outcome=AssertionOutcome.PASSED,
                        expected_sha256=expectation.expected_sha256,
                        observed_sha256=result.content_sha256,
                        result_artifact_sha256=result.content_sha256,
                        result_artifact_role=result.artifact_role,
                        predicate_sha256=stable_sha256(expectation.model_dump(mode="json")),
                        observed_cardinality=observation.item_count,
                        observed_invocation_count=observation.invocation_count,
                        observed_projection=expectation.exact_projection,
                        metadata_type=expectation.metadata_type,
                        metadata_member=expectation.metadata_member,
                        compatible_dataset_target_sha256s=(
                            expectation.compatible_dataset_target_sha256s
                        ),
                        duration_ms=observation.duration_ms,
                    )
                    for expectation, observation, result in zip(
                        expectations,
                        ordered_observations,
                        result_artifacts,
                        strict=True,
                    )
                ),
                result_artifacts=result_artifacts,
                runner_result_sha256=execution_artifact_index_sha256(result_artifacts),
                started_at=now,
                terminal_at=terminal_at,
                expires_at=expires_at,
                runner_signature_sha256="0" * 64,
            )
            artifact = sign_execution_assertion(artifact, runner_key=self.runner_authentication_key)
            assertion_record = self.execution_assertion_store.append(
                serialize_execution_assertion(artifact)
            )
            evidence = GateExecutionEvidence(
                assertion_artifact_id=assertion_record.assertion_artifact_id
            )
            return self.receipt_producer.append_gate_receipt(authority, evidence)
        except Exception:
            raise LiveReadError(LiveReadCode.RECEIPT_FAILED) from None

    def _bind_authority(
        self, capture: HostLiveReadCapture, partition: TargetPartition
    ) -> TrustedGateAppendAuthority:
        gate_id = _GATE_BY_PARTITION[partition]
        definition = self.receipt_producer.gate_definition(gate_id)
        available = {
            receipt.receipt_role: receipt for receipt in self._all_authority_receipts(capture)
        }
        try:
            inputs = tuple(available[role] for role in definition.requiredInputReceiptRoles)
            return self.receipt_producer.bind_gate(
                gate_id=gate_id,
                evidence_phase=EvidencePhase.LIVE_BASELINE,
                dependency_receipts=(capture.cli_authentication_receipt,),
                input_receipts=inputs,
            )
        except Exception:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID) from None

    def _invoke_json(
        self,
        arguments: tuple[str, ...],
        *,
        capture: HostLiveReadCapture,
        maximum: int | None = None,
        additional_deadline: datetime | None = None,
    ) -> dict[str, Any]:
        timeout = self._remaining_timeout(capture)
        if additional_deadline is not None:
            timeout = min(timeout, (additional_deadline - self._now()).total_seconds())
        if timeout <= 0:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        invocation = CliInvocation(
            arguments=arguments,
            timeout_seconds=timeout,
            maximum_stdout_bytes=maximum or self.config.maximum_cli_output_bytes,
        )
        try:
            completed = self.runner.run(invocation)
        except Exception:
            # A runner exception is not a completion acknowledgment. In particular,
            # metadata cleanup cannot assume that a descendant has stopped writing.
            raise LiveReadError(LiveReadCode.PROCESS_NOT_QUIESCENT) from None
        if not completed.quiescent:
            raise LiveReadError(LiveReadCode.PROCESS_NOT_QUIESCENT)
        self._validate_authority(capture, self._now())
        if completed.timed_out:
            raise LiveReadError(LiveReadCode.CLI_TIMEOUT)
        if completed.output_exceeded or len(completed.stdout) > invocation.maximum_stdout_bytes:
            raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
        if completed.returncode != 0:
            raise LiveReadError(LiveReadCode.CLI_FAILED)
        try:
            payload = _strict_json(completed.stdout)
        except (UnicodeDecodeError, ValueError):
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID) from None
        if type(payload.get("status")) is not int or payload["status"] != 0:
            raise LiveReadError(LiveReadCode.CLI_FAILED)
        return payload

    def _remaining_timeout(self, capture: HostLiveReadCapture) -> float:
        now = self._now()
        self._validate_authority(capture, now)
        deadline = min(
            _parse_time(capture.plan.valid_until),
            _parse_time(_strict_json(capture.expected_execution_contract_document)["valid_until"]),
            self.config.classification_authority.expires_at,
            capture.enrollment_receipt.payload.scope.recovery_deadline,
            *(receipt.payload.expires_at for receipt in self._all_authority_receipts(capture)),
        )
        remaining = (deadline - now).total_seconds()
        if remaining < 0.01:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        return min(float(self.config.command_timeout_seconds), remaining)

    def _now(self) -> datetime:
        try:
            return aware_utc(self.clock())
        except ValueError:
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID) from None


def _render_rest_target(
    config: HostLiveReadConfig,
    target: PlannedTarget,
    values: Mapping[str, str],
) -> tuple[str, int]:
    spec = target.specification
    route = str(spec.get("routeTemplate", ""))
    variables = spec.get("variables")
    if not isinstance(variables, list):
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    expected_names: set[str] = set()
    synthetic_names: set[str] = set()
    for variable in variables:
        if not isinstance(variable, dict) or not isinstance(variable.get("name"), str):
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        name = variable["name"]
        expected_names.add(name)
        source = variable.get("valueSource")
        if source == "SYNTHETIC_DATASET_RECEIPT":
            synthetic_names.add(name)
        value = config.api_version if source == "API_VERSION" else values.get(name)
        if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        route = route.replace("{" + name + "}", value)
    if set(values) != synthetic_names or len(expected_names) != len(variables):
        raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
    prefix = (
        "/services/data/"
        if target.partition is TargetPartition.STANDARD_REST
        else "/services/apexrest/"
    )
    parsed = urlsplit(route)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith(prefix)
        or "{" in route
        or ".." in parsed.path.split("/")
    ):
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    maximum = int(spec.get("maximumResponseBytes", 0))
    if maximum < 1 or maximum > config.maximum_cli_output_bytes:
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    return route, maximum


def _render_scoped_rest_target(
    config: HostLiveReadConfig,
    target: PlannedTarget,
    values: Mapping[str, str],
    dataset: ResolvedDatasetScope,
) -> tuple[str, int]:
    route, maximum = _render_rest_target(config, target, values)
    if target.partition is TargetPartition.STANDARD_REST:
        expected = f"/services/data/v{config.api_version}/sobjects/{dataset.object_api_name}/"
        supplied_path, _, supplied_query = route.partition("?")
        expected_query = "fields=" + ",".join(dataset.field_projection)
        if (
            dataset.expected_records != 1
            or not supplied_path.startswith(expected)
            or supplied_path[len(expected) :] != dataset.record_ids[0]
            or (supplied_query and supplied_query != expected_query)
            or not _METADATA_NAME.fullmatch(dataset.object_api_name)
            or any(not _METADATA_NAME.fullmatch(name) for name in dataset.field_projection)
        ):
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        route = supplied_path + "?" + expected_query
    return route, maximum


def _rest_invocation_bindings(
    config: HostLiveReadConfig,
    target: PlannedTarget,
    resolved: ResolvedTargetVariables | None,
    dataset: ResolvedDatasetScope,
) -> tuple[tuple[ResolvedVariableRow, ResolvedDatasetScope], ...]:
    """Expand exactly every independently admitted member, never a selected sample."""
    if (
        target.specification.get("requestExpansion") != "EACH_DATASET_RECORD"
        or resolved is None
        or resolved.target_sha256 != target.target_sha256
        or tuple(row.record_id for row in resolved.rows) != dataset.record_ids
        or len(resolved.rows) != dataset.expected_records
    ):
        raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
    bindings = []
    for row in resolved.rows:
        for variable in target.specification.get("variables", []):
            if variable.get("valueSource") == "API_VERSION":
                if variable.get("datasetField") is not None:
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
                continue
            field_name = variable.get("datasetField")
            expected = (
                row.record_id
                if field_name == dataset.record_id_field
                else dataset.member_predicate_values[row.record_id].get(field_name)
            )
            if not isinstance(expected, str) or row.values.get(variable["name"]) != expected:
                raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
        member = dataset.model_copy(
            update={
                "record_ids": (row.record_id,),
                "expected_records": 1,
                "member_predicate_values": {
                    row.record_id: dataset.member_predicate_values[row.record_id]
                },
            }
        )
        _render_scoped_rest_target(config, target, row.values, member)
        bindings.append((row, member))
    return tuple(bindings)


def _metadata_target(target: PlannedTarget) -> tuple[str, str]:
    spec = target.specification
    metadata_type = spec.get("metadataType")
    member = spec.get("member")
    if (
        not isinstance(metadata_type, str)
        or not _METADATA_NAME.fullmatch(metadata_type)
        or metadata_type
        not in {
            *_METADATA_FILE_LAYOUTS,
            *_CONTAINED_METADATA_TYPES,
            "LightningComponentBundle",
            "AuraDefinitionBundle",
        }
        or _metadata_key(metadata_type) in _DENIED_METADATA
        or not isinstance(member, str)
        or not _METADATA_MEMBER.fullmatch(member)
        or "*" in member
    ):
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    if int(spec.get("maximumFiles", 0)) < 1 or int(spec.get("maximumBytes", 0)) < 1:
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    return metadata_type, member


def _classification_pins(config: HostLiveReadConfig) -> ClassificationPins:
    return ClassificationPins(
        alias=config.alias,
        api_version="v" + config.api_version,
        org_fingerprint_sha256=config.org_fingerprint_sha256,
        actor_fingerprint_sha256=config.actor_fingerprint_sha256,
        actor_user_id_sha256=config.actor_user_id_sha256,
        instance_host_sha256=config.instance_host_sha256,
        edition_sha256=config.edition_sha256,
        environment_class=config.environment_class,
    )


def _unwrap_rest_response(payload: dict[str, Any], *, maximum: int) -> dict[str, Any]:
    """Unwrap sf plugin-api's transport envelope, never treating it as application data.

    HTTP headers and CLI warnings are bounded transient transport metadata. They are neither
    response projections nor evidence artifacts and may contain session-bearing values.
    """
    if (
        set(payload) != {"status", "result", "warnings"}
        or type(payload["status"]) is not int
        or payload["status"] != 0
        or not isinstance(payload["warnings"], list)
        or len(payload["warnings"]) > 64
        or any(not isinstance(value, str) or len(value) > 8192 for value in payload["warnings"])
    ):
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    response = payload["result"]
    if not isinstance(response, dict) or set(response) != {"statusCode", "headers", "body"}:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    if type(response["statusCode"]) is not int or not 200 <= response["statusCode"] < 300:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    headers = response["headers"]
    if not isinstance(headers, dict) or len(headers) > 128:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    for name, value in headers.items():
        if not isinstance(name, str) or not _HTTP_HEADER_NAME.fullmatch(name):
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        values = value if isinstance(value, list) else [value]
        if (
            not values
            or len(values) > 64
            or any(not isinstance(item, str) or len(item) > 8192 for item in values)
        ):
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    if len(_canonical_bytes({"headers": headers, "warnings": payload["warnings"]})) > 65_536:
        raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
    body = response["body"]
    # plugin-api already parses JSON. Re-parsing a string would turn a different response
    # contract into an object heuristically, while accepting the wrapper would be a false pass.
    if not isinstance(body, dict):
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    if len(_canonical_bytes(body)) > maximum:
        raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
    return body


def _strict_json(raw: bytes) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def reject_nonfinite(_: str) -> None:
        raise ValueError("non-finite JSON number")

    value = json.loads(
        raw.decode("utf-8", "strict"), object_pairs_hook=hook, parse_constant=reject_nonfinite
    )
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    return parse_aware_utc(value)


def _dataset_scope_digest(capture: HostLiveReadCapture) -> str:
    return stable_sha256(
        {
            "expectedExecutionContractSha256": capture.expected_execution_contract_sha256,
            "resolvedDatasetScopes": [
                item.model_dump(mode="json") for item in capture.resolved_dataset_scopes
            ],
            "resolvedVariables": [
                item.model_dump(mode="json") for item in capture.resolved_variables
            ],
        }
    )


def _validate_dataset_contract(
    dataset: ResolvedDatasetScope, plan: LiveTargetPlan, *, actor_user_id_sha256: str
) -> None:
    selected = [
        item
        for item in plan.targets
        if item.target_sha256 == dataset.dataset_target_sha256
        and item.partition is TargetPartition.SYNTHETIC_DATASET
    ]
    if len(selected) != 1:
        raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
    rest_targets = [item for item in plan.targets if item.target_sha256 == dataset.target_sha256]
    if len(rest_targets) != 1 or not set(rest_targets[0].source_entity_ids).issubset(
        selected[0].source_entity_ids
    ):
        raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
    spec = selected[0].specification
    predicates = spec.get("predicates", [])
    if (
        dataset.object_api_name != spec.get("objectApiName")
        or dataset.field_projection != tuple(sorted(spec.get("fieldProjection", [])))
        or dataset.ownership_marker_field != spec.get("ownershipMarkerField")
        or dataset.record_id_field != spec.get("identityField")
        or dataset.expected_records != spec.get("maximumRecords", 0)
        or any(item.get("operator") not in {"EQUALS", "IN_SET"} for item in predicates)
        or any(
            set(values) != {item.get("field") for item in predicates}
            for values in dataset.member_predicate_values.values()
        )
        or dataset.response_record_path != rest_targets[0].specification.get("responseRecordPath")
        or dataset.field_paths != rest_targets[0].specification.get("datasetFieldPaths")
    ):
        raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
    for values in dataset.member_predicate_values.values():
        for predicate in predicates:
            if predicate.get("valueSource") == "SOURCE_LITERAL_SET":
                allowed = predicate.get("values", [])
                actual = values[predicate["field"]]
                if not any(type(actual) is type(value) and actual == value for value in allowed):
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)
            elif predicate.get("valueSource") == "CURRENT_ENROLLED_ACTOR":
                actual = values[predicate["field"]]
                if not isinstance(actual, str) or _digest(actual) != actor_user_id_sha256:
                    raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)


def _response_path_valid(path: str) -> bool:
    return bool(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[\])?)*", path)
    )


def _path_values(value: Any, path: str) -> list[Any]:
    values = [value]
    if not path:
        return values
    for segment in path.split("."):
        array = segment.endswith("[]")
        key = segment[:-2] if array else segment
        next_values: list[Any] = []
        for current in values:
            if not isinstance(current, dict) or key not in current:
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            child = current[key]
            if array:
                if not isinstance(child, list) or len(child) > 1000:
                    raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
                next_values.extend(child)
            else:
                next_values.append(child)
        values = next_values
    return values


def _validate_dataset_response(
    result: Any,
    scope: ResolvedDatasetScope,
    *,
    parent_bindings: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, int]:
    bound_collections: set[str] = set()
    for binding in parent_bindings:
        if (
            set(binding) != {"collectionPath", "parentIdPath", "datasetField", "maximumCardinality"}
            or binding["datasetField"] != scope.record_id_field
            or not isinstance(binding["collectionPath"], str)
            or not binding["collectionPath"].endswith("[]")
            or not _response_path_valid(binding["collectionPath"])
            or not isinstance(binding["parentIdPath"], str)
            or not _response_path_valid(binding["parentIdPath"])
            or type(binding["maximumCardinality"]) is not int
            or not 1 <= binding["maximumCardinality"] <= 1000
            or binding["collectionPath"] in bound_collections
            or scope.expected_records != 1
        ):
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        bound_collections.add(binding["collectionPath"])
        children = _path_values(result, binding["collectionPath"])
        if len(children) > binding["maximumCardinality"]:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        for child in children:
            if _path_values(child, binding["parentIdPath"]) != [scope.record_ids[0]]:
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)

    def check_collection_scope(value: Any, path: str = "") -> None:
        if isinstance(value, list):
            collection_path = path + "[]"
            if (
                value
                and collection_path != scope.response_record_path
                and collection_path not in bound_collections
            ):
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            for child in value:
                check_collection_scope(child, collection_path)
        elif isinstance(value, dict):
            for name, child in value.items():
                check_collection_scope(child, f"{path}.{name}".lstrip("."))

    check_collection_scope(result)
    records = _path_values(result, scope.response_record_path)
    if len(records) != scope.expected_records:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    observed_ids: list[str] = []
    for record in records:
        projected: dict[str, Any] = {}
        for field_name, response_path in scope.field_paths.items():
            values = _path_values(record, response_path)
            if len(values) != 1:
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            projected[field_name] = values[0]
        identity = projected[scope.record_id_field]
        if (
            not isinstance(identity, str)
            or identity not in scope.record_ids
            or any(
                type(projected[key]) is not type(value) or projected[key] != value
                for key, value in scope.member_predicate_values[identity].items()
                if key in projected
            )
        ):
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        observed_ids.append(identity)
    if tuple(sorted(observed_ids)) != scope.record_ids:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    return stable_sha256(
        {"scope": scope.model_dump(mode="json"), "matchedRecords": len(records)}
    ), len(records)


def _validate_response_fields(result: Any, raw_fields: Any) -> tuple[str, int]:
    if not isinstance(raw_fields, list) or not raw_fields:
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    outcomes: list[dict[str, Any]] = []
    types = {
        "STRING": str,
        "INTEGER": int,
        "NUMBER": (int, float),
        "BOOLEAN": bool,
        "OBJECT": dict,
        "ARRAY": list,
    }
    tree: dict[str, Any] = {}
    for raw in raw_fields:
        if (
            not isinstance(raw, dict)
            or not {"path", "dataType", "required", "nullable"}.issubset(raw)
            or set(raw) - {"path", "dataType", "required", "nullable", "expectedLiteral"}
            or not isinstance(raw.get("path"), str)
            or not _response_path_valid(raw["path"])
            or raw.get("dataType") not in types
            or type(raw.get("required")) is not bool
            or type(raw.get("nullable")) is not bool
        ):
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        try:
            # The wrapper distinguishes absent constraint from an explicit null
            # literal and shares strict primitive/type rules with the compiler.
            ResponseField.model_validate(raw)
        except ValueError:
            raise LiveReadError(LiveReadCode.TARGET_INVALID) from None
        node = tree
        for part in raw["path"].split("."):
            node = node.setdefault(part, {})
        if "$field" in node:
            raise LiveReadError(LiveReadCode.TARGET_INVALID)
        node["$field"] = raw

    def validate_object(current: Any, node: dict[str, Any], prefix: str) -> None:
        if not isinstance(current, dict):
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        expected_keys = {key.removesuffix("[]") for key in node if key != "$field"}
        if set(current) - expected_keys:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        for key, branch in node.items():
            if key == "$field":
                continue
            name = key.removesuffix("[]")
            raw = branch.get("$field")
            children = {child: value for child, value in branch.items() if child != "$field"}
            path = f"{prefix}.{key}".lstrip(".")
            if name not in current:
                if raw is None or raw["required"]:
                    raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
                continue
            value = current[name]
            literal = raw.get("expectedLiteral") if raw is not None else None
            literal_sha = stable_sha256(literal) if literal is not None else None
            if literal is not None and stable_sha256({"value": value}) != literal_sha:
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            # Required governs presence; nullable independently governs an explicit
            # JSON null. A null parent does not fabricate absent child values.
            if value is None:
                if raw is None or not raw["nullable"]:
                    raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
                outcomes.append(
                    {
                        "path": path,
                        "found": True,
                        "type": raw["dataType"],
                        "null": True,
                        **({"literal_sha256": literal_sha} if literal_sha is not None else {}),
                    }
                )
                continue
            if raw is not None:
                data_type = raw["dataType"]
                if (
                    not isinstance(value, types[data_type])
                    or (data_type in {"INTEGER", "NUMBER"} and isinstance(value, bool))
                    or (isinstance(value, float) and not math.isfinite(value))
                ):
                    raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
                if data_type in {"OBJECT", "ARRAY"} and not children:
                    raise LiveReadError(LiveReadCode.TARGET_INVALID)
            if key.endswith("[]"):
                if not isinstance(value, list) or len(value) > 1000 or not children:
                    raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
                for item in value:
                    validate_object(item, children, path)
            elif children:
                validate_object(value, children, path)
            elif isinstance(value, (dict, list)):
                raise LiveReadError(LiveReadCode.TARGET_INVALID)
            outcomes.append(
                {
                    "path": path,
                    "found": True,
                    "type": raw["dataType"] if raw else "OBJECT",
                    **({"literal_sha256": literal_sha} if literal_sha is not None else {}),
                }
            )

    validate_object(result, tree, "")
    return stable_sha256(outcomes), len(outcomes)


def _scan_stage(
    stage: Path,
    *,
    maximum_files: int,
    maximum_bytes: int,
    allowed_paths: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if maximum_files < 1 or maximum_bytes < 1:
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    entries: list[dict[str, Any]] = []
    stage_identity = _checked_identity(stage, directory=True)
    total = 0
    directory_count = 0
    for root, directories, files in os.walk(stage, followlinks=False):
        _checked_identity(Path(root), directory=True)
        directories.sort()
        files.sort()
        directory_count += len(directories)
        if directory_count > _MAX_STAGE_DIRECTORIES:
            raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
        for name in [*directories, *files]:
            path = Path(root) / name
            metadata = path.lstat()
            mode = metadata.st_mode
            reparse = getattr(metadata, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            )
            if stat.S_ISLNK(mode) or reparse or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        for name in files:
            path = Path(root) / name
            relative = path.relative_to(stage).as_posix()
            if not _safe_metadata_path(relative) or (
                allowed_paths is not None and relative not in allowed_paths
            ):
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            if len(entries) >= maximum_files:
                raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
            size = path.stat(follow_symlinks=False).st_size
            total += size
            if total > maximum_bytes:
                raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
            content = _read_nofollow(path, maximum_bytes=maximum_bytes - total + size)
            if len(content) != size:
                raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
            entries.append(
                {
                    "relative_path": path.relative_to(stage).as_posix(),
                    "relative_sha256": _digest(path.relative_to(stage).as_posix()),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "size": size,
                    **(
                        {"manifest": _metadata_manifest(content)}
                        if path.relative_to(stage).as_posix() == "unpackaged/package.xml"
                        else {}
                    ),
                }
            )
    _require_identity(stage, stage_identity, directory=True)
    if not entries:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    return entries, total


def _checked_identity(path: Path, *, directory: bool) -> tuple[int, int, int, int]:
    try:
        absolute = path.absolute()
        for ancestor in reversed(absolute.parents):
            metadata = ancestor.lstat()
            if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        metadata = absolute.lstat()
        if _is_reparse(metadata) or not (
            stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        ):
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        if not directory and metadata.st_nlink != 1:
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns
    except OSError:
        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID) from None


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400
    )


def _require_identity(path: Path, expected: tuple[int, int, int, int], *, directory: bool) -> None:
    actual = _checked_identity(path, directory=directory)
    if (actual[:2] if directory else actual) != (expected[:2] if directory else expected):
        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)


def _windows_open_no_follow(path: Path, *, directory: bool) -> tuple[Any, Any]:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Excluding FILE_SHARE_DELETE prevents replacement of the directory/file while held.
    # Files additionally exclude FILE_SHARE_WRITE so their bytes cannot change during hashing.
    handle = create(
        str(path.absolute()),
        0 if directory else 0x80000000,
        3 if directory else 1,
        None,
        3,
        0x00200000 | (0x02000000 if directory else 0),
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
    return handle, kernel


class _DirectoryGuards:
    def __init__(self, path: Path) -> None:
        self.handles: list[tuple[Any, Any]] = []
        try:
            for directory in (*reversed(path.absolute().parents), path.absolute()):
                before = _checked_identity(directory, directory=True)
                if os.name == "nt":
                    self.handles.append(_windows_open_no_follow(directory, directory=True))
                _require_identity(directory, before, directory=True)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for handle, kernel in reversed(self.handles):
            kernel.CloseHandle(handle)
        self.handles.clear()


def _read_nofollow(path: Path, *, maximum_bytes: int) -> bytes:
    before = _checked_identity(path, directory=False)
    descriptor: int | None = None
    guards = _DirectoryGuards(path.parent)
    try:
        if os.name == "nt":
            import msvcrt

            handle, _ = _windows_open_no_follow(path, directory=False)
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        else:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        observed = os.fstat(descriptor)
        if _is_reparse(observed) or (observed.st_dev, observed.st_ino) != before[:2]:
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            content = stream.read(maximum_bytes + 1)
            after = os.fstat(stream.fileno())
        if len(content) > maximum_bytes:
            raise LiveReadError(LiveReadCode.OUTPUT_LIMIT)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != before:
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        _require_identity(path, before, directory=False)
        return content
    finally:
        if descriptor is not None:
            os.close(descriptor)
        guards.close()


def _prepare_staging_root(repository_root: Path, root: Path) -> None:
    repository_root = repository_root.absolute()
    _checked_identity(repository_root, directory=True)
    try:
        relative = root.relative_to(repository_root)
        if relative.parts[0] != ".runtime" or ".." in relative.parts:
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        for arguments, expected in (
            (
                ("check-ignore", "--no-index", "--", relative.as_posix() + "/scope-probe"),
                (relative.as_posix() + "/scope-probe").encode(),
            ),
            (("ls-files", "-z", "--", ".runtime"), b""),
        ):
            result = subprocess.run(
                ("git", "-C", str(repository_root), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                env=build_subprocess_environment(),
            )
            if result.returncode != 0 or result.stdout.strip() != expected:
                raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        current = repository_root
        for part in relative.parts:
            guards = _DirectoryGuards(current)
            try:
                current = current / part
                if not os.path.lexists(current):
                    current.mkdir(exist_ok=True)
                _checked_identity(current, directory=True)
            finally:
                guards.close()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID) from None


def _cleanup_stage(stage: Path) -> None:
    """No recursive operation follows an unverified path, even after a failed scan."""
    _checked_identity(stage, directory=True)
    for root, directories, files in os.walk(stage, topdown=False, followlinks=False):
        current = Path(root)
        _checked_identity(current, directory=True)
        for name in files:
            path = current / name
            _checked_identity(path, directory=False)
            path.unlink()
        for name in directories:
            directory = current / name
            _checked_identity(directory, directory=True)
            directory.rmdir()


def _metadata_expected_paths(
    response: dict[str, Any],
    metadata_type: str,
    member: str,
) -> set[str]:
    result = response.get("result")
    if (
        not isinstance(result, dict)
        or result.get("done") is not True
        or result.get("success") is not True
        or result.get("status") != "Succeeded"
        or result.get("messages")
        or result.get("errorMessage")
        or result.get("errorStatusCode")
    ):
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    properties = result.get("fileProperties")
    if not isinstance(properties, list) or not properties:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    expected_paths: set[str] = set()
    selected_count = 0
    for item in properties:
        if not isinstance(item, dict):
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        path = item.get("fileName")
        if not isinstance(path, str) or not _safe_metadata_path(path) or path in expected_paths:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        if item.get("type") == "Package" and item.get("fullName") in {
            "package",
            "unpackaged/package.xml",
        }:
            if path != "unpackaged/package.xml":
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        elif (item.get("type"), item.get("fullName")) == _metadata_file_property_identity(
            metadata_type, member
        ):
            if not _metadata_member_path(path, metadata_type, member):
                raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
            selected_count += 1
        else:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        expected_paths.add(path)
    if selected_count < 1 or "unpackaged/package.xml" not in expected_paths:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    return expected_paths


def _validate_metadata_completion(
    response: dict[str, Any],
    metadata_type: str,
    member: str,
    entries: list[dict[str, Any]],
    *,
    payload_root: Path | None = None,
) -> None:
    expected_paths = _metadata_expected_paths(response, metadata_type, member)
    actual_paths = {item["relative_path"] for item in entries}
    # Companion descriptor files are part of an exact same component, never an extra member.
    companions = {
        path + "-meta.xml" for path in expected_paths if path.endswith((".cls", ".trigger"))
    }
    if (
        not expected_paths.issubset(actual_paths)
        or actual_paths - expected_paths - companions
        or "unpackaged/package.xml" not in actual_paths
    ):
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    manifest = next(
        item.get("manifest")
        for item in entries
        if item["relative_path"] == "unpackaged/package.xml"
    )
    if manifest != [(metadata_type, member)]:
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    if metadata_type == "CustomField":
        if payload_root is None:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        object_name, field_name = _custom_field_identity(member)
        component = payload_root / f"unpackaged/objects/{object_name}.object"
        scanned = next(
            (
                item
                for item in entries
                if item["relative_path"] == component.relative_to(payload_root).as_posix()
            ),
            None,
        )
        if scanned is None or int(scanned["size"]) < 1:
            raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
        content = _read_nofollow(component, maximum_bytes=int(scanned["size"]))
        if (
            len(content) != int(scanned["size"])
            or hashlib.sha256(content).hexdigest() != scanned["content_sha256"]
        ):
            raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID)
        _validate_custom_field_component(content, field_name)


def _metadata_payload_root(stage: Path) -> Path:
    """Resolve the two bounded layouts emitted by supported Salesforce CLI versions."""

    direct_manifest = stage / "unpackaged/package.xml"
    if direct_manifest.is_file():
        return stage
    wrapper = stage / "unpackaged"
    nested_manifest = wrapper / "unpackaged/package.xml"
    try:
        _checked_identity(stage, directory=True)
        _checked_identity(wrapper, directory=True)
        children = tuple(sorted(item.name for item in stage.iterdir()))
    except OSError:
        raise LiveReadError(LiveReadCode.METADATA_STAGING_INVALID) from None
    if children != ("unpackaged",) or not nested_manifest.is_file():
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    return wrapper


def _custom_field_identity(member: str) -> tuple[str, str]:
    object_name, separator, field_name = member.partition(".")
    if (
        separator != "."
        or "." in field_name
        or not _METADATA_NAME.fullmatch(object_name)
        or not _METADATA_NAME.fullmatch(field_name)
    ):
        raise LiveReadError(LiveReadCode.TARGET_INVALID)
    return object_name, field_name


def _metadata_file_property_identity(metadata_type: str, member: str) -> tuple[str, str]:
    if metadata_type == "CustomField":
        object_name, _ = _custom_field_identity(member)
        return "CustomObject", object_name
    return metadata_type, member


def _validate_custom_field_component(content: bytes, field_name: str) -> None:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    namespace = "{http://soap.sforce.com/2006/04/metadata}"
    try:
        root = ET.fromstring(content)
        if root.tag != namespace + "CustomObject":
            raise ValueError("invalid custom object")
        components = list(root)
        if len(components) != 1 or components[0].tag != namespace + "fields":
            raise ValueError("custom field retrieval included another component")
        names = components[0].findall(namespace + "fullName")
        if len(names) != 1 or (names[0].text or "") != field_name:
            raise ValueError("retrieved field does not match requested member")
    except (ET.ParseError, ValueError):
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID) from None


def _metadata_manifest(content: bytes) -> list[tuple[str, str]]:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID)
    try:
        root = ET.fromstring(content)
        namespace = "{http://soap.sforce.com/2006/04/metadata}"
        if root.tag != namespace + "Package":
            raise ValueError("invalid package")
        entries: list[tuple[str, str]] = []
        versions = 0
        for child in root:
            if child.tag == namespace + "version":
                versions += 1
                continue
            if child.tag != namespace + "types":
                raise ValueError("unexpected package element")
            names = child.findall(namespace + "name")
            members = child.findall(namespace + "members")
            if len(names) != 1 or not members or len(child) != len(members) + 1:
                raise ValueError("invalid package member")
            name = names[0].text or ""
            for member in members:
                value = member.text or ""
                if not _METADATA_NAME.fullmatch(name) or not _METADATA_MEMBER.fullmatch(value):
                    raise ValueError("invalid package member")
                entries.append((name, value))
        if versions != 1 or len(entries) != len(set(entries)):
            raise ValueError("invalid package")
        return sorted(entries)
    except (ET.ParseError, ValueError):
        raise LiveReadError(LiveReadCode.RESPONSE_INVALID) from None


def _safe_metadata_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (
        not parsed.is_absolute()
        and ".." not in parsed.parts
        and "\\" not in path
        and parsed.as_posix() == path
        and bool(re.fullmatch(r"[A-Za-z0-9_./-]+", path))
    )


def _metadata_member_path(path: str, metadata_type: str, member: str) -> bool:
    if metadata_type == "CustomField":
        object_name, _ = _custom_field_identity(member)
        return path == f"unpackaged/objects/{object_name}.object"
    if metadata_type in _METADATA_FILE_LAYOUTS:
        folder, suffix = _METADATA_FILE_LAYOUTS[metadata_type]
        return path == f"unpackaged/{folder}/{member}{suffix}"
    if metadata_type in {"LightningComponentBundle", "AuraDefinitionBundle"}:
        folder = "lwc" if metadata_type == "LightningComponentBundle" else "aura"
        return (
            path.startswith(f"unpackaged/{folder}/{member}/")
            and len(PurePosixPath(path).parts) == 4
        )
    return False


def _metadata_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


__all__ = [
    "CliCompleted",
    "CliInvocation",
    "CliRunner",
    "HostLiveReadCapture",
    "HostLiveReadConfig",
    "HostLiveReadInputPort",
    "HostOwnedLiveReadExecutor",
    "LiveReadCode",
    "LiveReadError",
    "LiveReadObservation",
    "LiveReadResult",
    "ResolvedTargetVariables",
    "ResolvedDatasetScope",
    "SubprocessCliRunner",
]
