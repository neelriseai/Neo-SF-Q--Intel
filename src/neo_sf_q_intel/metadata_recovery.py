"""Journaled single-property metadata transaction; host wiring is deliberately disabled.

Only a private, exact MDAPI package is ever deployed. This is not a source compiler,
classification authority, browser driver or accepted campaign receipt producer.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import io
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol
from uuid import uuid4
from xml.parsers import expat
from xml.sax.saxutils import escape

from pydantic import ConfigDict, Field, StrictBool, StrictInt, model_validator

from neo_sf_q_intel.live_read_evidence import (
    CliInvocation,
    CliRunner,
    SubprocessCliRunner,
    _checked_identity,
    _DirectoryGuards,
    _metadata_expected_paths,
    _metadata_manifest,
    _metadata_member_path,
    _prepare_staging_root,
    _read_nofollow,
    _safe_metadata_path,
    _scan_stage,
    _validate_metadata_completion,
    _windows_open_no_follow,
)
from neo_sf_q_intel.live_target_plan import BrowserMetadataDrift
from neo_sf_q_intel.temporal import UtcModel, aware_utc

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_NS = "http://soap.sforce.com/2006/04/metadata"
_JOB = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _root(value: UtcModel) -> str:
    return _sha(_canonical(value.model_dump(mode="json", warnings=False)))


class _Model(UtcModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetadataRecoveryCode(StrEnum):
    DISABLED = "METADATA_RECOVERY_DISABLED"
    AUTHORITY_REJECTED = "METADATA_RECOVERY_AUTHORITY_REJECTED"
    JOURNAL_REPLAY = "METADATA_RECOVERY_JOURNAL_REPLAY"
    TRANSACTION_BUSY = "METADATA_RECOVERY_TRANSACTION_BUSY"
    STAGING_INVALID = "METADATA_RECOVERY_STAGING_INVALID"
    SOURCE_BINDING_INVALID = "METADATA_RECOVERY_SOURCE_BINDING_INVALID"
    PROPERTY_UNSUPPORTED = "METADATA_RECOVERY_PROPERTY_UNSUPPORTED"
    IMMUTABLE_STAGING_UNAVAILABLE = "METADATA_RECOVERY_IMMUTABLE_STAGING_UNAVAILABLE"
    CLI_FAILED = "METADATA_RECOVERY_CLI_FAILED"
    PROCESS_NOT_QUIESCENT = "METADATA_RECOVERY_PROCESS_NOT_QUIESCENT"
    JOB_UNKNOWN = "METADATA_RECOVERY_JOB_UNKNOWN"
    CHECK_ONLY_FAILED = "METADATA_RECOVERY_CHECK_ONLY_FAILED"
    CANDIDATE_MISMATCH = "METADATA_RECOVERY_CANDIDATE_MISMATCH"
    CONCURRENT_CHANGE = "METADATA_RECOVERY_CONCURRENT_CHANGE"
    CANDIDATE_WINDOW_FAILED = "METADATA_RECOVERY_CANDIDATE_WINDOW_FAILED"
    RESTORE_UNRESOLVED = "METADATA_RECOVERY_RESTORE_UNRESOLVED"


class MetadataRecoveryError(RuntimeError):
    def __init__(self, code: MetadataRecoveryCode):
        self.code = code
        super().__init__(code.value)


class MetadataPropertyIntent(_Model):
    """Consumes the compiler's closed scalar SET; no caller XPath, path, or code."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    scope_sha256: Digest
    source_contract_sha256: Digest
    source_property_declaration_sha256: Digest
    target_plan_sha256: Digest
    drift: BrowserMetadataDrift
    expected_preimage_sha256: Digest
    expected_candidate_sha256: Digest
    api_version: str = Field(pattern=r"^[1-9][0-9]{0,2}\.0$")
    test_level: Literal["NoTestRun", "RunSpecifiedTests"]
    test_classes: tuple[str, ...] = Field(max_length=200)
    tests: tuple[str, ...] = Field(max_length=200)
    expected_test_count: StrictInt = Field(ge=0, le=10_000)

    @property
    def metadata_type(self) -> str:
        return self.drift.metadata_type

    @property
    def member(self) -> str:
        return self.drift.member

    @property
    def relative_file(self) -> str:
        return f"unpackaged/flexipages/{self.drift.member}.flexipage"

    @model_validator(mode="after")
    def closed_intent(self) -> MetadataPropertyIntent:
        if not _safe_metadata_path(self.relative_file) or not _metadata_member_path(
            self.relative_file, self.metadata_type, self.member
        ):
            raise ValueError("Unsupported or non-exact single-file scalar metadata modification")
        if self.tests != tuple(sorted(set(self.tests))) or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,199}\.[A-Za-z][A-Za-z0-9_]{0,199}", value)
            for value in self.tests
        ):
            raise ValueError("Test methods must be exact, sorted and unique")
        if self.test_classes != tuple(sorted(set(self.test_classes))) or any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,199}", value) for value in self.test_classes
        ):
            raise ValueError("Deployment test classes must be exact, sorted and unique")
        if self.test_level == "NoTestRun":
            if self.test_classes or self.tests or self.expected_test_count:
                raise ValueError("NoTestRun must not pretend to execute tests")
        elif (
            not self.test_classes
            or not self.tests
            or self.expected_test_count != len(self.tests)
            or tuple(sorted({method.split(".")[0] for method in self.tests})) != self.test_classes
        ):
            raise ValueError("Specified tests require exact independent method accounting")
        return self


