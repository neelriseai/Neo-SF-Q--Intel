from __future__ import annotations

import fnmatch
import hashlib
import inspect
import json
import re
import stat
import subprocess
import threading
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.ontology import contract_sha256


class ChangeVerificationContractError(RuntimeError):
    """The independently pinned change-capture contract is unavailable."""


class _CaptureRejected(RuntimeError):
    def __init__(self, code: ChangeVerificationGapCode, *identity: Any) -> None:
        super().__init__(code.value)
        self.code = code
        self.identity = identity


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ProducerPin(_Model):
    producer_id: str = Field(alias="producerId", min_length=1, max_length=200)
    producer_version: str = Field(alias="producerVersion", min_length=1, max_length=40)
    implementation_locator: str = Field(alias="implementationLocator", min_length=1, max_length=500)
    implementation_sha256: str = Field(alias="implementationSha256", pattern=r"^[a-f0-9]{64}$")


class VerifiedChangePolicy(_Model):
    schema_version: str = Field(alias="schemaVersion")
    policy_id: str = Field(alias="policyId", min_length=1, max_length=200)
    policy_version: str = Field(alias="policyVersion")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_scope: Literal["FULL_TRACKED_AND_NONIGNORED_UNTRACKED_TREE"] = Field(
        alias="captureScope"
    )
    base_revision: Literal["HEAD"] = Field(alias="baseRevision")
    rename_detection: Literal["DISABLED_DELETE_ADD"] = Field(alias="renameDetection")
    producer: ProducerPin
    allowed_regular_modes: tuple[Literal["100644", "100755"], ...] = Field(
        alias="allowedRegularModes", min_length=2, max_length=2
    )
    sensitive_path_globs: tuple[str, ...] = Field(alias="sensitivePathGlobs", min_length=1)
    git_timeout_seconds: int = Field(alias="gitTimeoutSeconds", ge=1, le=120)
    maximum_git_output_bytes: int = Field(alias="maximumGitOutputBytes", ge=1024)
    maximum_files: int = Field(alias="maximumFiles", ge=1)
    maximum_changes: int = Field(alias="maximumChanges", ge=1)
    maximum_file_bytes: int = Field(alias="maximumFileBytes", ge=1)
    maximum_total_bytes: int = Field(alias="maximumTotalBytes", ge=1)
    maximum_manifest_bytes: int = Field(alias="maximumManifestBytes", ge=1024)
    maximum_path_bytes: int = Field(alias="maximumPathBytes", ge=32, le=32768)
    freshness_seconds: int = Field(alias="freshnessSeconds", ge=1, le=86400)

    @model_validator(mode="after")
    def validate_policy(self) -> VerifiedChangePolicy:
        _require_semver(self.schema_version)
        _require_semver(self.policy_version)
        _require_semver(self.producer.producer_version)
        if self.allowed_regular_modes != ("100644", "100755"):
            raise ValueError("Regular modes must be complete and canonical")
        if self.sensitive_path_globs != tuple(sorted(set(self.sensitive_path_globs))):
            raise ValueError("Sensitive path globs must be sorted and unique")
        if self.maximum_git_output_bytes < self.maximum_file_bytes:
            raise ValueError("Git output capacity must cover one maximum-sized blob")
        _require_safe_locator(self.producer.implementation_locator, self.maximum_path_bytes)
        return self


class GitFileEntry(_Model):
    path: str = Field(min_length=1, max_length=4096)
    mode: Literal["100644", "100755"]
    tracking: Literal["TRACKED", "UNTRACKED"]
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ChangeOperation(StrEnum):
    ADD = "ADD"
    MODIFY = "MODIFY"
    DELETE = "DELETE"


class GitChangeEntry(_Model):
    operation: ChangeOperation
    path: str = Field(min_length=1, max_length=4096)
    before_mode: Literal["100644", "100755"] | None = None
    before_size_bytes: int | None = Field(default=None, ge=0)
    before_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    after_mode: Literal["100644", "100755"] | None = None
    after_size_bytes: int | None = Field(default=None, ge=0)
    after_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_sides(self) -> GitChangeEntry:
        before = (self.before_mode, self.before_size_bytes, self.before_sha256)
        after = (self.after_mode, self.after_size_bytes, self.after_sha256)
        before_present = tuple(item is not None for item in before)
        after_present = tuple(item is not None for item in after)
        if self.operation is ChangeOperation.ADD and (
            any(before_present) or not all(after_present)
        ):
            raise ValueError("ADD must bind only a complete after image")
        if self.operation is ChangeOperation.DELETE and (
            not all(before_present) or any(after_present)
        ):
            raise ValueError("DELETE must bind only a complete before image")
        if self.operation is ChangeOperation.MODIFY and (
            not all(before_present) or not all(after_present)
        ):
            raise ValueError("MODIFY must bind complete before and after images")
        if self.operation is ChangeOperation.MODIFY and before == after:
            raise ValueError("MODIFY must change bytes or mode")
        return self


def _tree_digest(files: tuple[GitFileEntry, ...]) -> str:
    return stable_sha256(
        [
            {
                "path": item.path,
                "mode": item.mode,
                "size_bytes": item.size_bytes,
                "content_sha256": item.content_sha256,
            }
            for item in files
        ]
    )


