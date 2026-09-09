from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import neo_sf_q_intel.change_verification as change_verification
from neo_sf_q_intel.change_verification import (
    DEFAULT_VERIFIED_CHANGE_POLICY_SHA256,
    ChangeOperation,
    ChangeVerificationContractError,
    ChangeVerificationGapCode,
    LocalGitChangeProducer,
    VerifiedChangeSet,
    load_verified_change_policy,
)

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "config" / "verified-change-policy.json"
T0 = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)


def _run(repository: Path, *arguments: str, input: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=False,
        shell=False,
        input=input,
    )
    return completed.stdout


def _repository(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir(parents=True)
    _run(repository, "init", "--quiet")
    _run(repository, "config", "user.email", "fixture@example.invalid")
    _run(repository, "config", "user.name", "Fixture")
    for locator, content in (files or {"src/alpha.txt": b"alpha\n"}).items():
        target = repository.joinpath(*locator.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _run(repository, "add", "--all")
    _run(repository, "commit", "--quiet", "-m", "base")
    return repository


def _policy():
    return load_verified_change_policy(
        POLICY_PATH,
        implementation_root=ROOT,
    )


def _producer(repository: Path) -> LocalGitChangeProducer:
    return LocalGitChangeProducer(
        project_id="project-fixture",
        expected_repository_root=repository,
        policy=_policy(),
    )


def _codes(evaluation) -> set[ChangeVerificationGapCode]:
    return {gap.code for gap in evaluation.gaps}


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0)


def test_policy_is_self_hashed_and_pins_loaded_implementation() -> None:
    policy = _policy()

    assert policy.sha256 == DEFAULT_VERIFIED_CHANGE_POLICY_SHA256
    implementation = ROOT / policy.producer.implementation_locator
    assert (
        change_verification._implementation_sha256(implementation.read_bytes())
        == policy.producer.implementation_sha256
    )


def test_capture_binds_complete_actual_candidate_tree_and_delta(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {"src/alpha.txt": b"alpha\r\n", "src/binary.bin": b"\x00\xffbase"},
    )
    (repository / "src" / "alpha.txt").write_bytes(b"changed\r\n")
    (repository / "src" / "binary.bin").unlink()
    (repository / "empty.dat").write_bytes(b"")

    evaluation = _producer(repository).capture(repository)

    assert evaluation.change_capture_complete is True
    artifact = evaluation.artifact
    assert artifact is not None
    assert artifact.authority_scope == "ANALYSIS_ONLY"
    assert artifact.release_eligible is False
    assert artifact.candidate_build_verified is False
    assert [(item.operation, item.path) for item in artifact.changes] == [
        (ChangeOperation.ADD, "empty.dat"),
        (ChangeOperation.MODIFY, "src/alpha.txt"),
        (ChangeOperation.DELETE, "src/binary.bin"),
    ]
    by_path = {item.path: item for item in artifact.candidate_files}
    assert by_path["empty.dat"].size_bytes == 0
    assert by_path["empty.dat"].content_sha256 == hashlib.sha256(b"").hexdigest()
    assert by_path["src/alpha.txt"].content_sha256 == hashlib.sha256(b"changed\r\n").hexdigest()
    serialized = artifact.model_dump_json()
    assert str(repository) not in serialized
    assert "changed" not in serialized
    assert "CANDIDATE_BUILD_NOT_VERIFIED" in artifact.blocking_gap_codes

    narrowed = artifact.model_dump(mode="json")
    narrowed["changes"] = narrowed["changes"][1:]
    unsigned = {key: value for key, value in narrowed.items() if key != "manifest_sha256"}
    narrowed["manifest_sha256"] = change_verification.stable_sha256(unsigned)
    with pytest.raises(ValidationError):
        VerifiedChangeSet.model_validate(narrowed)


def test_staged_then_unstaged_bytes_capture_final_full_tree(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    target = repository / "src" / "alpha.txt"
    target.write_bytes(b"staged")
    _run(repository, "add", "src/alpha.txt")
    target.write_bytes(b"working-tree-final")

    artifact = _producer(repository).capture(repository).artifact

    assert artifact is not None
    changed = artifact.changes[0]
    assert changed.after_sha256 == hashlib.sha256(b"working-tree-final").hexdigest()


def test_rename_is_deterministic_delete_plus_add(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"old/name.txt": b"same-bytes"})
    (repository / "new").mkdir()
    os.replace(repository / "old" / "name.txt", repository / "new" / "name.txt")

    artifact = _producer(repository).capture(repository).artifact

    assert artifact is not None
    assert [(item.operation, item.path) for item in artifact.changes] == [
        (ChangeOperation.ADD, "new/name.txt"),
        (ChangeOperation.DELETE, "old/name.txt"),
    ]


def test_manifest_order_is_stable_across_creation_order(tmp_path: Path) -> None:
    first = _repository(tmp_path / "first", {"z.txt": b"z", "a.txt": b"a"})
    second = _repository(tmp_path / "second", {"a.txt": b"a", "z.txt": b"z"})
    for repository in (first, second):
        (repository / "m.txt").write_bytes(b"m")

    first_artifact = _producer(first).capture(first).artifact
    second_artifact = _producer(second).capture(second).artifact

    assert first_artifact is not None and second_artifact is not None
    assert first_artifact.candidate_input_tree_sha256 == (
        second_artifact.candidate_input_tree_sha256
    )
    assert tuple(item.path for item in first_artifact.candidate_files) == (
        "a.txt",
        "m.txt",
        "z.txt",
    )


def test_staging_state_is_audit_only_not_candidate_tree_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "new.txt").write_bytes(b"identical candidate bytes")
    producer = _producer(repository)

    untracked = producer.capture(repository).artifact
    _run(repository, "add", "new.txt")
    staged = producer.capture(repository).artifact

    assert untracked is not None and staged is not None
    assert untracked.candidate_input_tree_sha256 == staged.candidate_input_tree_sha256
    assert untracked.candidate_build_input_sha256 == staged.candidate_build_input_sha256
    untracked_by_path = {item.path: item for item in untracked.candidate_files}
    staged_by_path = {item.path: item for item in staged.candidate_files}
    assert untracked_by_path["new.txt"].tracking == "UNTRACKED"
    assert staged_by_path["new.txt"].tracking == "TRACKED"