class MetadataRecoveryLease(_Model):
    intent_sha256: Digest
    execution_binding_sha256: Digest
    org_fingerprint_sha256: Digest
    actor_fingerprint_sha256: Digest
    classification_receipt_sha256: Digest
    mutation_authority_receipt_sha256: Digest
    recovery_authority_receipt_sha256: Digest
    restore_rehearsal_receipt_sha256: Digest
    one_use_claim_sha256: Digest
    issued_at: datetime
    expires_at: datetime
    recovery_deadline: datetime

    @model_validator(mode="after")
    def bounded(self) -> MetadataRecoveryLease:
        if not self.issued_at < self.expires_at < self.recovery_deadline:
            raise ValueError("Invalid metadata recovery authority interval")
        if self.recovery_deadline - self.issued_at > timedelta(hours=1):
            raise ValueError("Metadata authority exceeds bounded lifetime")
        return self


class MetadataRecoveryAuthority(Protocol):
    """Host-owned, independently signed authority and durable claims, never request flags.

    Validate current non-production org AND effective actor at each call; bind alias/CLI/API
    versions, complete source declaration, exact package/test/operation set and preissued
    recovery authority. Recovery capability is verified locally after dispatch without
    depending on continued issuer availability. Claim/expiry checks cannot authorize scope.
    """

    def verify(
        self,
        intent: MetadataPropertyIntent,
        lease: MetadataRecoveryLease,
        binding: MetadataExecutionBinding,
        operation: str,
        package_sha256: str | None,
        now: datetime,
    ) -> None: ...

    def claim_dispatch_once(
        self,
        intent: MetadataPropertyIntent,
        lease: MetadataRecoveryLease,
        operation: str,
        package_sha256: str,
        now: datetime,
    ) -> str:
        """Atomically persist an independently durable one-use dispatch fence, returning its SHA.

        This authority store is separate from the local transaction journal; truncating a
        journal must not roll it back. Existing claims reject, never return authorization again.
        """
        ...

    def read_dispatch_fence(
        self, intent: MetadataPropertyIntent, lease: MetadataRecoveryLease, operation: str
    ) -> str | None:
        """Authenticate the independent fence; unavailable is an error, never absence."""
        ...

    def note_process_state(
        self,
        intent: MetadataPropertyIntent,
        lease: MetadataRecoveryLease,
        operation: str,
        quiescent: bool | None,
    ) -> None:
        """Durably record UNKNOWN before transport, then its authenticated containment result."""
        ...

    def require_recovery_quiescence(
        self, intent: MetadataPropertyIntent, lease: MetadataRecoveryLease
    ) -> None:
        """Independent process-tree proof; unknown/false or unavailable blocks resume transport."""
        ...


class MetadataRecoveryConfig(_Model):
    alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    expected_cli_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    journal_root: str = ".runtime/metadata-recovery"
    maximum_bytes: StrictInt = Field(default=2_097_152, ge=1024, le=16_777_216)
    operation_timeout_seconds: StrictInt = Field(default=30, ge=1, le=120)
    mutation_enabled: StrictBool = False

    @model_validator(mode="after")
    def private_relative_root(self) -> MetadataRecoveryConfig:
        path = PurePosixPath(self.journal_root)
        if (
            not _safe_metadata_path(self.journal_root)
            or not path.parts
            or path.parts[0] != ".runtime"
            or len(path.parts) < 2
        ):
            raise ValueError(
                "Metadata recovery journal must be private repository-relative runtime state"
            )
        return self


class MetadataExecutionBinding(_Model):
    alias: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    api_version: str = Field(pattern=r"^[1-9][0-9]{0,2}\.0$")
    expected_cli_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    transport: Literal["DIRECT_SHELL_FALSE_SF_CLI"] = "DIRECT_SHELL_FALSE_SF_CLI"
    runner_implementation_sha256: Digest


def metadata_execution_binding(
    config: MetadataRecoveryConfig, api_version: str
) -> MetadataExecutionBinding:
    return MetadataExecutionBinding(
        alias=config.alias,
        api_version=api_version,
        expected_cli_version=config.expected_cli_version,
        runner_implementation_sha256=_sha(Path(inspect.getfile(SubprocessCliRunner)).read_bytes()),
    )


class MetadataRecoveryReport(_Model):
    evidence_class: Literal["OFFLINE_ADAPTER_CONTRACT_NOT_ACCEPTANCE_RECEIPT"] = (
        "OFFLINE_ADAPTER_CONTRACT_NOT_ACCEPTANCE_RECEIPT"
    )
    acceptance_credit: Literal[False] = False
    release_eligible: Literal[False] = False
    intent_sha256: Digest
    journal_sha256: Digest | None
    status: Literal["NOT_RUN", "RESTORED", "FAILED_RESTORED", "FAILED_BEFORE_DEPLOY", "QUARANTINED"]
    candidate_reconciled: bool
    restored_exactly: bool
    zero_scoped_residue: bool
    gap_codes: tuple[MetadataRecoveryCode, ...]


def _parse_xml(content: bytes) -> ET.Element:
    if b"\x00" in content or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise MetadataRecoveryError(MetadataRecoveryCode.PROPERTY_UNSUPPORTED)
    try:
        content.decode("utf-8")
        return ET.fromstring(content)
    except (UnicodeError, ET.ParseError):
        raise MetadataRecoveryError(MetadataRecoveryCode.PROPERTY_UNSUPPORTED) from None


def _xml_identity(element: ET.Element):
    children = list(element)
    text = element.text or ""
    return {
        "tag": element.tag,
        "attributes": sorted(element.attrib.items()),
        "text": None if children and not text.strip() else text,
        "children": [_xml_identity(child) for child in children],
        "tail": element.tail if element.tail and element.tail.strip() else None,
    }


def metadata_state_sha256(files: dict[str, bytes]) -> str:
    """Exact ordered XML semantics and file inventory; only serialization whitespace normalizes."""
    return _sha(
        _canonical(
            {path: _xml_identity(_parse_xml(content)) for path, content in sorted(files.items())}
        )
    )


def edit_single_property(content: bytes, intent: MetadataPropertyIntent) -> bytes:
    return edit_compiled_property(content, intent.drift)