def _derive_change_entries(
    base_files: tuple[GitFileEntry, ...],
    candidate_files: tuple[GitFileEntry, ...],
    status_paths: tuple[str, ...],
) -> tuple[GitChangeEntry, ...]:
    before = {item.path: item for item in base_files}
    after = {item.path: item for item in candidate_files}
    changes: list[GitChangeEntry] = []
    for path in status_paths:
        old = before.get(path)
        new = after.get(path)
        if old is None and new is not None:
            changes.append(
                GitChangeEntry(
                    operation="ADD",
                    path=path,
                    after_mode=new.mode,
                    after_size_bytes=new.size_bytes,
                    after_sha256=new.content_sha256,
                )
            )
        elif old is not None and new is None:
            changes.append(
                GitChangeEntry(
                    operation="DELETE",
                    path=path,
                    before_mode=old.mode,
                    before_size_bytes=old.size_bytes,
                    before_sha256=old.content_sha256,
                )
            )
        elif (
            old is not None
            and new is not None
            and (
                old.mode,
                old.size_bytes,
                old.content_sha256,
            )
            != (new.mode, new.size_bytes, new.content_sha256)
        ):
            changes.append(
                GitChangeEntry(
                    operation="MODIFY",
                    path=path,
                    before_mode=old.mode,
                    before_size_bytes=old.size_bytes,
                    before_sha256=old.content_sha256,
                    after_mode=new.mode,
                    after_size_bytes=new.size_bytes,
                    after_sha256=new.content_sha256,
                )
            )
    return tuple(changes)


def _semantic_diff_paths(
    base_files: tuple[GitFileEntry, ...],
    candidate_files: tuple[GitFileEntry, ...],
) -> tuple[str, ...]:
    before = {item.path: (item.mode, item.size_bytes, item.content_sha256) for item in base_files}
    after = {
        item.path: (item.mode, item.size_bytes, item.content_sha256) for item in candidate_files
    }
    return tuple(
        path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)
    )


_PERMANENT_GAPS = (
    "CANDIDATE_BUILD_NOT_VERIFIED",
    "CHANGE_SEED_SCOPE_NOT_ATTESTED",
    "CONFLICT_SCOPE_NOT_ATTESTED",
    "DEPLOYMENT_NOT_ATTESTED",
    "GIT_COMMIT_SIGNATURE_NOT_ATTESTED",
    "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
    "RELEASE_EVIDENCE_MODEL_INCOMPLETE",
    "REPOSITORY_ORIGIN_NOT_ATTESTED",
    "RISK_FACTORS_NOT_ATTESTED",
    "TEST_EXECUTION_SCOPE_NOT_ATTESTED",
    "TEST_OBLIGATION_SCOPE_NOT_ATTESTED",
    "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED",
)


class VerifiedChangeSet(_Model):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    capture_scope: Literal["LOCAL_GIT_CANDIDATE_CAPTURE"] = "LOCAL_GIT_CANDIDATE_CAPTURE"
    change_capture_complete: Literal[True] = True
    candidate_build_verified: Literal[False] = False
    project_id: str = Field(min_length=1, max_length=200)
    repository_identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base_commit_oid: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    base_tree_oid: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    policy_id: str
    policy_version: str
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    producer_id: str
    producer_version: str
    producer_implementation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    git_version_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: str
    valid_until: str
    freshness_seconds: int = Field(ge=1)
    base_files: tuple[GitFileEntry, ...]
    candidate_files: tuple[GitFileEntry, ...]
    changes: tuple[GitChangeEntry, ...] = Field(min_length=1)
    base_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_input_tree_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_build_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    blocking_gap_codes: tuple[str, ...] = _PERMANENT_GAPS
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_manifest(self) -> VerifiedChangeSet:
        _require_logical_id(self.project_id, 200)
        observed = _parse_timestamp(self.observed_at)
        valid = _parse_timestamp(self.valid_until)
        if valid <= observed or int((valid - observed).total_seconds()) != self.freshness_seconds:
            raise ValueError("Capture freshness is inconsistent")
        for entries, label in (
            (self.base_files, "base files"),
            (self.candidate_files, "candidate files"),
            (self.changes, "changes"),
        ):
            paths = tuple(item.path for item in entries)
            if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
                raise ValueError(f"{label} must be sorted with unique paths")
            for path in paths:
                _require_safe_locator(path, 4096)
        semantic_paths = _semantic_diff_paths(self.base_files, self.candidate_files)
        if self.changes != _derive_change_entries(
            self.base_files, self.candidate_files, semantic_paths
        ):
            raise ValueError("Change delta differs from the complete bytewise manifests")
        if _tree_digest(self.base_files) != self.base_tree_sha256:
            raise ValueError("Base tree digest differs from its files")
        if _tree_digest(self.candidate_files) != self.candidate_input_tree_sha256:
            raise ValueError("Candidate tree digest differs from its files")
        expected_repository = _identity(
            self.project_id, self.base_commit_oid, self.base_tree_oid
        )
        if self.repository_identity_sha256 != expected_repository:
            raise ValueError("Repository identity differs from the captured base history")
        expected_snapshot = stable_sha256(
            {
                "base_commit_oid": self.base_commit_oid,
                "base_tree_oid": self.base_tree_oid,
                "candidate_input_tree_sha256": self.candidate_input_tree_sha256,
            }
        )
        if self.source_snapshot_sha256 != expected_snapshot:
            raise ValueError("Source snapshot differs from the captured candidate tree")
        build_body = {
            "project_id": self.project_id,
            "repository_identity_sha256": self.repository_identity_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_input_tree_sha256": self.candidate_input_tree_sha256,
            "policy_sha256": self.policy_sha256,
            "producer_implementation_sha256": self.producer_implementation_sha256,
        }
        if stable_sha256(build_body) != self.candidate_build_input_sha256:
            raise ValueError("Candidate build-input digest differs from its exact tree")
        if self.blocking_gap_codes != _PERMANENT_GAPS:
            raise ValueError("Permanent release gaps cannot be omitted")
        _verify_digest(self, "manifest_sha256")
        return self