def test_repository_identity_binds_actual_base_history_not_directory(tmp_path: Path) -> None:
    first = _repository(tmp_path / "first", {"same.txt": b"first history"})
    second = _repository(tmp_path / "second", {"same.txt": b"second history"})
    (first / "same.txt").write_bytes(b"candidate")
    (second / "same.txt").write_bytes(b"candidate")

    first_artifact = _producer(first).capture(first).artifact
    second_artifact = _producer(second).capture(second).artifact

    assert first_artifact is not None and second_artifact is not None
    assert first_artifact.repository_identity_sha256 != (second_artifact.repository_identity_sha256)


def test_renamed_topology_retains_generic_capture_class(tmp_path: Path) -> None:
    first = _repository(tmp_path / "first", {"feature/x.py": b"one"})
    second = _repository(tmp_path / "second", {"metadata/widget.xml": b"one"})
    (first / "feature" / "x.py").write_bytes(b"two")
    (second / "metadata" / "widget.xml").write_bytes(b"two")

    artifacts = [
        _producer(repository).capture(repository).artifact for repository in (first, second)
    ]

    assert all(artifact is not None for artifact in artifacts)
    assert [
        [item.operation for item in artifact.changes] for artifact in artifacts if artifact
    ] == [
        [ChangeOperation.MODIFY],
        [ChangeOperation.MODIFY],
    ]


def test_later_unexpired_verification_refreshes_time_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    producer = _producer(repository)
    candidate = producer.capture(repository).artifact
    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0 + timedelta(seconds=30))

    evaluation = producer.verify(candidate, repository)

    assert evaluation.artifact is not None
    assert evaluation.artifact.observed_at != candidate.observed_at
    assert evaluation.artifact.candidate_input_tree_sha256 == (
        candidate.candidate_input_tree_sha256
    )


def test_expired_and_tampered_candidate_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    producer = _producer(repository)
    candidate = producer.capture(repository).artifact
    assert candidate is not None
    monkeypatch.setattr(
        change_verification,
        "_utc_now",
        lambda: T0 + timedelta(seconds=_policy().freshness_seconds + 1),
    )
    expired = producer.verify(candidate, repository)
    assert ChangeVerificationGapCode.CAPTURE_EXPIRED in _codes(expired)
    assert expired.artifact is None

    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0)
    forged = candidate.model_copy(update={"project_id": "other-project"})
    tampered = producer.verify(forged, repository)
    assert ChangeVerificationGapCode.CAPTURE_TAMPERED in _codes(tampered)
    assert tampered.artifact is None


