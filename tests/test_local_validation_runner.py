from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import neo_sf_q_intel.local_validation_runner as runner_module
from neo_sf_q_intel.candidate_target_compiler import ProductionCandidateTargetCompiler
from neo_sf_q_intel.live_receipt_ledger import SQLiteLiveReceiptLedger
from neo_sf_q_intel.live_receipts import (
    ReceiptScope,
    TrustedIssuer,
    TrustedIssuerClass,
    TrustedIssuerRegistry,
)
from neo_sf_q_intel.local_validation import (
    ROLE,
    LocalValidationRunnerPins,
    verify_local_validation_evidence,
)
from neo_sf_q_intel.local_validation_runner import (
    HostLocalValidationService,
    LocalValidationRunError,
    NodeCompleted,
    NodeInvocation,
    SQLiteLocalValidationArtifactStore,
    SubprocessNodeRunner,
    local_validation_runner_implementation_sha256,
)
from tests.test_candidate_target_compiler import (
    _bundle,
    _inputs,
    _local_declarations,
)

PRODUCT_KEY = b"local-product-execution-key-material-0001"
PASS_RESULT = (
    b'{"protocol":"neo-node-test-events-v1","complete":true,"tests":1,'
    b'"pass":1,"fail":0,"cancelled":0,"skipped":0,"todo":0}'
)


@dataclass
class FakeNodeRunner:
    result: NodeCompleted = NodeCompleted(0, PASS_RESULT)
    callback: object | None = None
    invocations: list[NodeInvocation] = field(default_factory=list)

    def version(self, *, timeout_seconds: float) -> str:
        assert 0 < timeout_seconds <= 5
        return "26.5.0"

    def run(self, invocation: NodeInvocation) -> NodeCompleted:
        self.invocations.append(invocation)
        if callable(self.callback):
            self.callback(invocation)
        if invocation.result_authentication_key is None or self.result.returncode != 0:
            return self.result
        document = self.result.stdout
        envelope = {
            "document": base64.b64encode(document).decode("ascii"),
            "mac": hmac.new(
                invocation.result_authentication_key, document, hashlib.sha256
            ).hexdigest(),
        }
        return NodeCompleted(
            self.result.returncode,
            json.dumps(envelope, separators=(",", ":")).encode(),
            self.result.timed_out,
            self.result.output_exceeded,
            self.result.quiescent,
        )


def _compiled(
    tmp_path: Path,
    *,
    prefix: str = "workspace/dx",
    script: bytes | None = None,
    obligation_count: int = 1,
    maximum_seconds: int = 60,
    baseline_optional: bool = False,
):
    declarations = _local_declarations()
    declarations["nonRuntimeFiles"][0]["locator"] = f"{prefix}/docs/guide.md"
    declarations["localTestObligations"][0].update(
        workingDirectory=prefix,
        testLocators=[f"{prefix}/catalog.test.mjs"],
        maximumSeconds=maximum_seconds,
    )
    declaration = declarations["localTestObligations"][0]
    declarations["localTestObligations"] = [
        {**declaration, "obligationId": f"local:documentation-{index}"}
        for index in range(obligation_count)
    ]
    declarations["nonRuntimeFiles"][0]["localObligationIds"] = [
        value["obligationId"] for value in declarations["localTestObligations"]
    ]
    if baseline_optional:
        from neo_sf_q_intel.local_validation_phase import CANDIDATE_REQUIRED_PHASES

        for value in declarations["localTestObligations"]:
            value["requiredEvidencePhases"] = [str(phase) for phase in CANDIDATE_REQUIRED_PHASES]
    test_bytes = script or (
        b"import test from 'node:test';"
        b"import assert from 'node:assert/strict';"
        b"test('manifest',()=>assert.equal(2+2,4));"
    )
    pair = _bundle(
        tmp_path,
        declarations=declarations,
        extra_changes=(
            (f"{prefix}/docs/guide.md", b"# Before\n", b"# Current\n"),
            (f"{prefix}/catalog.test.mjs", test_bytes, test_bytes),
        ),
    )
    return pair[0], ProductionCandidateTargetCompiler().compile(_inputs(pair))