def edit_compiled_property(content: bytes, drift: BrowserMetadataDrift) -> bytes:
    """SET one exact component property; original bytes outside the property are preserved."""
    drift = BrowserMetadataDrift.model_validate_json(drift.model_dump_json(warnings=False))
    root = _parse_xml(content)
    ns = f"{{{_NS}}}"
    if root.tag != ns + drift.metadata_type or len(content) > drift.maximum_bytes:
        raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)

    def scalar(node, name):
        values = node.findall(ns + name)
        if len(values) != 1 or list(values[0]) or values[0].attrib:
            raise MetadataRecoveryError(MetadataRecoveryCode.PROPERTY_UNSUPPORTED)
        return values[0]

    matches = [
        node
        for node in root.findall(f"{ns}flexiPageRegions/{ns}itemInstances/{ns}componentInstance")
        if scalar(node, "componentName").text == drift.component_name
        and scalar(node, "identifier").text == drift.component_identifier
    ]
    if len(matches) != 1:
        raise MetadataRecoveryError(MetadataRecoveryCode.PROPERTY_UNSUPPORTED)
    component = matches[0]
    properties = [
        node
        for node in component.findall(ns + "componentInstanceProperties")
        if scalar(node, "name").text == drift.property_name
    ]
    if len(properties) > 1:
        raise MetadataRecoveryError(MetadataRecoveryCode.PROPERTY_UNSUPPORTED)
    states = {value.state for value in drift.allowed_pre_states}
    selected = scalar(properties[0], "value") if properties else None
    if (selected is None and "ABSENT" not in states) or (
        selected is not None and ("PRESENT" not in states or selected.text != drift.baseline_value)
    ):
        raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
    anchor = selected if selected is not None else scalar(component, "componentName")
    ordinal = list(root.iter()).index(anchor)
    spans: list[list[int]] = []
    stack: list[int] = []
    parser = expat.ParserCreate()

    def start(_name, _attributes):
        stack.append(len(spans))
        spans.append([parser.CurrentByteIndex, -1])

    def end(_name):
        spans[stack.pop()][1] = parser.CurrentByteIndex

    parser.StartElementHandler, parser.EndElementHandler = start, end
    try:
        parser.Parse(content, True)
        beginning, ending = spans[ordinal]
        if selected is None:
            element = ET.Element(ns + "componentInstanceProperties")
            ET.SubElement(element, ns + "name").text = drift.property_name
            ET.SubElement(element, ns + "value").text = drift.alternate_value
            fragment = ET.tostring(element, encoding="utf-8")
            # Insert before componentName, preserving Metadata API child ordering and all old bytes.
            result = content[:beginning] + fragment + content[beginning:]
            if len(result) > drift.maximum_bytes:
                raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
            # Independently prove the whole semantic delta is precisely this inserted child.
            component.insert(list(component).index(anchor), element)
            if _xml_identity(_parse_xml(result)) != _xml_identity(root):
                raise ValueError("property insertion changed unrelated structure")
            return result
        beginning = content.index(b">", beginning) + 1
        if (
            content[beginning - 2 : beginning] == b"/>"
            or ending < beginning
            or not content[ending:].startswith(b"</")
            or b"<" in content[beginning:ending]
        ):
            raise ValueError("not a round-trippable scalar leaf")
        result = content[:beginning] + escape(drift.alternate_value).encode() + content[ending:]
        if len(result) > drift.maximum_bytes:
            raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
        # Validate the edited bytes again; XML escape rules must not create structure.
        edited = _parse_xml(result)
        leaf = list(edited.iter())[ordinal]
        if leaf.tag != selected.tag or (leaf.text or "") != drift.alternate_value:
            raise ValueError("property edit changed structure")
        selected.text = drift.alternate_value
        if _xml_identity(edited) != _xml_identity(root):
            raise ValueError("property replacement changed unrelated structure")
        return result
    except (ValueError, IndexError, expat.ExpatError):
        raise MetadataRecoveryError(MetadataRecoveryCode.PROPERTY_UNSUPPORTED) from None