def test_future_candidate_fails_with_specific_gap(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    producer = _producer(repository)
    candidate = producer.capture(repository).artifact
    assert candidate is not None
    body = candidate.model_dump(mode="json")
    body["observed_at"] = (T0 + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body["valid_until"] = (T0 + timedelta(seconds=10 + candidate.freshness_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    unsigned = {key: value for key, value in body.items() if key != "manifest_sha256"}
    future = VerifiedChangeSet.model_validate(
        {**unsigned, "manifest_sha256": change_verification.stable_sha256(unsigned)}
    )

    evaluation = producer.verify(future, repository)

    assert ChangeVerificationGapCode.CAPTURE_FROM_FUTURE in _codes(evaluation)
    assert evaluation.artifact is None


def test_wrong_repository_root_is_refused_without_persisting_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "expected")
    other = _repository(tmp_path / "other")
    (other / "src" / "alpha.txt").write_bytes(b"changed")

    evaluation = _producer(repository).capture(other)

    assert ChangeVerificationGapCode.REPOSITORY_ROOT_MISMATCH in _codes(evaluation)
    assert str(other) not in evaluation.model_dump_json()


@pytest.mark.parametrize(
    "secret_locator",
    [".env", "credentials.json", "admin-auth.json", "secret.txt", "token.json"],
)
def test_tracked_secret_path_is_refused_before_blob_admission(
    tmp_path: Path, secret_locator: str
) -> None:
    repository = _repository(tmp_path, {secret_locator: b"AZURE_KEY=do-not-leak"})
    (repository / "ordinary.txt").write_bytes(b"change")

    evaluation = _producer(repository).capture(repository)

    assert ChangeVerificationGapCode.SENSITIVE_PATH_REFUSED in _codes(evaluation)
    serialized = evaluation.model_dump_json()
    assert "AZURE_KEY" not in serialized
    assert "do-not-leak" not in serialized
    assert str(repository) not in serialized


def test_secret_preflight_occurs_before_any_repository_blob_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path, {"credentials.json": b"never-read"})
    (repository / "ordinary.txt").write_bytes(b"change")
    actual_runner = change_verification._run_git
    commands: list[tuple[str, ...]] = []

    def spy(command, timeout_seconds, maximum_output_bytes):
        commands.append(tuple(command))
        return actual_runner(command, timeout_seconds, maximum_output_bytes)

    monkeypatch.setattr(change_verification, "_run_git", spy)
    evaluation = _producer(repository).capture(repository)

    assert ChangeVerificationGapCode.SENSITIVE_PATH_REFUSED in _codes(evaluation)
    assert not any("cat-file" in command for command in commands)


def test_core_autocrlf_candidate_hash_uses_actual_working_bytes(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            ".gitattributes": b"normalized.txt text\nchanged.txt -text\n",
            "normalized.txt": b"line-one\nline-two\n",
            "changed.txt": b"before\n",
        },
    )
    _run(repository, "config", "core.autocrlf", "true")
    _run(repository, "add", "--renormalize", ".")
    _run(repository, "commit", "--quiet", "--allow-empty", "-m", "normalized baseline")
    (repository / "normalized.txt").write_bytes(b"line-one\r\nline-two\r\n")
    _run(repository, "add", "normalized.txt")
    assert _run(repository, "status", "--porcelain", "--", "normalized.txt") == b""
    (repository / "changed.txt").write_bytes(b"after\r\n")

    artifact = _producer(repository).capture(repository).artifact

    assert artifact is not None
    normalized = {item.path: item for item in artifact.candidate_files}["normalized.txt"]
    assert normalized.content_sha256 == hashlib.sha256(b"line-one\r\nline-two\r\n").hexdigest()
    assert [item.path for item in artifact.changes] == ["changed.txt", "normalized.txt"]


def test_unstaged_mode_drift_is_refused_and_staged_mode_is_bound() -> None:
    producer = LocalGitChangeProducer(
        project_id="project", expected_repository_root=ROOT, policy=_policy()
    )
    hash_field = b"0" * 40
    record = b"1 .M N... 100644 100644 100755 " + hash_field + b" " + hash_field + b" file.txt\0"
    with pytest.raises(Exception, match="UNSUPPORTED_INDEX_STATE"):
        producer._parse_status(record)

    before = (
        change_verification.GitFileEntry(
            path="file.txt",
            mode="100644",
            tracking="TRACKED",
            size_bytes=1,
            content_sha256=hashlib.sha256(b"x").hexdigest(),
        ),
    )
    after = (before[0].model_copy(update={"mode": "100755"}),)
    changes = producer._derive_changes(before, after, ("file.txt",))
    assert changes[0].operation is ChangeOperation.MODIFY
    assert changes[0].after_mode == "100755"


@pytest.mark.parametrize(
    "locator",
    [
        "/absolute/path",
        "../escape",
        "nested/../escape",
        "CON",
        "folder/NUL.txt",
        "stream:name",
        "trailing. ",
        "bad\\separator",
        "line\nbreak",
        "e\u0301.txt",
    ],
)
def test_unsafe_and_alias_paths_are_rejected(locator: str) -> None:
    producer = LocalGitChangeProducer(
        project_id="project",
        expected_repository_root=ROOT,
        policy=_policy(),
    )

    with pytest.raises(Exception, match="UNSAFE_PATH"):
        producer._preflight_paths([locator])

    with pytest.raises(Exception, match="DUPLICATE_PATH"):
        producer._preflight_paths(["Folder/a.txt", "folder/a.txt"])


def test_symlink_and_submodule_modes_are_refused_before_blob_read(tmp_path: Path) -> None:
    for mode, name in (("120000", "link"), ("160000", "submodule")):
        repository = _repository(tmp_path / name)
        oid = _run(repository, "rev-parse", "HEAD").decode().strip()
        if mode == "120000":
            oid = _run(repository, "hash-object", "-w", "--stdin", input=b"outside")
            oid = oid.decode().strip()
        _run(repository, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{name}")
        _run(repository, "commit", "--quiet", "-m", name)
        (repository / "src" / "alpha.txt").write_bytes(b"changed")

        evaluation = _producer(repository).capture(repository)

        assert ChangeVerificationGapCode.UNSUPPORTED_ENTRY_TYPE in _codes(evaluation)


def test_timeout_malformed_status_and_concurrent_change_are_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    actual_runner = change_verification._run_git

    def timeout_runner(command, timeout_seconds, maximum_output_bytes):
        raise subprocess.TimeoutExpired(command, timeout=timeout_seconds)

    monkeypatch.setattr(change_verification, "_run_git", timeout_runner)
    timed_out = _producer(repository).capture(repository)
    assert ChangeVerificationGapCode.GIT_TIMEOUT in _codes(timed_out)

    def malformed_runner(command, timeout_seconds, maximum_output_bytes):
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, b"not-nul", b"")
        return actual_runner(command, timeout_seconds, maximum_output_bytes)

    monkeypatch.setattr(change_verification, "_run_git", malformed_runner)
    malformed = _producer(repository).capture(repository)
    assert ChangeVerificationGapCode.GIT_OUTPUT_INVALID in _codes(malformed)

    status_calls = 0

    def moving_runner(command, timeout_seconds, maximum_output_bytes):
        nonlocal status_calls
        completed = actual_runner(command, timeout_seconds, maximum_output_bytes)
        if "status" in command:
            status_calls += 1
            if status_calls == 2:
                return subprocess.CompletedProcess(command, 0, completed.stdout + b"? later\0", b"")
        return completed

    monkeypatch.setattr(change_verification, "_run_git", moving_runner)
    moving = _producer(repository).capture(repository)
    assert ChangeVerificationGapCode.CONCURRENT_MUTATION in _codes(moving)

    def malformed_result(command, timeout_seconds, maximum_output_bytes):
        return subprocess.CompletedProcess(command, 0, None, b"")

    monkeypatch.setattr(change_verification, "_run_git", malformed_result)
    invalid_result = _producer(repository).capture(repository)
    assert ChangeVerificationGapCode.GIT_OUTPUT_INVALID in _codes(invalid_result)


def test_commit_during_second_byte_capture_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    producer = _producer(repository)
    original = LocalGitChangeProducer._read_candidate_files
    calls = 0

    def commit_on_second_read(self, root, paths, index, base_files):
        nonlocal calls
        files = original(self, root, paths, index, base_files)
        calls += 1
        if calls == 2:
            _run(root, "add", "--all")
            _run(root, "commit", "--quiet", "-m", "concurrent commit")
        return files

    monkeypatch.setattr(
        LocalGitChangeProducer, "_read_candidate_files", commit_on_second_read
    )

    evaluation = producer.capture(repository)

    assert evaluation.artifact is None
    assert ChangeVerificationGapCode.CONCURRENT_MUTATION in _codes(evaluation)


def test_capacity_is_checked_before_candidate_or_base_content_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    candidate = repository / "large.bin"
    candidate.write_bytes(b"12345")
    policy = _policy().model_copy(update={"maximum_file_bytes": 4, "maximum_total_bytes": 4})
    producer = LocalGitChangeProducer(
        project_id="project", expected_repository_root=repository, policy=policy
    )
    original_read = Path.read_bytes

    def guarded_read(path: Path) -> bytes:
        if path == candidate:
            raise AssertionError("oversized candidate content was read")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(Exception, match="CAPACITY_EXCEEDED"):
        producer._read_candidate_files(repository, ["large.bin"], {}, ())

    calls: list[tuple[str, ...]] = []

    def size_only(_producer: LocalGitChangeProducer, root: Path, *arguments: str) -> bytes:
        assert root == repository
        calls.append(arguments)
        if arguments[:2] == ("cat-file", "-s"):
            return b"5\n"
        raise AssertionError("oversized base blob content was read")

    monkeypatch.setattr(LocalGitChangeProducer, "_git", size_only)
    record = change_verification._TreeRecord("large.bin", "100644", "0" * 40)
    with pytest.raises(Exception, match="CAPACITY_EXCEEDED"):
        producer._read_base_files(repository, (record,))
    assert calls == [("cat-file", "-s", "0" * 40)]


def test_clean_and_unborn_repositories_never_seal_empty_scope(tmp_path: Path) -> None:
    clean = _repository(tmp_path / "clean")
    clean_evaluation = _producer(clean).capture(clean)
    assert ChangeVerificationGapCode.NO_CANDIDATE_CHANGES in _codes(clean_evaluation)
    assert clean_evaluation.artifact is None

    unborn = tmp_path / "unborn"
    unborn.mkdir(parents=True)
    _run(unborn, "init", "--quiet")
    unborn_evaluation = _producer(unborn).capture(unborn)
    assert unborn_evaluation.artifact is None
    assert _codes(unborn_evaluation) & {
        ChangeVerificationGapCode.GIT_COMMAND_FAILED,
        ChangeVerificationGapCode.NO_BASE_COMMIT,
    }


def test_policy_rotation_and_model_gap_omission_fail_closed(tmp_path: Path) -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["freshnessSeconds"] += 1
    altered = tmp_path / "altered-policy.json"
    altered.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ChangeVerificationContractError):
        load_verified_change_policy(altered, implementation_root=ROOT)

    repository = _repository(tmp_path / "repo")
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    artifact = _producer(repository).capture(repository).artifact
    assert artifact is not None
    assert {
        "CHANGE_SEED_SCOPE_NOT_ATTESTED",
        "RISK_FACTORS_NOT_ATTESTED",
        "CONFLICT_SCOPE_NOT_ATTESTED",
        "HUMAN_APPROVAL_SCOPE_NOT_ATTESTED",
    }.issubset(artifact.blocking_gap_codes)
    body = artifact.model_dump(mode="json")
    body["blocking_gap_codes"] = body["blocking_gap_codes"][1:]
    with pytest.raises(ValidationError):
        VerifiedChangeSet.model_validate(body)


def test_naive_clock_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alpha.txt").write_bytes(b"changed")
    monkeypatch.setattr(change_verification, "_utc_now", lambda: datetime(2026, 9, 9, 8, 0))

    evaluation = _producer(repository).capture(repository)

    assert ChangeVerificationGapCode.TIME_AUTHORITY_UNAVAILABLE in _codes(evaluation)
    assert evaluation.artifact is None
