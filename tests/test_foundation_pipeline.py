from __future__ import annotations

import inspect
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import neo_sf_q_intel.foundation_pipeline as foundation_pipeline
from neo_sf_q_intel.change_verification import LocalGitChangeProducer
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.foundation_pipeline import (
    CandidateFoundationEvidence,
    CandidateFoundationPipeline,
    FoundationOutcome,
    FoundationPipelineVerificationError,
    FoundationStage,
    FoundationStageState,
    load_foundation_pipeline_policy,
)
from neo_sf_q_intel.graph_production import LocalTreeGraphProducer
from neo_sf_q_intel.operation_seed import OperationAwareSeedCompiler

ROOT = Path(__file__).resolve().parents[1]
NS = "http://soap.sforce.com/2006/04/metadata"


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
    )


def _write(root: Path, relative: str, content: bytes) -> None:
    target = root / Path(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _object(label: str) -> bytes:
    return (
        f'<CustomObject xmlns="{NS}"><label>{label}</label>'
        "<pluralLabel>Records</pluralLabel></CustomObject>"
    ).encode()


def _repository(
    tmp_path: Path,
    *,
    dirty: bool = True,
    entity_name: str = "Entity__c",
    package_name: str = "package",
) -> Path:
    repository = tmp_path / "host-repository"
    repository.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    _write(
        repository,
        "workspace/dx/sfdx-project.json",
        json.dumps(
            {"packageDirectories": [{"path": package_name, "default": True}]},
            separators=(",", ":"),
        ).encode(),
    )
    _write(
        repository,
        f"workspace/dx/{package_name}/main/default/objects/{entity_name}/{entity_name}.object-meta.xml",
        _object("Initial"),
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "baseline")
    if dirty:
        _write(
            repository,
            f"workspace/dx/{package_name}/main/default/objects/{entity_name}/{entity_name}.object-meta.xml",
            _object("Changed"),
        )
    return repository


def _pipeline(repository: Path) -> CandidateFoundationPipeline:
    return CandidateFoundationPipeline.from_host_configuration(
        project_id="fixture-project",
        repository_root=repository,
        salesforce_app_root=repository / "workspace" / "dx",
        implementation_root=ROOT,
    )


def _measurement(stage, name: str) -> int:  # noqa: ANN001
    return next(item.value for item in stage.measurements if item.name == name)


def test_foundation_composer_policy_and_implementation_are_pinned() -> None:
    policy = load_foundation_pipeline_policy(
        ROOT / "config" / "foundation-pipeline-policy.json",
        implementation_root=ROOT,
    )

    assert policy.sha256 == foundation_pipeline.DEFAULT_FOUNDATION_PIPELINE_POLICY_SHA256
    implementation = ROOT / policy.composer.implementation_locator
    assert foundation_pipeline._implementation_sha256(implementation.read_bytes()) == (
        policy.composer.implementation_sha256
    )


def test_host_owned_pipeline_composes_three_replayed_foundations(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    result = _pipeline(repository).capture_current()

    assert result.evidence.outcome is FoundationOutcome.EXECUTED
    assert result.evidence.foundation_execution_complete is True
    assert result.evidence.evidence_completeness == "INCOMPLETE"
    assert result.evidence.non_authoritative_projection is True
    assert result.evidence.release_eligible is False
    assert result.verified_change is not None
    assert result.produced_graph is not None
    assert result.operation_seeds is not None
    assert tuple(item.stage for item in result.evidence.stages) == (
        FoundationStage.VERIFIED_CHANGE_CAPTURE,
        FoundationStage.TREE_GRAPH_PRODUCTION,
        FoundationStage.OPERATION_SEED_MAPPING,
    )
    assert all(item.state is FoundationStageState.EXECUTED for item in result.evidence.stages)
    change, graph, seeds = result.evidence.stages
    assert graph.input_receipts[0].sha256 == change.output_receipt.sha256
    assert {item.role: item.sha256 for item in seeds.input_receipts} == {
        "graph-production": graph.output_receipt.sha256,
        "verified-change": change.output_receipt.sha256,
    }
    assert _measurement(change, "change_count") == 1
    assert _measurement(graph, "graph_delta_count") > 0
    assert _measurement(seeds, "binding_count") == 1
    assert "RELEASE_EVIDENCE_MODEL_INCOMPLETE" in result.evidence.blocking_gap_codes


def test_capture_current_accepts_no_caller_scope() -> None:
    signature = inspect.signature(CandidateFoundationPipeline.capture_current)
    assert tuple(signature.parameters) == ("self",)


def test_configured_application_root_must_match_discovered_candidate_project(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    pipeline = CandidateFoundationPipeline.from_host_configuration(
        project_id="fixture-project",
        repository_root=repository,
        salesforce_app_root=repository,
        implementation_root=ROOT,
    )

    result = pipeline.capture_current()

    assert result.evidence.outcome is FoundationOutcome.ABSTAINED
    assert tuple(stage.state for stage in result.evidence.stages) == (
        FoundationStageState.EXECUTED,
        FoundationStageState.FAILED,
        FoundationStageState.NOT_RUN,
    )
    assert "CONFIGURED_PROJECT_ROOT_MISMATCH" in result.evidence.blocking_gap_codes
    assert result.verified_change is result.produced_graph is result.operation_seeds is None


def test_runtime_composer_policy_rotation_fails_closed(tmp_path: Path) -> None:
    pipeline = _pipeline(_repository(tmp_path))
    forged = pipeline.policy.model_copy(update={"sha256": "f" * 64})

    result = replace(pipeline, policy=forged).capture_current()

    assert result.evidence.outcome is FoundationOutcome.ABSTAINED
    assert tuple(item.state.value for item in result.evidence.stages) == (
        "FAILED",
        "NOT_RUN",
        "NOT_RUN",
    )
    assert result.verified_change is None


def test_no_change_abstains_and_does_not_promote_partial_artifacts(tmp_path: Path) -> None:
    result = _pipeline(_repository(tmp_path, dirty=False)).capture_current()

    assert result.evidence.outcome is FoundationOutcome.ABSTAINED
    assert [item.state for item in result.evidence.stages] == [
        FoundationStageState.FAILED,
        FoundationStageState.NOT_RUN,
        FoundationStageState.NOT_RUN,
    ]
    assert result.verified_change is None
    assert result.produced_graph is None
    assert result.operation_seeds is None
    assert "NO_CANDIDATE_CHANGES" in result.evidence.stages[0].gap_codes
    assert result.evidence.stages[1].gap_codes == ("UPSTREAM_STAGE_INCOMPLETE",)


def test_graph_failure_stops_seed_stage_without_partial_promotion(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write(
        repository,
        "workspace/dx/package/main/default/aura/Panel/Panel.cmp",
        b"<aura:component/>",
    )
    result = _pipeline(repository).capture_current()

    assert [item.state for item in result.evidence.stages] == [
        FoundationStageState.EXECUTED,
        FoundationStageState.FAILED,
        FoundationStageState.NOT_RUN,
    ]
    assert result.verified_change is None
    assert result.produced_graph is None
    assert result.operation_seeds is None
    assert "UNSUPPORTED_SOURCE_FAMILY" in result.evidence.stages[1].gap_codes


def test_operation_policy_failure_retains_no_runtime_artifact(tmp_path: Path) -> None:
    pipeline = _pipeline(_repository(tmp_path))
    invalid_policy = pipeline.operation_compiler.policy.model_copy(
        update={"maximum_changes": pipeline.operation_compiler.policy.maximum_changes + 1}
    )
    pipeline = replace(
        pipeline,
        operation_compiler=OperationAwareSeedCompiler(invalid_policy),
    )

    result = pipeline.capture_current()

    assert [item.state for item in result.evidence.stages] == [
        FoundationStageState.EXECUTED,
        FoundationStageState.EXECUTED,
        FoundationStageState.FAILED,
    ]
    assert result.evidence.outcome is FoundationOutcome.ABSTAINED
    assert result.operation_seeds is None
    assert "POLICY_ROOT_MISMATCH" in result.evidence.stages[2].gap_codes


def test_projection_is_bounded_and_contains_no_paths_or_source_bytes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    secret = "private-label-that-must-not-be-projected"
    locator = "workspace/dx/package/main/default/objects/Entity__c/Entity__c.object-meta.xml"
    _write(repository, locator, _object(secret))

    result = _pipeline(repository).capture_current()
    rendered = result.evidence.model_dump_json()

    assert result.evidence.foundation_execution_complete
    assert str(repository) not in rendered
    assert locator not in rendered
    assert secret not in rendered
    assert "<CustomObject" not in rendered
    assert len(rendered.encode()) < 32_768


def test_stage_and_chain_models_reject_rehashed_scope_drift(tmp_path: Path) -> None:
    evidence = _pipeline(_repository(tmp_path)).capture_current().evidence
    document = evidence.model_dump(mode="json")
    document["stages"][1]["input_receipts"] = []
    stage_body = dict(document["stages"][1])
    stage_body.pop("evidence_sha256")
    document["stages"][1]["evidence_sha256"] = stable_sha256(stage_body)
    chain_body = dict(document)
    chain_body.pop("chain_sha256")
    document["chain_sha256"] = stable_sha256(chain_body)

    with pytest.raises(ValidationError, match="inputs differ from its exact contract"):
        CandidateFoundationEvidence.model_validate(document)


@pytest.mark.parametrize(
    ("stage_index", "mutation", "message"),
    [
        (
            1,
            lambda stage: stage["input_receipts"].append(
                {"role": "zz-unexpected", "sha256": "a" * 64}
            ),
            "exact contract",
        ),
        (
            2,
            lambda stage: stage["output_receipt"].update({"role": "wrong-output"}),
            "exact contract",
        ),
    ],
)
def test_stage_contract_rejects_rehashed_extra_or_wrong_receipts(
    tmp_path: Path,
    stage_index: int,
    mutation,
    message: str,
) -> None:
    evidence = _pipeline(_repository(tmp_path)).capture_current().evidence
    document = evidence.model_dump(mode="json")
    stage = document["stages"][stage_index]
    mutation(stage)
    stage_body = dict(stage)
    stage_body.pop("evidence_sha256")
    stage["evidence_sha256"] = stable_sha256(stage_body)
    chain_body = dict(document)
    chain_body.pop("chain_sha256")
    document["chain_sha256"] = stable_sha256(chain_body)

    with pytest.raises(ValidationError, match=message):
        CandidateFoundationEvidence.model_validate(document)


def test_chain_model_cannot_omit_global_release_interlock(tmp_path: Path) -> None:
    evidence = _pipeline(_repository(tmp_path)).capture_current().evidence
    document = evidence.model_dump(mode="json")
    for stage in document["stages"]:
        stage["gap_codes"] = [
            code for code in stage["gap_codes"] if code != "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
        ]
        stage_body = dict(stage)
        stage_body.pop("evidence_sha256")
        stage["evidence_sha256"] = stable_sha256(stage_body)
    document["blocking_gap_codes"] = [
        code
        for code in document["blocking_gap_codes"]
        if code != "RELEASE_EVIDENCE_MODEL_INCOMPLETE"
    ]
    chain_body = dict(document)
    chain_body.pop("chain_sha256")
    document["chain_sha256"] = stable_sha256(chain_body)

    with pytest.raises(ValidationError, match="Permanent|interlock"):
        CandidateFoundationEvidence.model_validate(document)


def test_stage_model_cannot_omit_another_permanent_gap_when_rehashed(
    tmp_path: Path,
) -> None:
    evidence = _pipeline(_repository(tmp_path)).capture_current().evidence
    document = evidence.model_dump(mode="json")
    stage = document["stages"][0]
    stage["gap_codes"].remove("CANDIDATE_BUILD_NOT_VERIFIED")
    stage_body = dict(stage)
    stage_body.pop("evidence_sha256")
    stage["evidence_sha256"] = stable_sha256(stage_body)
    document["blocking_gap_codes"].remove("CANDIDATE_BUILD_NOT_VERIFIED")
    chain_body = dict(document)
    chain_body.pop("chain_sha256")
    document["chain_sha256"] = stable_sha256(chain_body)

    with pytest.raises(ValidationError, match="Permanent"):
        CandidateFoundationEvidence.model_validate(document)


def test_renamed_host_directory_does_not_change_control_flow(tmp_path: Path) -> None:
    first = _pipeline(_repository(tmp_path / "one")).capture_current()
    second = _pipeline(_repository(tmp_path / "two")).capture_current()

    assert first.evidence.outcome is second.evidence.outcome is FoundationOutcome.EXECUTED
    assert [item.state for item in first.evidence.stages] == [
        item.state for item in second.evidence.stages
    ]
    assert [
        tuple((value.name, value.value) for value in item.measurements)
        for item in first.evidence.stages
    ] == [
        tuple((value.name, value.value) for value in item.measurements)
        for item in second.evidence.stages
    ]


def test_business_entity_and_package_rename_do_not_change_control_flow(
    tmp_path: Path,
) -> None:
    first = _pipeline(
        _repository(
            tmp_path / "one",
            entity_name="AlphaThing__c",
            package_name="first-package",
        )
    ).capture_current()
    second = _pipeline(
        _repository(
            tmp_path / "two",
            entity_name="OmegaItem__c",
            package_name="second-package",
        )
    ).capture_current()

    assert first.evidence.outcome is second.evidence.outcome is FoundationOutcome.EXECUTED
    assert first.evidence.blocking_gap_codes == second.evidence.blocking_gap_codes
    assert tuple(stage.state for stage in first.evidence.stages) == tuple(
        stage.state for stage in second.evidence.stages
    )
    assert tuple(stage.measurements for stage in first.evidence.stages) == tuple(
        stage.measurements for stage in second.evidence.stages
    )


def test_projection_json_has_only_safe_digest_references(tmp_path: Path) -> None:
    result = _pipeline(_repository(tmp_path)).capture_current()
    document = json.loads(result.evidence.model_dump_json())

    assert document["authority_scope"] == "ANALYSIS_ONLY"
    assert document["release_eligible"] is False
    for stage in document["stages"]:
        assert all(len(item["sha256"]) == 64 for item in stage["input_receipts"])
        if stage["output_receipt"]:
            assert len(stage["output_receipt"]["sha256"]) == 64


@pytest.mark.parametrize(
    ("failure", "expected_states", "expected_code"),
    [
        (
            "change",
            ("FAILED", "NOT_RUN", "NOT_RUN"),
            "CHANGE_STAGE_EXCEPTION",
        ),
        (
            "graph",
            ("EXECUTED", "FAILED", "NOT_RUN"),
            "GRAPH_STAGE_EXCEPTION",
        ),
        (
            "seed-compile",
            ("EXECUTED", "EXECUTED", "FAILED"),
            "SEED_COMPILE_EXCEPTION",
        ),
        (
            "seed-verify",
            ("EXECUTED", "EXECUTED", "FAILED"),
            "SEED_VERIFY_EXCEPTION",
        ),
    ],
)
def test_stage_exceptions_are_sanitized_and_stop_downstream_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
    expected_states: tuple[str, str, str],
    expected_code: str,
) -> None:
    pipeline = _pipeline(_repository(tmp_path))
    sensitive = "token=super-secret C:\\private\\customer\\source.json"
    downstream_calls = 0

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError(sensitive)

    def downstream(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal downstream_calls
        downstream_calls += 1
        raise AssertionError("a dependent stage was called")

    if failure == "change":
        monkeypatch.setattr(LocalGitChangeProducer, "capture", boom)
        monkeypatch.setattr(LocalTreeGraphProducer, "capture", downstream)
    elif failure == "graph":
        monkeypatch.setattr(LocalTreeGraphProducer, "capture", boom)
        monkeypatch.setattr(OperationAwareSeedCompiler, "compile", downstream)
    elif failure == "seed-compile":
        monkeypatch.setattr(OperationAwareSeedCompiler, "compile", boom)
        monkeypatch.setattr(OperationAwareSeedCompiler, "verify", downstream)
    else:
        monkeypatch.setattr(OperationAwareSeedCompiler, "verify", boom)

    result = pipeline.capture_current()
    rendered = result.evidence.model_dump_json()

    assert tuple(item.state.value for item in result.evidence.stages) == expected_states
    assert expected_code in rendered
    assert sensitive not in rendered
    assert "super-secret" not in caplog.text
    assert downstream_calls == 0
    assert result.verified_change is None
    assert result.produced_graph is None
    assert result.operation_seeds is None


def _rehash_candidate(document: dict) -> CandidateFoundationEvidence:
    for stage in document["stages"]:
        stage["gap_codes"] = sorted(set(stage["gap_codes"]))
        stage_body = dict(stage)
        stage_body.pop("evidence_sha256")
        stage["evidence_sha256"] = stable_sha256(stage_body)
    document["blocking_gap_codes"] = sorted(
        {code for stage in document["stages"] for code in stage["gap_codes"]}
    )
    chain_body = dict(document)
    chain_body.pop("chain_sha256")
    document["chain_sha256"] = stable_sha256(chain_body)
    return CandidateFoundationEvidence.model_validate(document)


def test_verify_current_reruns_host_chain_and_returns_only_an_exact_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    monkeypatch.setattr("neo_sf_q_intel.change_verification._utc_now", lambda: fixed)
    monkeypatch.setattr("neo_sf_q_intel.graph_production._utc_now", lambda: fixed)
    monkeypatch.setattr("neo_sf_q_intel.operation_seed._utc_now", lambda: fixed)
    pipeline = _pipeline(_repository(tmp_path))
    candidate = pipeline.capture_current()
    later = fixed + timedelta(seconds=30)
    monkeypatch.setattr("neo_sf_q_intel.change_verification._utc_now", lambda: later)
    monkeypatch.setattr("neo_sf_q_intel.graph_production._utc_now", lambda: later)
    monkeypatch.setattr("neo_sf_q_intel.operation_seed._utc_now", lambda: later)

    verified = pipeline.verify_current(candidate)

    assert verified.evidence.chain_sha256 != candidate.evidence.chain_sha256
    assert verified.verified_change is not None
    assert verified.produced_graph is not None
    assert verified.operation_seeds is not None


def test_verify_current_rejects_fully_rehashed_projection_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline(_repository(tmp_path))
    current = pipeline.capture_current()
    reruns = 0

    def current_capture(_self):  # noqa: ANN001, ANN202
        nonlocal reruns
        reruns += 1
        return current

    monkeypatch.setattr(CandidateFoundationPipeline, "capture_current", current_capture)
    monkeypatch.setattr(
        LocalGitChangeProducer,
        "verify",
        lambda *_args, **_kwargs: SimpleNamespace(artifact=current.verified_change),
    )
    monkeypatch.setattr(
        LocalTreeGraphProducer,
        "verify",
        lambda *_args, **_kwargs: SimpleNamespace(artifact=current.produced_graph),
    )
    monkeypatch.setattr(
        OperationAwareSeedCompiler,
        "verify",
        lambda *_args, **_kwargs: SimpleNamespace(artifact=current.operation_seeds),
    )
    mutations = []

    policy = current.evidence.model_dump(mode="json")
    policy["stages"][1]["policy_version"] = "9.9.9"
    mutations.append(policy)

    measurement = current.evidence.model_dump(mode="json")
    measurement["stages"][0]["measurements"][0]["value"] += 1
    mutations.append(measurement)

    output = current.evidence.model_dump(mode="json")
    output["stages"][2]["output_receipt"]["sha256"] = "b" * 64
    mutations.append(output)

    lineage = current.evidence.model_dump(mode="json")
    lineage["stages"][2]["input_receipts"][1]["sha256"] = "d" * 64
    with pytest.raises(ValidationError, match="not linked to exact upstream outputs"):
        _rehash_candidate(lineage)

    state = current.evidence.model_dump(mode="json")
    state["stages"][2]["state"] = "FAILED"
    state["stages"][2]["local_foundation_stage_complete"] = False
    state["stages"][2]["output_receipt"] = None
    state["outcome"] = "ABSTAINED"
    state["foundation_execution_complete"] = False
    mutations.append(state)

    gap = current.evidence.model_dump(mode="json")
    gap["stages"][2]["gap_codes"].append("FORGED_GAP")
    mutations.append(gap)

    timestamp = current.evidence.model_dump(mode="json")
    timestamp["stages"][0]["evaluated_at"] = "2026-09-10T00:00:00Z"
    mutations.append(timestamp)

    for document in mutations:
        candidate = replace(current, evidence=_rehash_candidate(document))
        with pytest.raises(
            FoundationPipelineVerificationError,
            match="^FOUNDATION_PROJECTION_NOT_CURRENT$",
        ):
            pipeline.verify_current(candidate)

    assert 0 < reruns <= len(mutations)