def _read_metadata_archive(content: bytes, expected: set[str], maximum: int) -> dict[str, bytes]:
    """Never let CLI unzip untrusted member paths onto the host filesystem."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 16:
                raise ValueError("archive entry bound")
            files: dict[str, bytes] = {}
            seen: set[str] = set()
            total = 0
            for item in members:
                name = item.filename.rstrip("/")
                mode = item.external_attr >> 16
                if (
                    not _safe_metadata_path(name)
                    or name in seen
                    or item.flag_bits & 1
                    or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or (stat.S_ISDIR(mode) and not item.is_dir())
                ):
                    raise ValueError("unsafe archive member")
                seen.add(name)
                if item.is_dir():
                    if not any(path.startswith(name + "/") for path in expected):
                        raise ValueError("unrelated archive directory")
                    continue
                if name not in expected or item.file_size > maximum:
                    raise ValueError("unrelated or oversized archive member")
                total += item.file_size
                if total > maximum:
                    raise ValueError("archive output bound")
                with archive.open(item) as stream:
                    value = stream.read(item.file_size + 1)
                if len(value) != item.file_size:
                    raise ValueError("archive size mismatch")
                files[name] = value
            if set(files) != expected:
                raise ValueError("archive omitted exact metadata member")
            return files
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError):
        raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID) from None


def _write_exclusive(path: Path, content: bytes) -> None:
    guards = _DirectoryGuards(path.parent)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if _read_nofollow(path, maximum_bytes=len(content)) != content:
            raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
    finally:
        guards.close()


class _Journal:
    def __init__(self, path: Path, authentication_key: bytes):
        self.path = path
        self.key = authentication_key
        self.previous: str | None = None
        self.sequence = 0
        self.operations: list[str] = []
        self.payloads: list[dict] = []

    def event(self, operation: str, now: datetime, **roots) -> str:
        body = {
            "schema_version": "1.0.0",
            "sequence": self.sequence,
            "previous_sha256": self.previous,
            "operation": operation,
            "observed_at": aware_utc(now).isoformat(),
            **roots,
        }
        payload = _canonical(
            {
                "payload": body,
                "signature_sha256": hmac.new(
                    self.key, _canonical(body), hashlib.sha256
                ).hexdigest(),
            }
        )
        _write_exclusive(self.path / f"event-{self.sequence:04d}.json", payload)
        self.previous = _sha(payload)
        self.sequence += 1
        self.operations.append(operation)
        self.payloads.append(body)
        return self.previous

    def replay(self, intent_root: str, lease_root: str) -> None:
        entries = sorted(self.path.glob("event-*.json"))
        if not entries or len(entries) > 200:
            raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
        for index, entry in enumerate(entries):
            if entry.name != f"event-{index:04d}.json":
                raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
            content = _read_nofollow(entry, maximum_bytes=8192)
            value = json.loads(content)
            body = value["payload"]
            if (
                content != _canonical(value)
                or set(value) != {"payload", "signature_sha256"}
                or body["sequence"] != index
                or body["previous_sha256"] != self.previous
                or not hmac.compare_digest(
                    value["signature_sha256"],
                    hmac.new(self.key, _canonical(body), hashlib.sha256).hexdigest(),
                )
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
            if index == 0 and (
                body.get("operation") != "CLAIMED"
                or body.get("intent_sha256") != intent_root
                or body.get("lease_sha256") != lease_root
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
            self.previous, self.sequence = _sha(content), index + 1
            self.operations.append(body["operation"])
            self.payloads.append(body)

    def private_job(self, operation: str) -> str | None:
        records = [value for value in self.payloads if value["operation"] == operation + "_JOB"]
        if not records:
            return None
        if len(records) != 1:
            raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
        content = _read_nofollow(
            self.path / (operation.lower() + "-job.private.json"), maximum_bytes=100
        )
        value = json.loads(content)
        job = value.get("id")
        if (
            set(value) != {"id"}
            or not isinstance(job, str)
            or not _JOB.fullmatch(job)
            or content != _canonical(value)
            or _sha(job.encode()) != records[0]["job_sha256"]
        ):
            raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
        return job

    def package(self, name: str, files: dict[str, bytes]) -> Path:
        directory = self.path / name
        directory.mkdir(mode=0o700)
        for locator, content in sorted(files.items()):
            if not _safe_metadata_path(locator) or not locator.startswith("unpackaged/"):
                raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
            destination = directory / locator
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _write_exclusive(destination, content)
        return directory


class _ExecutionLock:
    """Kernel-owned process lock: auto-released after crash, distinct from durable quarantine."""

    def __init__(self, path: Path):
        self.descriptor: int | None = None
        try:
            if not os.path.lexists(path):
                with suppress(FileExistsError):
                    _write_exclusive(path, b"1")
            before = _checked_identity(path, directory=False)
            descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            self.descriptor = descriptor
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != before[:2]:
                raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise MetadataRecoveryError(MetadataRecoveryCode.TRANSACTION_BUSY) from None

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


class HostMetadataRecoveryAdapter:
    """Concrete CLI/filesystem transaction, requiring separately implemented host authority.

    The host-only callback is the candidate-phase assertion window. Finally restores after
    every actual deployment dispatch, including callback failures and partial CLI outcomes.
    This callback is not accepted from API/MCP input and cannot authorize another mutation.
    """

    def __init__(
        self,
        repository_root: Path,
        config: MetadataRecoveryConfig,
        authority: MetadataRecoveryAuthority,
        *,
        runner: CliRunner | None = None,
        journal_authentication_key: bytes,
        now: Callable[[], datetime] | None = None,
    ):
        self.repository_root = repository_root.absolute()
        self.config = MetadataRecoveryConfig.model_validate_json(
            config.model_dump_json(warnings=False)
        )
        self.authority = authority
        self.runner = runner or SubprocessCliRunner()
        if (
            not isinstance(journal_authentication_key, bytes)
            or len(journal_authentication_key) < 32
        ):
            raise ValueError("A private host journal authentication key is required")
        self.journal_key = journal_authentication_key
        clock = now or (lambda: datetime.now(UTC))
        self.now = lambda: aware_utc(clock())

    def run(
        self,
        intent: MetadataPropertyIntent,
        lease: MetadataRecoveryLease,
        *,
        candidate_window: Callable[[str], None] | None = None,
        _recovery_only: bool = False,
    ) -> MetadataRecoveryReport:
        intent = MetadataPropertyIntent.model_validate_json(intent.model_dump_json(warnings=False))
        lease = MetadataRecoveryLease.model_validate_json(lease.model_dump_json(warnings=False))
        gaps: list[MetadataRecoveryCode] = []
        journal = None
        preimage = candidate = None
        preimage_dir = candidate_dir = None
        deployment_dispatched = False
        dispatch_fenced = False
        process_quiescent = True
        forward_job: str | None = None
        forward_terminal = False
        candidate_reconciled = restored = False
        guards = None
        execution_lock = None
        org_claim: Path | None = None
        org_claim_content: bytes | None = None
        status = "NOT_RUN"

        def authorize(operation: str, package_root: str | None, *, recovery=False):
            deadline = lease.recovery_deadline if recovery else lease.expires_at
            if not lease.issued_at <= self.now() < deadline or lease.intent_sha256 != _root(intent):
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            try:
                binding = metadata_execution_binding(self.config, intent.api_version)
                if _root(binding) != lease.execution_binding_sha256:
                    raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
                self.authority.verify(intent, lease, binding, operation, package_root, self.now())
                if (
                    _root(metadata_execution_binding(self.config, intent.api_version))
                    != lease.execution_binding_sha256
                ):
                    raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            except Exception:
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED) from None
            if self.now() >= deadline:
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)

        def invoke(
            arguments: tuple[str, ...],
            operation: str,
            *,
            recovery=False,
            package_root: str | None = None,
            before_dispatch: Callable[[], None] | None = None,
            dispatch_started: Callable[[], None] | None = None,
            effect_started: Callable[[], None] | None = None,
        ):
            nonlocal process_quiescent
            if not process_quiescent:
                raise MetadataRecoveryError(MetadataRecoveryCode.PROCESS_NOT_QUIESCENT)
            if recovery:
                try:
                    self.authority.require_recovery_quiescence(intent, lease)
                except Exception:
                    process_quiescent = False
                    raise MetadataRecoveryError(
                        MetadataRecoveryCode.PROCESS_NOT_QUIESCENT
                    ) from None
            if (
                arguments.count("--target-org") != 1
                or arguments[arguments.index("--target-org") + 1] != self.config.alias
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            deadline = lease.recovery_deadline if recovery else lease.expires_at
            authorize(operation, package_root, recovery=recovery)
            timeout = min(
                self.config.operation_timeout_seconds, (deadline - self.now()).total_seconds()
            )
            if timeout <= 0:
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            if before_dispatch is not None:
                before_dispatch()
                # Disk journaling or authority refresh may consume the original time budget.
                authorize(operation, package_root, recovery=recovery)
                timeout = min(
                    self.config.operation_timeout_seconds, (deadline - self.now()).total_seconds()
                )
                if timeout <= 0:
                    raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            if dispatch_started is not None:
                dispatch_started()
            # The durable fence can consume the remaining lease. A claim is not an effect.
            authorize(operation, package_root, recovery=recovery)
            process_quiescent = False
            self.authority.note_process_state(intent, lease, operation, None)
            # No external authority/store call may follow this final actual-transport check.
            if (
                _root(metadata_execution_binding(self.config, intent.api_version))
                != lease.execution_binding_sha256
                or arguments[arguments.index("--target-org") + 1] != self.config.alias
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            timeout = min(
                self.config.operation_timeout_seconds, (deadline - self.now()).total_seconds()
            )
            if timeout <= 0:
                raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
            if effect_started is not None:
                effect_started()
            result = self.runner.run(CliInvocation(arguments, timeout, self.config.maximum_bytes))
            self.authority.note_process_state(intent, lease, operation, result.quiescent)
            process_quiescent = result.quiescent
            if not result.quiescent:
                process_quiescent = False
                raise MetadataRecoveryError(MetadataRecoveryCode.PROCESS_NOT_QUIESCENT)
            if (
                result.timed_out
                or result.output_exceeded
                or len(result.stdout) > self.config.maximum_bytes
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.CLI_FAILED)
            try:

                def unique_pairs(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError("duplicate response key")
                        value[key] = item
                    return value

                payload = json.loads(result.stdout, object_pairs_hook=unique_pairs)
                if (
                    not isinstance(payload, dict)
                    or type(payload.get("status")) is not int
                    or not isinstance(payload.get("result"), dict)
                ):
                    raise ValueError("invalid CLI envelope")
                # Terminal failure report bodies are needed for safe compensation; never logged.
                if result.returncode != payload["status"] or payload["status"] not in {0, 1}:
                    raise ValueError("CLI status mismatch")
                return payload
            except (ValueError, TypeError):
                raise MetadataRecoveryError(MetadataRecoveryCode.CLI_FAILED) from None

        def retrieve(label: str, *, recovery=False):
            directory = journal.path / ("read-" + uuid4().hex)
            directory.mkdir(mode=0o700)
            response = invoke(
                (
                    "project",
                    "retrieve",
                    "start",
                    "--metadata",
                    f"{intent.metadata_type}:{intent.member}",
                    "--target-metadata-dir",
                    str(directory),
                    "--zip-file-name",
                    "capture.zip",
                    "--wait",
                    "1",
                    "--api-version",
                    intent.api_version,
                    "--target-org",
                    self.config.alias,
                    "--json",
                ),
                "READ_PREIMAGE" if not recovery else "READ_RECOVERY",
                recovery=recovery,
            )
            paths = _metadata_expected_paths(response, intent.metadata_type, intent.member)
            expected = {"unpackaged/package.xml", intent.relative_file}
            if paths != expected:
                raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
            _scan_stage(
                directory,
                maximum_files=1,
                maximum_bytes=self.config.maximum_bytes,
                allowed_paths={"capture.zip"},
            )
            archive = _read_nofollow(
                directory / "capture.zip", maximum_bytes=self.config.maximum_bytes
            )
            files = _read_metadata_archive(archive, expected, self.config.maximum_bytes)
            if len(files[intent.relative_file]) > intent.drift.maximum_bytes:
                raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
            entries = [
                {
                    "relative_path": locator,
                    "size": len(content),
                    "content_sha256": _sha(content),
                    **(
                        {"manifest": _metadata_manifest(content)}
                        if locator == "unpackaged/package.xml"
                        else {}
                    ),
                }
                for locator, content in sorted(files.items())
            ]
            _validate_metadata_completion(response, intent.metadata_type, intent.member, entries)
            package = _parse_xml(files["unpackaged/package.xml"])
            versions = package.findall(f"{{{_NS}}}version")
            if len(versions) != 1 or versions[0].text != intent.api_version:
                raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
            journal.event(
                label.upper(),
                self.now(),
                state_sha256=metadata_state_sha256(files),
                archive_sha256=_sha(archive),
            )
            return files

        def package_check(directory: Path, files: dict[str, bytes]):
            if len(files[intent.relative_file]) > intent.drift.maximum_bytes:
                raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
            _scan_stage(
                directory,
                maximum_files=2,
                maximum_bytes=self.config.maximum_bytes,
                allowed_paths=set(files),
            )
            if any(
                _read_nofollow(directory / name, maximum_bytes=self.config.maximum_bytes) != content
                for name, content in files.items()
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)

        def package_read(directory: Path):
            expected = {"unpackaged/package.xml", intent.relative_file}
            _scan_stage(
                directory,
                maximum_files=2,
                maximum_bytes=self.config.maximum_bytes,
                allowed_paths=expected,
            )
            files = {
                locator: _read_nofollow(
                    directory / locator, maximum_bytes=self.config.maximum_bytes
                )
                for locator in expected
            }
            if len(files[intent.relative_file]) > intent.drift.maximum_bytes:
                raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
            return files

        def job_report(job: str, *, recovery=False):
            payload = invoke(
                (
                    "project",
                    "deploy",
                    "report",
                    "--job-id",
                    job,
                    "--wait",
                    "1",
                    "--target-org",
                    self.config.alias,
                    "--json",
                ),
                "REPORT_JOB",
                recovery=recovery,
            )
            value = payload["result"]
            if value.get("id") != job or type(value.get("done")) is not bool:
                raise MetadataRecoveryError(MetadataRecoveryCode.JOB_UNKNOWN)
            return value

        def settle(job: str, *, recovery=False):
            result = job_report(job, recovery=recovery)
            if result["done"] is not True or result.get("status") not in {
                "Succeeded",
                "Failed",
                "Canceled",
            }:
                raise MetadataRecoveryError(MetadataRecoveryCode.JOB_UNKNOWN)
            return result

        def validate_completion(value, *, check_only: bool):
            count_fields = (
                "numberComponentErrors",
                "numberComponentsDeployed",
                "numberComponentsTotal",
                "numberTestErrors",
                "numberTestsCompleted",
                "numberTestsTotal",
            )
            if (
                any(type(value.get(field)) is not int for field in count_fields)
                or value.get("checkOnly") is not check_only
                or value.get("done") is not True
                or value.get("success") is not True
                or value.get("status") != "Succeeded"
                or value.get("rollbackOnError") is not True
                or value.get("numberComponentErrors") != 0
                or value.get("numberComponentsDeployed") != 1
                or value.get("numberComponentsTotal") != 1
                or value.get("numberTestErrors") != 0
                or value.get("numberTestsCompleted") != intent.expected_test_count
                or value.get("numberTestsTotal") != intent.expected_test_count
            ):
                raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)
            details = value.get("details")
            if not isinstance(details, dict) or details.get("componentFailures"):
                raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)
            successes = details.get("componentSuccesses")
            if isinstance(successes, dict):
                successes = [successes]
            if not isinstance(successes, list):
                raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)
            identities = []
            package_count = 0
            for item in successes:
                if not isinstance(item, dict) or item.get("success") is not True:
                    raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)
                identity = (item.get("componentType"), item.get("fullName"))
                if identity == ("", "package.xml"):
                    package_count += 1
                    continue
                if item.get("created") is not False or item.get("deleted") is not False:
                    raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)
                identities.append(identity)
            if package_count > 1 or identities != [(intent.metadata_type, intent.member)]:
                raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)
            if intent.tests:
                tests = details.get("runTestResult")
                successes = tests.get("successes") if isinstance(tests, dict) else None
                if (
                    not isinstance(successes, list)
                    or tests.get("failures")
                    or any(not isinstance(item, dict) for item in successes)
                    or tuple(
                        sorted(
                            f"{item.get('name')}.{item.get('methodName')}"
                            for item in successes
                            if isinstance(item, dict)
                        )
                    )
                    != intent.tests
                ):
                    raise MetadataRecoveryError(MetadataRecoveryCode.CHECK_ONLY_FAILED)

        def deploy(directory: Path, files: dict[str, bytes], *, check_only=False, recovery=False):
            nonlocal deployment_dispatched, forward_job, forward_terminal
            operation = "CHECK_ONLY" if check_only else "RESTORE" if recovery else "DEPLOY"
            package_check(directory, files)
            package_root = metadata_state_sha256(files)
            authorize(operation, package_root, recovery=recovery)
            journal.event(operation + "_INTENT", self.now(), package_sha256=package_root)
            arguments = (
                "project",
                "deploy",
                "start",
                "--metadata-dir",
                str(directory / "unpackaged"),
                "--single-package",
                "--async",
                "--api-version",
                intent.api_version,
                "--test-level",
                intent.test_level,
                *(flag for test in intent.test_classes for flag in ("--tests", test)),
                *(("--dry-run",) if check_only else ()),
                "--target-org",
                self.config.alias,
                "--json",
            )

            # invoke performs a second authority check; mark dispatch only at the runner boundary.
            def mark_dispatch():
                package_check(directory, files)
                journal.event(operation + "_DISPATCHED", self.now(), package_sha256=package_root)

            def activate_dispatch():
                nonlocal dispatch_fenced
                fence = self.authority.claim_dispatch_once(
                    intent, lease, operation, package_root, self.now()
                )
                if not isinstance(fence, str) or not re.fullmatch(r"[a-f0-9]{64}", fence):
                    raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
                dispatch_fenced = True
                journal.event(operation + "_FENCE", self.now(), fence_sha256=fence)

            def activate_effect():
                nonlocal deployment_dispatched, dispatch_fenced
                dispatch_fenced = False
                if not check_only and not recovery:
                    deployment_dispatched = True

            file_handles = []
            nested_guards = []
            package_guards = _DirectoryGuards(directory)
            try:
                if os.name == "nt":
                    for locator in sorted(files):
                        nested_guards.append(_DirectoryGuards((directory / locator).parent))
                        # Native handles deny write/delete sharing until CLI dispatch finishes.
                        file_handles.append(
                            _windows_open_no_follow(directory / locator, directory=False)
                        )
                package_check(directory, files)
                payload = invoke(
                    arguments,
                    operation,
                    recovery=recovery,
                    package_root=package_root,
                    before_dispatch=mark_dispatch,
                    dispatch_started=activate_dispatch,
                    effect_started=activate_effect,
                )
            finally:
                for handle, kernel in reversed(file_handles):
                    kernel.CloseHandle(handle)
                for item in reversed(nested_guards):
                    item.close()
                package_guards.close()
            value = payload["result"]
            job = value.get("id")
            if not isinstance(job, str) or not _JOB.fullmatch(job):
                raise MetadataRecoveryError(MetadataRecoveryCode.JOB_UNKNOWN)
            _write_exclusive(
                journal.path / (operation.lower() + "-job.private.json"), _canonical({"id": job})
            )
            if not check_only and not recovery:
                forward_job = job
            journal.event(operation + "_JOB", self.now(), job_sha256=_sha(job.encode()))
            result = settle(job, recovery=recovery)
            if not check_only and not recovery:
                forward_terminal = True
            validate_completion(result, check_only=check_only)
            package_check(directory, files)
            journal.event(
                operation + "_VERIFIED", self.now(), response_sha256=_sha(_canonical(result))
            )

        if not self.config.mutation_enabled:
            gaps.append(MetadataRecoveryCode.DISABLED)
        else:
            try:
                if os.name != "nt" and isinstance(self.runner, SubprocessCliRunner):
                    # The declared runtime is Windows. Do not call advisory POSIX locks equivalent.
                    raise MetadataRecoveryError(MetadataRecoveryCode.IMMUTABLE_STAGING_UNAVAILABLE)
                if lease.recovery_deadline - lease.expires_at < timedelta(
                    seconds=10 * self.config.operation_timeout_seconds
                ):
                    raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
                authorize(
                    "PREPARE_RECOVERY" if _recovery_only else "PREPARE",
                    None,
                    recovery=_recovery_only,
                )
                root = self.repository_root / self.config.journal_root
                _prepare_staging_root(self.repository_root, root)
                guards = _DirectoryGuards(root)
                execution_lock = _ExecutionLock(
                    root / ("execution-" + lease.org_fingerprint_sha256 + ".lock")
                )
                org_claim = root / ("active-" + lease.org_fingerprint_sha256 + ".json")
                claim_body = {"intent_sha256": _root(intent), "lease_sha256": _root(lease)}
                org_claim_content = _canonical(
                    {
                        "payload": claim_body,
                        "signature_sha256": hmac.new(
                            self.journal_key, _canonical(claim_body), hashlib.sha256
                        ).hexdigest(),
                    }
                )
                if _recovery_only:
                    if _read_nofollow(org_claim, maximum_bytes=1024) != org_claim_content:
                        raise MetadataRecoveryError(MetadataRecoveryCode.JOURNAL_REPLAY)
                else:
                    try:
                        _write_exclusive(org_claim, org_claim_content)
                    except FileExistsError:
                        org_claim = None  # Never release another transaction's claim.
                        raise MetadataRecoveryError(MetadataRecoveryCode.JOURNAL_REPLAY) from None
                directory = root / _root(intent)
                if not _recovery_only:
                    try:
                        directory.mkdir(mode=0o700)
                    except FileExistsError:
                        raise MetadataRecoveryError(MetadataRecoveryCode.JOURNAL_REPLAY) from None
                _checked_identity(directory, directory=True)
                journal = _Journal(directory, self.journal_key)
                if _recovery_only:
                    journal.replay(_root(intent), _root(lease))
                    if any(
                        MetadataRecoveryCode.PROCESS_NOT_QUIESCENT in value.get("gap_codes", [])
                        for value in journal.payloads
                    ):
                        raise MetadataRecoveryError(MetadataRecoveryCode.PROCESS_NOT_QUIESCENT)
                    preimage_dir, candidate_dir = directory / "preimage", directory / "candidate"
                    preimage, candidate = package_read(preimage_dir), package_read(candidate_dir)
                    if (
                        metadata_state_sha256(preimage) != intent.expected_preimage_sha256
                        or metadata_state_sha256(candidate) != intent.expected_candidate_sha256
                    ):
                        raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
                    durable = [
                        value
                        for value in journal.payloads
                        if value["operation"] == "PREIMAGE_DURABLE"
                    ]
                    if len(durable) != 1 or durable[0]["exact_preimage_bytes_sha256"] != _sha(
                        _canonical({path: _sha(value) for path, value in preimage.items()})
                    ):
                        raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
                    deployment_dispatched = (
                        self.authority.read_dispatch_fence(intent, lease, "DEPLOY") is not None
                    )
                    if "DEPLOY_DISPATCHED" in journal.operations and not deployment_dispatched:
                        raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
                    try:
                        self.authority.require_recovery_quiescence(intent, lease)
                    except Exception:
                        process_quiescent = False
                        raise MetadataRecoveryError(
                            MetadataRecoveryCode.PROCESS_NOT_QUIESCENT
                        ) from None
                    forward_job = journal.private_job("DEPLOY")
                    forward_terminal = "DEPLOY_VERIFIED" in journal.operations
                else:
                    journal.event(
                        "CLAIMED",
                        self.now(),
                        intent_sha256=_root(intent),
                        lease_sha256=_root(lease),
                    )
                    preimage = retrieve("preimage-retrieve")
                    if metadata_state_sha256(preimage) != intent.expected_preimage_sha256:
                        raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
                    candidate = dict(preimage)
                    candidate[intent.relative_file] = edit_single_property(
                        preimage[intent.relative_file], intent
                    )
                    if metadata_state_sha256(candidate) != intent.expected_candidate_sha256:
                        raise MetadataRecoveryError(MetadataRecoveryCode.SOURCE_BINDING_INVALID)
                    preimage_dir = journal.package("preimage", preimage)
                    candidate_dir = journal.package("candidate", candidate)
                    journal.event(
                        "PREIMAGE_DURABLE",
                        self.now(),
                        preimage_sha256=metadata_state_sha256(preimage),
                        candidate_sha256=metadata_state_sha256(candidate),
                        exact_preimage_bytes_sha256=_sha(
                            _canonical({path: _sha(value) for path, value in preimage.items()})
                        ),
                    )
                    deploy(candidate_dir, candidate, check_only=True)
                    current = retrieve("before-deploy")
                    if metadata_state_sha256(current) != metadata_state_sha256(preimage):
                        raise MetadataRecoveryError(MetadataRecoveryCode.CONCURRENT_CHANGE)
                    deploy(candidate_dir, candidate)
                    current = retrieve("candidate-reconcile")
                    if metadata_state_sha256(current) != metadata_state_sha256(candidate):
                        raise MetadataRecoveryError(MetadataRecoveryCode.CANDIDATE_MISMATCH)
                    candidate_reconciled = True
                    if candidate_window is not None:
                        try:
                            candidate_window(metadata_state_sha256(current))
                        except Exception:
                            raise MetadataRecoveryError(
                                MetadataRecoveryCode.CANDIDATE_WINDOW_FAILED
                            ) from None
            except MetadataRecoveryError as error:
                gaps.append(error.code)
            except Exception:
                gaps.append(MetadataRecoveryCode.STAGING_INVALID)
            finally:
                if deployment_dispatched:
                    try:
                        if not process_quiescent:
                            raise MetadataRecoveryError(MetadataRecoveryCode.PROCESS_NOT_QUIESCENT)
                        try:
                            self.authority.require_recovery_quiescence(intent, lease)
                        except Exception:
                            raise MetadataRecoveryError(
                                MetadataRecoveryCode.PROCESS_NOT_QUIESCENT
                            ) from None
                        if not forward_terminal and forward_job is None:
                            raise MetadataRecoveryError(MetadataRecoveryCode.JOB_UNKNOWN)
                        retrieve("recovery-readback", recovery=True)
                        if not forward_terminal:
                            if forward_job is None:
                                raise MetadataRecoveryError(MetadataRecoveryCode.JOB_UNKNOWN)
                            try:
                                settle(forward_job, recovery=True)
                            except MetadataRecoveryError:
                                invoke(
                                    (
                                        "project",
                                        "deploy",
                                        "cancel",
                                        "--job-id",
                                        forward_job,
                                        "--target-org",
                                        self.config.alias,
                                        "--json",
                                    ),
                                    "CANCEL_FORWARD_JOB",
                                    recovery=True,
                                )
                                settle(forward_job, recovery=True)
                        current = retrieve("before-restore", recovery=True)
                        if metadata_state_sha256(current) not in {
                            metadata_state_sha256(preimage),
                            metadata_state_sha256(candidate),
                        }:
                            raise MetadataRecoveryError(MetadataRecoveryCode.CONCURRENT_CHANGE)
                        # Independent fences prevent journal prefix rollback from creating a retry.
                        restore_fence = self.authority.read_dispatch_fence(intent, lease, "RESTORE")
                        if "RESTORE_DISPATCHED" in journal.operations and restore_fence is None:
                            raise MetadataRecoveryError(MetadataRecoveryCode.AUTHORITY_REJECTED)
                        if restore_fence is not None:
                            restore_job = journal.private_job("RESTORE")
                            if restore_job is None:
                                raise MetadataRecoveryError(MetadataRecoveryCode.JOB_UNKNOWN)
                            validate_completion(
                                settle(restore_job, recovery=True), check_only=False
                            )
                        else:
                            deploy(preimage_dir, preimage, recovery=True)
                        final = retrieve("restore-reconcile", recovery=True)
                        restored = metadata_state_sha256(final) == metadata_state_sha256(preimage)
                        if not restored:
                            raise MetadataRecoveryError(MetadataRecoveryCode.RESTORE_UNRESOLVED)
                        journal.event(
                            "RESTORED_EXACTLY",
                            self.now(),
                            state_sha256=metadata_state_sha256(final),
                        )
                    except MetadataRecoveryError as error:
                        gaps.append(error.code)
                        gaps.append(MetadataRecoveryCode.RESTORE_UNRESOLVED)
                    except Exception:
                        gaps.append(MetadataRecoveryCode.RESTORE_UNRESOLVED)
                    status = (
                        "RESTORED"
                        if restored and not gaps
                        else "FAILED_RESTORED"
                        if restored
                        else "QUARANTINED"
                    )
                else:
                    status = (
                        "QUARANTINED"
                        if _recovery_only or not process_quiescent or dispatch_fenced
                        else "FAILED_BEFORE_DEPLOY"
                    )
                if journal is not None:
                    try:
                        journal.event(status, self.now(), gap_codes=sorted(set(gaps)))
                    except Exception:
                        gaps.append(MetadataRecoveryCode.STAGING_INVALID)
                        status = "QUARANTINED"
                if org_claim is not None and status != "QUARANTINED":
                    try:
                        if _read_nofollow(org_claim, maximum_bytes=1024) != org_claim_content:
                            raise MetadataRecoveryError(MetadataRecoveryCode.STAGING_INVALID)
                        # Preserve the authenticated claim; never delete or overwrite it.
                        org_claim.rename(org_claim.parent / ("released-" + uuid4().hex + ".json"))
                    except Exception:
                        gaps.append(MetadataRecoveryCode.STAGING_INVALID)
                        status = "QUARANTINED"
                if guards is not None:
                    guards.close()
                if execution_lock is not None:
                    execution_lock.close()
        return MetadataRecoveryReport(
            intent_sha256=_root(intent),
            journal_sha256=journal.previous if journal else None,
            status=status,
            candidate_reconciled=candidate_reconciled,
            restored_exactly=restored,
            zero_scoped_residue=restored,
            gap_codes=tuple(sorted(set(gaps))),
        )

    def recover(
        self, intent: MetadataPropertyIntent, lease: MetadataRecoveryLease
    ) -> MetadataRecoveryReport:
        """Host-only restart recovery: authenticate journal/preimage, never retry a deploy."""
        return self.run(intent, lease, _recovery_only=True)