class ChangeVerificationGapCode(StrEnum):
    CANDIDATE_BUILD_NOT_VERIFIED = "CANDIDATE_BUILD_NOT_VERIFIED"
    CHANGE_SEED_SCOPE_NOT_ATTESTED = "CHANGE_SEED_SCOPE_NOT_ATTESTED"
    CONFLICT_SCOPE_NOT_ATTESTED = "CONFLICT_SCOPE_NOT_ATTESTED"
    DEPLOYMENT_NOT_ATTESTED = "DEPLOYMENT_NOT_ATTESTED"
    GIT_COMMIT_SIGNATURE_NOT_ATTESTED = "GIT_COMMIT_SIGNATURE_NOT_ATTESTED"
    HUMAN_APPROVAL_SCOPE_NOT_ATTESTED = "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED"
    RELEASE_EVIDENCE_MODEL_INCOMPLETE = "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    REPOSITORY_ORIGIN_NOT_ATTESTED = "REPOSITORY_ORIGIN_NOT_ATTESTED"
    RISK_FACTORS_NOT_ATTESTED = "RISK_FACTORS_NOT_ATTESTED"
    TEST_EXECUTION_SCOPE_NOT_ATTESTED = "TEST_EXECUTION_SCOPE_NOT_ATTESTED"
    TEST_OBLIGATION_SCOPE_NOT_ATTESTED = "TEST_OBLIGATION_SCOPE_NOT_ATTESTED"
    UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED = "UPSTREAM_SOURCE_CAPTURE_NOT_ATTESTED"
    POLICY_ROOT_MISMATCH = "POLICY_ROOT_MISMATCH"
    PRODUCER_IMPLEMENTATION_MISMATCH = "PRODUCER_IMPLEMENTATION_MISMATCH"
    GIT_COMMAND_FAILED = "GIT_COMMAND_FAILED"
    GIT_OUTPUT_INVALID = "GIT_OUTPUT_INVALID"
    GIT_TIMEOUT = "GIT_TIMEOUT"
    REPOSITORY_ROOT_MISMATCH = "REPOSITORY_ROOT_MISMATCH"
    NO_BASE_COMMIT = "NO_BASE_COMMIT"
    STATUS_AMBIGUOUS = "STATUS_AMBIGUOUS"
    UNSAFE_PATH = "UNSAFE_PATH"
    SENSITIVE_PATH_REFUSED = "SENSITIVE_PATH_REFUSED"
    UNSUPPORTED_ENTRY_TYPE = "UNSUPPORTED_ENTRY_TYPE"
    UNSUPPORTED_INDEX_STATE = "UNSUPPORTED_INDEX_STATE"
    DUPLICATE_PATH = "DUPLICATE_PATH"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    CONCURRENT_MUTATION = "CONCURRENT_MUTATION"
    NO_CANDIDATE_CHANGES = "NO_CANDIDATE_CHANGES"
    TIME_AUTHORITY_UNAVAILABLE = "TIME_AUTHORITY_UNAVAILABLE"
    CAPTURE_EXPIRED = "CAPTURE_EXPIRED"
    CAPTURE_FROM_FUTURE = "CAPTURE_FROM_FUTURE"
    CAPTURE_TAMPERED = "CAPTURE_TAMPERED"
    RUNNER_NOT_ATTESTED = "RUNNER_NOT_ATTESTED"


class ChangeVerificationGap(_Model):
    code: ChangeVerificationGapCode
    identity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["RELEASE_ONLY"] = "RELEASE_ONLY"
    blocking: Literal[True] = True


class ChangeVerificationEvaluation(_Model):
    authority_scope: Literal["ANALYSIS_ONLY"] = "ANALYSIS_ONLY"
    release_eligible: Literal[False] = False
    capture_scope: Literal["LOCAL_GIT_CANDIDATE_CAPTURE"] = "LOCAL_GIT_CANDIDATE_CAPTURE"
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluated_at: str
    change_capture_complete: bool
    artifact: VerifiedChangeSet | None = None
    gaps: tuple[ChangeVerificationGap, ...]
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_evaluation(self) -> ChangeVerificationEvaluation:
        if self.change_capture_complete is not (self.artifact is not None):
            raise ValueError("Capture completeness differs from artifact presence")
        keys = tuple((item.code.value, item.identity_sha256) for item in self.gaps)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Capture gaps must be sorted and unique")
        if not set(_PERMANENT_GAPS).issubset(item.code.value for item in self.gaps):
            raise ValueError("Permanent release gaps cannot be omitted")
        _verify_digest(self, "evaluation_sha256")
        return self