def _scope(compilation) -> ReceiptScope:
    return ReceiptScope(
        campaign_id="local-validation-campaign",
        project_id=compilation.verified_scope.project_id,
        source_contract_sha256=compilation.source_contract_sha256,
        candidate_sha256=compilation.candidate_bundle_sha256,
        build_sha256="1" * 64,
        operation_plan_sha256="2" * 64,
        restore_scope_sha256="3" * 64,
        policy_sha256="4" * 64,
        profile_sha256="5" * 64,
        org_fingerprint_sha256="6" * 64,
        actor_fingerprint_sha256="7" * 64,
        recovery_deadline=datetime.now(UTC) + timedelta(hours=1),
    )


def _pins(**changes: object) -> LocalValidationRunnerPins:
    values = {
        "runner_id": "host-local-node",
        "runner_version": "1.0.0",
        "implementation_sha256": local_validation_runner_implementation_sha256(),
        "node_executable_locator": "node",
        "node_expected_major": 26,
        "node_minimum_version": "26.5.0",
        "allowed_runner_kinds": ("NODE_GENERATED_CHECK", "NODE_TEST"),
        "maximum_global_timeout_seconds": 60,
        "maximum_output_bytes": 1024 * 1024,
    }
    values.update(changes)
    return LocalValidationRunnerPins(**values)


def _ports(tmp_path: Path):
    ledger = SQLiteLiveReceiptLedger(tmp_path / "receipts.sqlite3")
    ledger.setup()
    artifacts = SQLiteLocalValidationArtifactStore(tmp_path / "artifacts.sqlite3")
    artifacts.setup()
    issuer = TrustedIssuer(
        issuer_id="local-product-execution",
        issuer_class=TrustedIssuerClass.PRODUCT_EXECUTION,
        hmac_key=PRODUCT_KEY,
        allowed_receipt_roles=frozenset({ROLE}),
    )
    return ledger, artifacts, issuer


def _service(
    tmp_path: Path,
    repository: Path,
    node_runner,
    *,
    pins: LocalValidationRunnerPins | None = None,
):
    ledger, artifacts, issuer = _ports(tmp_path)
    return (
        HostLocalValidationService(
            repository_root=repository,
            pins=pins or _pins(),
            ledger=ledger,
            artifact_store=artifacts,
            product_issuer=issuer,
            runner=node_runner,
        ),
        ledger,
        artifacts,
        issuer,
    )


def test_full_manifest_runs_and_replays_exact_durable_artifact_and_receipt(tmp_path) -> None:
    repository, compilation = _compiled(tmp_path)
    node = FakeNodeRunner()
    service, ledger, artifacts, issuer = _service(tmp_path, repository, node)
    scope = _scope(compilation)

    evidence = service.run(compilation, scope=scope)
    verified = verify_local_validation_evidence(
        compilation,
        evidence,
        scope=scope,
        registry=TrustedIssuerRegistry([issuer]),
        ledger=ledger,
        artifact_store=artifacts,
        runner_pins=_pins(),
        observed_at=datetime.now(UTC),
    )

    assert len(evidence) == len(verified) == 1
    assert len(evidence[0].artifact.bound_files) > 2
    assert evidence[0].artifact.candidate_tree_sha256 == (
        compilation.required_local_validations[0].candidate_tree_sha256
    )
    assert artifacts.read(artifact_sha256=verified[0].artifact_sha256).document_bytes
    assert ledger.replay(campaign_id=scope.campaign_id)[0].receipt_role == ROLE
    assert node.invocations[0].arguments[0] == "--permission"
    assert node.invocations[0].arguments[-2].endswith("catalog.test.mjs")
    assert all("secret" not in str(value).casefold() for value in evidence)

    recovered = service.recover(compilation, scope=scope)
    assert recovered == evidence
    assert len(node.invocations) == 1

    wrong_scope = scope.model_copy(update={"campaign_id": "different-local-campaign"})
    assert service.recover(compilation, scope=wrong_scope) is None