DEFAULT_VERIFIED_CHANGE_POLICY_SHA256 = (
    "ef43e79fb8e2244d7187ae2a18b4d77ae7e7d17758f10fd6fe7ddb75df5a8d05"
)
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_OBJECT_ID = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_WINDOWS_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE)


def _require_semver(value: str) -> None:
    if not _SEMVER.fullmatch(value):
        raise ValueError("Version must use semantic versioning")


def _parse_timestamp(value: str) -> datetime:
    if not _TIMESTAMP.fullmatch(value):
        raise ValueError("Timestamp must be canonical UTC seconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _verify_digest(model: BaseModel, field: str) -> None:
    body = model.model_dump(mode="json")
    declared = body.pop(field)
    if stable_sha256(body) != declared:
        raise ValueError(f"{field} differs from canonical content")


def _require_safe_locator(value: str, maximum_bytes: int) -> str:
    if not value or "\\" in value or ":" in value or any(ord(char) < 32 for char in value):
        raise ValueError("Repository locator is unsafe")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError("Repository locator exceeds policy capacity")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("Repository locator is not Unicode-canonical")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Repository locator is unsafe")
    if any(part.endswith((".", " ")) or _WINDOWS_DEVICE.fullmatch(part) for part in path.parts):
        raise ValueError("Repository locator is unsafe")
    return path.as_posix()


def _require_logical_id(value: str, maximum: int) -> str:
    if (
        not value
        or len(value) > maximum
        or unicodedata.normalize("NFC", value) != value
        or any(ord(character) < 32 for character in value)
        or any(character in value for character in ("\\", ":"))
    ):
        raise ValueError("Logical identity is unsafe")
    return value


def _implementation_sha256(content: bytes) -> str:
    """Hash producer code while excluding its circular policy-root constant value."""
    normalized = re.sub(
        rb"DEFAULT_VERIFIED_CHANGE_POLICY_SHA256\s*=\s*"
        rb'(?:"[a-f0-9]{64}"|\(\s*"[a-f0-9]{64}"\s*\))',
        b'DEFAULT_VERIFIED_CHANGE_POLICY_SHA256 = (\n    "' + (b"0" * 64) + b'"\n)',
        content,
        count=1,
    )
    return hashlib.sha256(normalized).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identity(*values: Any) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _gap(code: ChangeVerificationGapCode, *identity: Any) -> ChangeVerificationGap:
    return ChangeVerificationGap(code=code, identity_sha256=_identity(code.value, *identity))


def _planned_gaps() -> list[ChangeVerificationGap]:
    return [_gap(ChangeVerificationGapCode(value)) for value in _PERMANENT_GAPS]


def _evaluation(
    policy: VerifiedChangePolicy,
    evaluated_at: str,
    gaps: list[ChangeVerificationGap],
    artifact: VerifiedChangeSet | None,
) -> ChangeVerificationEvaluation:
    ordered = tuple(sorted(set(gaps), key=lambda item: (item.code.value, item.identity_sha256)))
    body = {
        "authority_scope": "ANALYSIS_ONLY",
        "release_eligible": False,
        "capture_scope": "LOCAL_GIT_CANDIDATE_CAPTURE",
        "policy_sha256": policy.sha256,
        "evaluated_at": evaluated_at,
        "change_capture_complete": artifact is not None,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "gaps": [item.model_dump(mode="json") for item in ordered],
    }
    return ChangeVerificationEvaluation.model_validate(
        {**body, "evaluation_sha256": stable_sha256(body)}
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChangeVerificationContractError("Policy contains duplicate keys")
        result[key] = value
    return result


def load_verified_change_policy(
    path: Path,
    *,
    implementation_root: Path,
    expected_sha256: str = DEFAULT_VERIFIED_CHANGE_POLICY_SHA256,
) -> VerifiedChangePolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise ChangeVerificationContractError("Verified-change policy cannot be loaded") from exc
    if not isinstance(raw, dict) or raw.get("sha256") != contract_sha256(raw):
        raise ChangeVerificationContractError("Verified-change policy self-hash is invalid")
    if raw.get("sha256") != expected_sha256:
        raise ChangeVerificationContractError("Verified-change policy is not externally pinned")
    try:
        policy = VerifiedChangePolicy.model_validate(raw)
    except ValidationError as exc:
        raise ChangeVerificationContractError("Verified-change policy is invalid") from exc
    implementation = (
        implementation_root.resolve() / policy.producer.implementation_locator
    ).resolve()
    try:
        implementation.relative_to(implementation_root.resolve())
    except ValueError as exc:
        raise ChangeVerificationContractError("Producer implementation escapes its root") from exc
    loaded = Path(inspect.getsourcefile(LocalGitChangeProducer) or "").resolve()
    if implementation != loaded:
        raise ChangeVerificationContractError("Loaded producer does not match its policy locator")
    try:
        digest = _implementation_sha256(implementation.read_bytes())
    except OSError as exc:
        raise ChangeVerificationContractError("Producer implementation is unavailable") from exc
    if digest != policy.producer.implementation_sha256:
        raise ChangeVerificationContractError("Producer implementation digest is invalid")
    return policy


@dataclass(frozen=True)
class _TreeRecord:
    path: str
    mode: str
    object_id: str


def _run_git(
    command: list[str], timeout_seconds: int, maximum_output_bytes: int
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    streams = (process.stdout, process.stderr)
    buffers = (bytearray(), bytearray())
    exceeded = threading.Event()

    def drain(index: int) -> None:
        stream = streams[index]
        if stream is None:
            return
        while chunk := stream.read(65536):
            if len(buffers[index]) + len(chunk) > maximum_output_bytes:
                exceeded.set()
                process.kill()
                return
            buffers[index].extend(chunk)

    threads = tuple(
        threading.Thread(target=drain, args=(index,), daemon=True) for index in range(2)
    )
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        for thread in threads:
            thread.join()
    if exceeded.is_set():
        raise OverflowError("Git output exceeds the configured bound")
    return subprocess.CompletedProcess(command, returncode, bytes(buffers[0]), bytes(buffers[1]))


@dataclass(frozen=True)
class LocalGitChangeProducer:
    """Capture a complete local repository candidate tree from trusted composition."""

    project_id: str
    expected_repository_root: Path
    policy: VerifiedChangePolicy

    def capture(self, repository_hint: Path) -> ChangeVerificationEvaluation:
        gaps = _planned_gaps()
        try:
            now = _utc_now()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError
            now = now.astimezone(UTC).replace(microsecond=0)
        except (OSError, ValueError):
            return _evaluation(
                self.policy,
                "unavailable",
                [*gaps, _gap(ChangeVerificationGapCode.TIME_AUTHORITY_UNAVAILABLE)],
                None,
            )
        now_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            artifact = self._capture(repository_hint, now)
        except _CaptureRejected as exc:
            gaps.append(_gap(exc.code, *exc.identity))
            return _evaluation(self.policy, now_text, gaps, None)
        return _evaluation(self.policy, now_text, gaps, artifact)

    def verify(
        self,
        candidate: VerifiedChangeSet,
        repository_hint: Path,
    ) -> ChangeVerificationEvaluation:
        current = self.capture(repository_hint)
        now = (
            _parse_timestamp(current.evaluated_at)
            if current.evaluated_at != "unavailable"
            else None
        )
        try:
            validated = VerifiedChangeSet.model_validate(candidate.model_dump(mode="json"))
        except (AttributeError, TypeError, ValidationError, ValueError):
            validated = None
        if validated is not None and now is not None:
            if _parse_timestamp(validated.observed_at) > now:
                return _evaluation(
                    self.policy,
                    current.evaluated_at,
                    [*current.gaps, _gap(ChangeVerificationGapCode.CAPTURE_FROM_FUTURE)],
                    None,
                )
            if _parse_timestamp(validated.valid_until) <= now:
                return _evaluation(
                    self.policy,
                    current.evaluated_at,
                    [*current.gaps, _gap(ChangeVerificationGapCode.CAPTURE_EXPIRED)],
                    None,
                )
        if current.artifact is None:
            return current
        if validated is None or _static_artifact(validated) != _static_artifact(current.artifact):
            return _evaluation(
                self.policy,
                current.evaluated_at,
                [*current.gaps, _gap(ChangeVerificationGapCode.CAPTURE_TAMPERED)],
                None,
            )
        return current

    def _capture(
        self,
        repository_hint: Path,
        now: datetime,
    ) -> VerifiedChangeSet:
        self._require_runtime_policy()
        if not self.project_id or len(self.project_id) > 200:
            raise _CaptureRejected(ChangeVerificationGapCode.REPOSITORY_ROOT_MISMATCH)
        try:
            _require_logical_id(self.project_id, 200)
        except ValueError:
            raise _CaptureRejected(ChangeVerificationGapCode.STATUS_AMBIGUOUS, "identity") from None
        root_output = self._git(repository_hint, "rev-parse", "--show-toplevel")
        try:
            root = Path(root_output.decode("utf-8", "strict").strip()).resolve()
        except (OSError, UnicodeDecodeError):
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "root") from None
        expected = self.expected_repository_root.resolve()
        if root != expected:
            raise _CaptureRejected(ChangeVerificationGapCode.REPOSITORY_ROOT_MISMATCH)
        head = self._object_id(root, "HEAD")
        tree = self._object_id(root, "HEAD^{tree}")
        git_version = self._git(root, "--version")
        status_before = self._git(
            root,
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
            "--no-renames",
        )
        base_records = self._parse_tree(self._git(root, "ls-tree", "-rz", "--full-tree", "HEAD"))
        index = self._parse_index(self._git(root, "ls-files", "-z", "--stage"))
        sparse = self._git(
            root,
            "config",
            "--type=bool",
            "--default=false",
            "core.sparseCheckout",
        )
        if sparse.strip() != b"false":
            raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_INDEX_STATE, "sparse")
        status_paths = tuple(sorted(self._parse_status(status_before)))
        self._validate_index_tags(self._git(root, "ls-files", "-z", "-v"))
        candidate_paths = self._nul_paths(
            self._git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        )
        all_paths = [record.path for record in base_records]
        all_paths.extend(candidate_paths)
        all_paths.extend(status_paths)
        self._preflight_paths(all_paths)
        if len(set(candidate_paths)) > self.policy.maximum_files or len(base_records) > (
            self.policy.maximum_files
        ):
            raise _CaptureRejected(ChangeVerificationGapCode.CAPACITY_EXCEEDED, "files")

        base_files = tuple(self._read_base_files(root, base_records))
        candidate_files = tuple(
            self._read_candidate_files(root, candidate_paths, index, base_files)
        )
        if sum(item.size_bytes for item in candidate_files) > self.policy.maximum_total_bytes:
            raise _CaptureRejected(ChangeVerificationGapCode.CAPACITY_EXCEEDED, "total-bytes")
        semantic_paths = _semantic_diff_paths(base_files, candidate_files)
        changes = self._derive_changes(base_files, candidate_files, semantic_paths)
        if not changes:
            raise _CaptureRejected(ChangeVerificationGapCode.NO_CANDIDATE_CHANGES)
        if len(changes) > self.policy.maximum_changes:
            raise _CaptureRejected(ChangeVerificationGapCode.CAPACITY_EXCEEDED, "changes")
        if not set(status_paths).issubset(item.path for item in changes):
            raise _CaptureRejected(ChangeVerificationGapCode.STATUS_AMBIGUOUS, "delta")

        second = tuple(self._read_candidate_files(root, candidate_paths, index, base_files))
        if second != candidate_files:
            raise _CaptureRejected(ChangeVerificationGapCode.CONCURRENT_MUTATION, "bytes")
        if self._object_id(root, "HEAD") != head or self._object_id(root, "HEAD^{tree}") != tree:
            raise _CaptureRejected(ChangeVerificationGapCode.CONCURRENT_MUTATION, "base")
        status_after = self._git(
            root,
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
            "--no-renames",
        )
        if status_after != status_before:
            raise _CaptureRejected(ChangeVerificationGapCode.CONCURRENT_MUTATION, "status")

        base_body = [item.model_dump(mode="json") for item in base_files]
        candidate_body = [item.model_dump(mode="json") for item in candidate_files]
        base_sha = _tree_digest(base_files)
        candidate_sha = _tree_digest(candidate_files)
        repository_identity_sha256 = _identity(self.project_id, head, tree)
        source_snapshot_sha256 = stable_sha256(
            {
                "base_commit_oid": head,
                "base_tree_oid": tree,
                "candidate_input_tree_sha256": candidate_sha,
            }
        )
        build_body = {
            "project_id": self.project_id,
            "repository_identity_sha256": repository_identity_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "base_commit_oid": head,
            "base_tree_oid": tree,
            "candidate_input_tree_sha256": candidate_sha,
            "policy_sha256": self.policy.sha256,
            "producer_implementation_sha256": self.policy.producer.implementation_sha256,
        }
        valid_until = now + timedelta(seconds=self.policy.freshness_seconds)
        body = {
            "schema_version": "1.0.0",
            "authority_scope": "ANALYSIS_ONLY",
            "release_eligible": False,
            "capture_scope": "LOCAL_GIT_CANDIDATE_CAPTURE",
            "change_capture_complete": True,
            "candidate_build_verified": False,
            "project_id": self.project_id,
            "repository_identity_sha256": repository_identity_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "base_commit_oid": head,
            "base_tree_oid": tree,
            "policy_id": self.policy.policy_id,
            "policy_version": self.policy.policy_version,
            "policy_sha256": self.policy.sha256,
            "producer_id": self.policy.producer.producer_id,
            "producer_version": self.policy.producer.producer_version,
            "producer_implementation_sha256": self.policy.producer.implementation_sha256,
            "git_version_sha256": hashlib.sha256(git_version).hexdigest(),
            "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "freshness_seconds": self.policy.freshness_seconds,
            "base_files": base_body,
            "candidate_files": candidate_body,
            "changes": [item.model_dump(mode="json") for item in changes],
            "base_tree_sha256": base_sha,
            "candidate_input_tree_sha256": candidate_sha,
            "candidate_build_input_sha256": stable_sha256(build_body),
            "blocking_gap_codes": list(_PERMANENT_GAPS),
        }
        if len(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()) > (
            self.policy.maximum_manifest_bytes
        ):
            raise _CaptureRejected(ChangeVerificationGapCode.CAPACITY_EXCEEDED, "manifest")
        return VerifiedChangeSet.model_validate({**body, "manifest_sha256": stable_sha256(body)})

    def _require_runtime_policy(self) -> None:
        body = self.policy.model_dump(mode="json", by_alias=True)
        if (
            contract_sha256(body) != self.policy.sha256
            or self.policy.sha256 != DEFAULT_VERIFIED_CHANGE_POLICY_SHA256
        ):
            raise _CaptureRejected(ChangeVerificationGapCode.POLICY_ROOT_MISMATCH)
        loaded = Path(inspect.getsourcefile(LocalGitChangeProducer) or "").resolve()
        try:
            digest = _implementation_sha256(loaded.read_bytes())
        except OSError:
            raise _CaptureRejected(
                ChangeVerificationGapCode.PRODUCER_IMPLEMENTATION_MISMATCH
            ) from None
        if digest != self.policy.producer.implementation_sha256:
            raise _CaptureRejected(ChangeVerificationGapCode.PRODUCER_IMPLEMENTATION_MISMATCH)

    def _git(self, cwd: Path, *arguments: str) -> bytes:
        command = ["git", "-c", "core.quotepath=false", "-C", str(cwd), *arguments]
        try:
            completed = _run_git(
                command,
                self.policy.git_timeout_seconds,
                self.policy.maximum_git_output_bytes,
            )
        except subprocess.TimeoutExpired:
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_TIMEOUT) from None
        except OverflowError:
            raise _CaptureRejected(
                ChangeVerificationGapCode.CAPACITY_EXCEEDED, "git-output"
            ) from None
        except (OSError, RuntimeError, TypeError, ValueError):
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_COMMAND_FAILED) from None
        if not isinstance(completed, subprocess.CompletedProcess):
            raise _CaptureRejected(ChangeVerificationGapCode.RUNNER_NOT_ATTESTED)
        if not isinstance(completed.returncode, int):
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "result")
        if not isinstance(completed.stdout, (bytes, str)) or not isinstance(
            completed.stderr, (bytes, str)
        ):
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "result")
        stdout = (
            completed.stdout.encode() if isinstance(completed.stdout, str) else completed.stdout
        )
        stderr = (
            completed.stderr.encode() if isinstance(completed.stderr, str) else completed.stderr
        )
        if len(stdout) > self.policy.maximum_git_output_bytes or len(stderr) > (
            self.policy.maximum_git_output_bytes
        ):
            raise _CaptureRejected(ChangeVerificationGapCode.CAPACITY_EXCEEDED, "git-output")
        if completed.returncode != 0:
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_COMMAND_FAILED)
        return stdout

    def _object_id(self, root: Path, revision: str) -> str:
        try:
            value = self._git(root, "rev-parse", "--verify", revision).decode().strip()
        except UnicodeDecodeError:
            raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "object") from None
        if not _OBJECT_ID.fullmatch(value):
            raise _CaptureRejected(ChangeVerificationGapCode.NO_BASE_COMMIT)
        return value

    def _preflight_paths(self, values: list[str]) -> None:
        aliases: dict[str, str] = {}
        for value in values:
            try:
                locator = _require_safe_locator(value, self.policy.maximum_path_bytes)
            except ValueError:
                raise _CaptureRejected(ChangeVerificationGapCode.UNSAFE_PATH) from None
            lowered = locator.casefold()
            if any(
                fnmatch.fnmatchcase(lowered, pattern.casefold())
                for pattern in self.policy.sensitive_path_globs
            ):
                raise _CaptureRejected(
                    ChangeVerificationGapCode.SENSITIVE_PATH_REFUSED,
                    hashlib.sha256(locator.encode()).hexdigest(),
                )
            alias = unicodedata.normalize("NFC", locator).casefold()
            prior = aliases.setdefault(alias, locator)
            if prior != locator:
                raise _CaptureRejected(ChangeVerificationGapCode.DUPLICATE_PATH)

    def _parse_tree(self, output: bytes) -> tuple[_TreeRecord, ...]:
        records: list[_TreeRecord] = []
        for raw in _split_nul(output):
            try:
                metadata, path_raw = raw.split(b"\t", 1)
                mode_raw, kind, object_raw = metadata.split(b" ", 2)
                path = path_raw.decode("utf-8", "strict")
                mode = mode_raw.decode("ascii")
                object_id = object_raw.decode("ascii")
            except (ValueError, UnicodeDecodeError):
                raise _CaptureRejected(
                    ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "tree"
                ) from None
            if kind != b"blob" or mode not in self.policy.allowed_regular_modes:
                raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_ENTRY_TYPE)
            if not _OBJECT_ID.fullmatch(object_id):
                raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "tree-oid")
            records.append(_TreeRecord(path, mode, object_id))
        if len({item.path for item in records}) != len(records):
            raise _CaptureRejected(ChangeVerificationGapCode.DUPLICATE_PATH, "base")
        return tuple(sorted(records, key=lambda item: item.path))

    def _parse_index(self, output: bytes) -> dict[str, _TreeRecord]:
        records: dict[str, _TreeRecord] = {}
        for raw in _split_nul(output):
            try:
                metadata, path_raw = raw.split(b"\t", 1)
                mode_raw, object_raw, stage_raw = metadata.split(b" ", 2)
                path = path_raw.decode("utf-8", "strict")
                mode = mode_raw.decode("ascii")
                object_id = object_raw.decode("ascii")
            except (ValueError, UnicodeDecodeError):
                raise _CaptureRejected(
                    ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "index"
                ) from None
            if stage_raw != b"0" or set(object_id) == {"0"}:
                raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_INDEX_STATE)
            if mode not in self.policy.allowed_regular_modes:
                raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_ENTRY_TYPE)
            if path in records:
                raise _CaptureRejected(ChangeVerificationGapCode.DUPLICATE_PATH, "index")
            records[path] = _TreeRecord(path, mode, object_id)
        return records

    def _validate_index_tags(self, output: bytes) -> None:
        for raw in _split_nul(output):
            if len(raw) < 3 or raw[:2] != b"H ":
                raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_INDEX_STATE)

    def _parse_status(self, output: bytes) -> list[str]:
        records = _split_nul(output)
        paths: list[str] = []
        index = 0
        while index < len(records):
            raw = records[index]
            index += 1
            if raw.startswith(b"? "):
                path_raw = raw[2:]
            elif raw.startswith(b"1 "):
                fields = raw.split(b" ", 8)
                if len(fields) != 9 or b"U" in fields[1] or fields[2] != b"N...":
                    raise _CaptureRejected(ChangeVerificationGapCode.STATUS_AMBIGUOUS)
                if fields[5] != b"000000" and fields[4] != fields[5]:
                    raise _CaptureRejected(
                        ChangeVerificationGapCode.UNSUPPORTED_INDEX_STATE,
                        "unstaged-mode",
                    )
                path_raw = fields[8]
            elif raw.startswith(b"2 "):
                raise _CaptureRejected(ChangeVerificationGapCode.STATUS_AMBIGUOUS, "rename")
            elif raw.startswith((b"u ", b"! ", b"# ")):
                raise _CaptureRejected(ChangeVerificationGapCode.STATUS_AMBIGUOUS)
            else:
                raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "status")
            paths.append(_decode_path(path_raw))
        return paths

    def _nul_paths(self, output: bytes) -> list[str]:
        paths = [_decode_path(item) for item in _split_nul(output)]
        if len(paths) != len(set(paths)):
            raise _CaptureRejected(ChangeVerificationGapCode.DUPLICATE_PATH, "candidate")
        return sorted(paths)

    def _read_base_files(self, root: Path, records: tuple[_TreeRecord, ...]) -> list[GitFileEntry]:
        files: list[GitFileEntry] = []
        total = 0
        for record in records:
            try:
                size = int(self._git(root, "cat-file", "-s", record.object_id).strip())
            except ValueError:
                raise _CaptureRejected(
                    ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "blob-size"
                ) from None
            if size > self.policy.maximum_file_bytes or total + size > (
                self.policy.maximum_total_bytes
            ):
                raise _CaptureRejected(ChangeVerificationGapCode.CAPACITY_EXCEEDED, "base-bytes")
            content = self._git(root, "cat-file", "blob", record.object_id)
            total += len(content)
            if len(content) != size:
                raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "blob-size")
            files.append(
                GitFileEntry(
                    path=record.path,
                    mode=record.mode,
                    tracking="TRACKED",
                    size_bytes=len(content),
                    content_sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        return files

    def _read_candidate_files(
        self,
        root: Path,
        paths: list[str],
        index: dict[str, _TreeRecord],
        base_files: tuple[GitFileEntry, ...],
    ) -> list[GitFileEntry]:
        base_paths = {item.path for item in base_files}
        files: list[GitFileEntry] = []
        total = 0
        for locator in paths:
            target = root.joinpath(*PurePosixPath(locator).parts)
            try:
                details = target.lstat()
            except FileNotFoundError:
                if locator in base_paths:
                    continue
                raise _CaptureRejected(
                    ChangeVerificationGapCode.CONCURRENT_MUTATION, "missing"
                ) from None
            except (OSError, ValueError):
                raise _CaptureRejected(ChangeVerificationGapCode.UNSAFE_PATH) from None
            if stat.S_ISLNK(details.st_mode):
                raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_ENTRY_TYPE)
            if details.st_size > self.policy.maximum_file_bytes or total + details.st_size > (
                self.policy.maximum_total_bytes
            ):
                raise _CaptureRejected(
                    ChangeVerificationGapCode.CAPACITY_EXCEEDED, "candidate-bytes"
                )
            try:
                relative = target.resolve(strict=False).relative_to(root)
            except (OSError, ValueError):
                raise _CaptureRejected(ChangeVerificationGapCode.UNSAFE_PATH) from None
            expected_relative = Path(*PurePosixPath(locator).parts)
            if not stat.S_ISREG(details.st_mode) or relative != expected_relative:
                raise _CaptureRejected(ChangeVerificationGapCode.UNSUPPORTED_ENTRY_TYPE)
            try:
                content = target.read_bytes()
            except OSError:
                raise _CaptureRejected(
                    ChangeVerificationGapCode.CONCURRENT_MUTATION, "read"
                ) from None
            total += len(content)
            if len(content) != details.st_size:
                raise _CaptureRejected(ChangeVerificationGapCode.CONCURRENT_MUTATION, "file-size")
            if len(content) > self.policy.maximum_file_bytes or total > (
                self.policy.maximum_total_bytes
            ):
                raise _CaptureRejected(
                    ChangeVerificationGapCode.CAPACITY_EXCEEDED, "candidate-bytes"
                )
            tracked = locator in index
            mode = (
                index[locator].mode
                if tracked
                else ("100755" if details.st_mode & stat.S_IXUSR else "100644")
            )
            files.append(
                GitFileEntry(
                    path=locator,
                    mode=mode,
                    tracking="TRACKED" if tracked else "UNTRACKED",
                    size_bytes=len(content),
                    content_sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        return sorted(files, key=lambda item: item.path)

    @staticmethod
    def _derive_changes(
        base_files: tuple[GitFileEntry, ...],
        candidate_files: tuple[GitFileEntry, ...],
        status_paths: tuple[str, ...],
    ) -> tuple[GitChangeEntry, ...]:
        return _derive_change_entries(base_files, candidate_files, status_paths)


def _decode_path(value: bytes) -> str:
    try:
        return value.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "path") from None


def _split_nul(value: bytes) -> list[bytes]:
    if value and not value.endswith(b"\0"):
        raise _CaptureRejected(ChangeVerificationGapCode.GIT_OUTPUT_INVALID, "nul")
    return value[:-1].split(b"\0") if value else []


def _static_artifact(value: VerifiedChangeSet) -> dict[str, Any]:
    body = value.model_dump(mode="json")
    for field in ("observed_at", "valid_until", "manifest_sha256"):
        body.pop(field)
    return body