@pytest.mark.parametrize(
    ("completed", "expected"),
    (
        (NodeCompleted(1, b"token=must-not-leak"), "LOCAL_VALIDATION_FAILED"),
        (NodeCompleted(0, b"", timed_out=True), "LOCAL_VALIDATION_TIMEOUT"),
        (NodeCompleted(0, b"", output_exceeded=True), "LOCAL_VALIDATION_OUTPUT_EXCEEDED"),
        (NodeCompleted(0, b"", quiescent=False), "LOCAL_PROCESS_TREE_NOT_QUIESCENT"),
        (
            NodeCompleted(
                0,
                b'{"protocol":"neo-node-test-events-v1","complete":true,"tests":1,'
                b'"pass":0,"fail":0,"cancelled":0,"skipped":1,"todo":0}',
            ),
            "LOCAL_TEST_NOT_COMPLETE_PASS_NO_SKIP",
        ),
    ),
)
def test_failure_timeout_output_process_tree_and_skip_never_issue_receipt(
    tmp_path, completed, expected
) -> None:
    repository, compilation = _compiled(tmp_path)
    node = FakeNodeRunner(result=completed)
    service, ledger, _artifacts, _issuer = _service(tmp_path, repository, node)

    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))

    assert caught.value.code == expected
    assert str(caught.value) == expected
    assert ledger.replay(campaign_id="local-validation-campaign") == ()


def test_source_tamper_and_staged_tamper_fail_closed(tmp_path) -> None:
    repository, compilation = _compiled(tmp_path)
    source_target = repository / "workspace/dx/docs/guide.md"

    def mutate_source(_invocation: NodeInvocation) -> None:
        source_target.write_bytes(b"tampered")

    service, *_ = _service(tmp_path, repository, FakeNodeRunner(callback=mutate_source))
    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))
    assert caught.value.code == "LOCAL_CANDIDATE_CHANGED_DURING_VALIDATION"

    repository, compilation = _compiled(tmp_path / "stage-case")

    def mutate_stage(invocation: NodeInvocation) -> None:
        stage = invocation.working_directory.parents[1]
        (stage / "workspace/dx/docs/guide.md").write_bytes(b"tampered")

    service, *_ = _service(
        tmp_path / "stage-ports", repository, FakeNodeRunner(callback=mutate_stage)
    )
    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))
    assert caught.value.code == "LOCAL_STAGED_TREE_CHANGED"


def test_host_node_event_driver_is_rehashed_after_candidate_execution(tmp_path) -> None:
    repository, compilation = _compiled(tmp_path)

    def mutate_driver(invocation: NodeInvocation) -> None:
        driver = Path(invocation.arguments[-3])
        driver.chmod(0o600)
        driver.write_bytes(b"tampered")

    service, ledger, *_ = _service(
        tmp_path / "ports", repository, FakeNodeRunner(callback=mutate_driver)
    )
    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))
    assert caught.value.code == "LOCAL_TEST_DRIVER_CHANGED"
    assert ledger.replay(campaign_id="local-validation-campaign") == ()


def test_hardlink_and_reparse_source_are_rejected_before_execution(tmp_path, monkeypatch) -> None:
    repository, compilation = _compiled(tmp_path)
    target = repository / "workspace/dx/docs/guide.md"
    twin = repository / "workspace/dx/docs/twin.md"
    twin.write_bytes(target.read_bytes())
    target.unlink()
    os.link(twin, target)
    node = FakeNodeRunner()
    service, *_ = _service(tmp_path / "ports", repository, node)
    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))
    assert caught.value.code == "LOCAL_SOURCE_ENTRY_UNSAFE"
    assert node.invocations == []

    repository, compilation = _compiled(tmp_path / "reparse")
    original = runner_module._is_reparse

    def force_one_reparse(metadata) -> bool:
        return original(metadata) or metadata.st_size == len(b"# Current\n")

    monkeypatch.setattr(runner_module, "_is_reparse", force_one_reparse)
    service, *_ = _service(tmp_path / "reparse-ports", repository, FakeNodeRunner())
    with pytest.raises((LocalValidationRunError, ValueError)):
        service.run(compilation, scope=_scope(compilation))


def test_runner_pin_scope_and_scenario_rename_are_generic(tmp_path) -> None:
    repository, compilation = _compiled(tmp_path, prefix="alternate/catalog-root")
    with pytest.raises(ValueError, match="implementation pin"):
        _service(
            tmp_path / "stale",
            repository,
            FakeNodeRunner(),
            pins=_pins(implementation_sha256="0" * 64),
        )
    service, *_ = _service(tmp_path / "renamed", repository, FakeNodeRunner())
    wrong = _scope(compilation).model_copy(update={"candidate_sha256": "8" * 64})
    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=wrong)
    assert caught.value.code == "LOCAL_VALIDATION_SCOPE_MISMATCH"
    evidence = service.run(compilation, scope=_scope(compilation))
    assert evidence[0].artifact.working_directory == "alternate/catalog-root"


def test_artifact_store_is_immutable_and_detects_tamper(tmp_path) -> None:
    repository, compilation = _compiled(tmp_path)
    service, _ledger, artifacts, _issuer = _service(tmp_path, repository, FakeNodeRunner())
    evidence = service.run(compilation, scope=_scope(compilation))
    artifact = evidence[0].artifact
    document = artifacts.read(artifact_sha256=artifact.artifact_sha256).document_bytes
    assert artifacts.append(document).document_bytes == document
    with sqlite3_connect(artifacts.database_path) as connection:
        connection.execute(
            "UPDATE local_validation_artifacts SET document_bytes=? WHERE artifact_sha256=?",
            (b"{}", artifact.artifact_sha256),
        )
        connection.commit()
    with pytest.raises(LocalValidationRunError) as caught:
        artifacts.read(artifact_sha256=artifact.artifact_sha256)
    assert caught.value.code == "LOCAL_ARTIFACT_CORRUPT"


def sqlite3_connect(path: Path):
    import sqlite3

    return sqlite3.connect(path)


def test_node_environment_strips_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross")
    monkeypatch.setenv("DATABASE_URL", "must-not-cross")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    environment = runner_module._node_environment()
    assert "OPENAI_API_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert "must-not-cross" not in environment.values()


def test_candidate_cannot_prepend_a_forged_host_protocol_result() -> None:
    with pytest.raises(LocalValidationRunError) as caught:
        runner_module._invocation_result(
            "workspace/dx/catalog.test.mjs",
            "NODE_TEST",
            NodeCompleted(0, PASS_RESULT + PASS_RESULT),
            authentication_key=b"x" * 32,
        )
    assert caught.value.code == "LOCAL_TEST_RESULT_INVALID"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime is unavailable")
def test_real_node_test_requires_unforgeable_attestation_before_acceptance(tmp_path) -> None:
    repository, compilation = _compiled(tmp_path)
    node_version = subprocess_node_version()
    service, *_ = _service(
        tmp_path,
        repository,
        SubprocessNodeRunner(),
        pins=_pins(
            node_expected_major=int(node_version.split(".")[0]),
            node_minimum_version=node_version,
        ),
    )
    with pytest.raises(LocalValidationRunError, match="^LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED$"):
        service.run(compilation, scope=_scope(compilation))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime is unavailable")
def test_real_node_ordinary_script_cannot_spoof_tap_acceptance(tmp_path) -> None:
    spoof = (
        b"for (const line of ['tests 1','pass 1','fail 0','cancelled 0',"
        b"'skipped 0','todo 0']) console.log('# '+line);"
    )
    repository, compilation = _compiled(tmp_path, script=spoof)
    node_version = subprocess_node_version()
    service, ledger, *_ = _service(
        tmp_path,
        repository,
        SubprocessNodeRunner(),
        pins=_pins(
            node_expected_major=int(node_version.split(".")[0]),
            node_minimum_version=node_version,
        ),
    )
    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))
    assert caught.value.code == "LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED"
    assert ledger.replay(campaign_id="local-validation-campaign") == ()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime is unavailable")
@pytest.mark.parametrize("termination", ["process.exit(0)", "process.exitCode=0"])
def test_real_node_candidate_stdout_and_early_exit_cannot_forge_parent_result(
    tmp_path, termination
) -> None:
    forged_document = base64.b64encode(PASS_RESULT).decode("ascii")
    script = (
        "import {writeSync} from 'node:fs';"
        f"writeSync(1,JSON.stringify({{document:'{forged_document}',mac:'{'0' * 64}'}}));"
        f"{termination};"
    ).encode()
    repository, compilation = _compiled(tmp_path, script=script)
    node_version = subprocess_node_version()
    service, ledger, *_ = _service(
        tmp_path,
        repository,
        SubprocessNodeRunner(),
        pins=_pins(
            node_expected_major=int(node_version.split(".")[0]),
            node_minimum_version=node_version,
        ),
    )

    with pytest.raises(LocalValidationRunError) as caught:
        service.run(compilation, scope=_scope(compilation))

    assert caught.value.code == "LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED"
    assert ledger.replay(campaign_id="local-validation-campaign") == ()


def subprocess_node_version() -> str:
    return SubprocessNodeRunner().version(timeout_seconds=5)


def test_pin_digest_uses_exact_module_and_temporal_dependency_bytes() -> None:
    path = Path(runner_module.__file__)
    assert (
        local_validation_runner_implementation_sha256()
        == hashlib.sha256(
            path.read_bytes() + b"\x00" + path.with_name("temporal.py").read_bytes()
        ).hexdigest()
    )


@pytest.mark.parametrize("filename", ["local_validation_runner.py", "temporal.py"])
def test_pin_digest_changes_when_either_execution_dependency_changes(monkeypatch, filename):
    original_digest = local_validation_runner_implementation_sha256()
    selected = Path(runner_module.__file__).with_name(filename)
    original_read = Path.read_bytes

    def changed_read(path):
        content = original_read(path)
        return content + b"\n# changed dependency\n" if path == selected else content

    monkeypatch.setattr(Path, "read_bytes", changed_read)
    assert local_validation_runner_implementation_sha256() != original_digest


def test_batch_seals_one_expiry_after_all_obligations_not_first_terminal_plus_five_minutes(
    tmp_path,
):
    repository, compilation = _compiled(tmp_path, obligation_count=2, maximum_seconds=450)
    observed = [datetime.now(UTC) - timedelta(seconds=400)]
    node = FakeNodeRunner()

    def advance_on_second(_invocation):
        if len(node.invocations) == 2:
            observed[0] += timedelta(seconds=400)

    node.callback = advance_on_second
    service, ledger, artifacts, _ = _service(
        tmp_path, repository, node, pins=_pins(maximum_global_timeout_seconds=900)
    )
    service.clock = lambda: datetime.now(UTC) if len(node.invocations) >= 2 else observed[0]
    scope = _scope(compilation)
    evidence = service.run(compilation, scope=scope)

    assert len(evidence) == 2
    first, last = (value.artifact for value in evidence)
    assert first.terminal_at + timedelta(minutes=5) < last.terminal_at
    assert (
        first.expires_at
        == last.expires_at
        == min(
            scope.recovery_deadline,
            datetime.fromisoformat(compilation.verified_scope.valid_until.replace("Z", "+00:00")),
        )
    )
    assert first.expires_at > observed[0]
    assert all(
        value.appended_at >= last.terminal_at
        for value in ledger.replay(campaign_id=scope.campaign_id)
    )
    assert all(
        artifacts.read(artifact_sha256=value.artifact.artifact_sha256).appended_at
        >= last.terminal_at
        for value in evidence
    )


def test_batch_expired_after_last_obligation_persists_nothing(tmp_path):
    repository, compilation = _compiled(tmp_path)
    observed = [datetime.now(UTC)]
    scope = _scope(compilation).model_copy(
        update={"recovery_deadline": observed[0] + timedelta(seconds=10)}
    )

    def exhaust_authority(_invocation):
        observed[0] = scope.recovery_deadline

    node = FakeNodeRunner(callback=exhaust_authority)
    service, ledger, *_ = _service(tmp_path, repository, node)
    service.clock = lambda: observed[0]

    with pytest.raises(LocalValidationRunError, match="^LOCAL_CANDIDATE_CAPTURE_EXPIRED$"):
        service.run(compilation, scope=scope)
    assert ledger.replay(campaign_id=scope.campaign_id) == ()
    assert node.invocations[0].timeout_seconds <= 10


def test_run_replays_exact_durable_aggregate_before_returning(tmp_path):
    repository, compilation = _compiled(tmp_path)
    service, _ledger, artifacts, _ = _service(tmp_path, repository, FakeNodeRunner())
    original = artifacts.read

    def no_durable_artifact(*, artifact_sha256):
        original(artifact_sha256=artifact_sha256)
        raise ValueError("artifact vanished after append")

    artifacts.read = no_durable_artifact
    with pytest.raises(LocalValidationRunError, match="^LOCAL_VALIDATION_EVIDENCE_INVALID$"):
        service.run(compilation, scope=_scope(compilation))
