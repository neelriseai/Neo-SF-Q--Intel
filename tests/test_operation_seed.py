from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import neo_sf_q_intel.change_verification as change_verification
import neo_sf_q_intel.graph_production as graph_production
import neo_sf_q_intel.operation_seed as operation_seed
from neo_sf_q_intel.change_verification import LocalGitChangeProducer, load_verified_change_policy
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.graph_production import (
    GraphProductionInputs,
    LocalTreeGraphProducer,
    load_graph_producer_policy,
)
from neo_sf_q_intel.ontology import (
    contract_sha256,
    load_canonical_ontology,
    load_source_graph_profile,
)
from neo_sf_q_intel.operation_seed import (
    DEFAULT_OPERATION_SEED_POLICY_SHA256,
    OperationAwareSeedCompiler,
    OperationSeedArtifact,
    OperationSeedGapCode,
    OperationSeedInputs,
    OperationSeedPolicy,
    SemanticSeedOutcome,
    load_operation_seed_policy,
)

ROOT = Path(__file__).parents[1]
T0 = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
NS = "http://soap.sforce.com/2006/04/metadata"


def _run(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        shell=False,
    )


def _write(repository: Path, locator: str, content: bytes) -> None:
    target = repository.joinpath(*locator.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _xml(root: str, body: str = "") -> bytes:
    return f'<{root} xmlns="{NS}">{body}</{root}>'.encode()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _run(repository, "init", "--quiet")
    _run(repository, "config", "user.email", "fixture@example.invalid")
    _run(repository, "config", "user.name", "Fixture")
    _run(repository, "config", "core.autocrlf", "false")
    _write(
        repository,
        "sfdx-project.json",
        json.dumps({"packageDirectories": [{"path": "package"}]}).encode(),
    )
    field = "package/main/default/objects/Entity__c/fields/Link__c.field-meta.xml"
    _write(
        repository,
        field,
        _xml(
            "CustomField",
            "<fullName>Link__c</fullName><type>Lookup</type><referenceTo>Before__c</referenceTo>",
        ),
    )
    deleted = "package/main/default/classes/RemovedWorker.cls"
    _write(repository, deleted, b"public with sharing class RemovedWorker {}\n")
    _write(
        repository,
        "package/main/default/classes/RemovedWorker.cls-meta.xml",
        _xml("ApexClass", "<apiVersion>67.0</apiVersion>"),
    )
    _write(
        repository,
        "package/main/default/objects/After__c/After__c.object-meta.xml",
        _xml("CustomObject", "<label>After</label>"),
    )
    _run(repository, "add", "--all")
    _run(repository, "commit", "--quiet", "-m", "base")
    _write(
        repository,
        field,
        _xml(
            "CustomField",
            "<fullName>Link__c</fullName><type>Lookup</type><referenceTo>After__c</referenceTo>",
        ),
    )
    (repository / deleted).unlink()
    (repository / "package/main/default/classes/RemovedWorker.cls-meta.xml").unlink()
    (repository / "package/main/default/objects/After__c/After__c.object-meta.xml").unlink()
    _write(
        repository,
        "package/main/default/objects/Before__c/Before__c.object-meta.xml",
        _xml("CustomObject", "<label>Before</label>"),
    )
    _write(repository, "notes/context.json", b'{"reason":"fixture"}\n')
    return repository


def _system(repository: Path):
    change_policy = load_verified_change_policy(
        ROOT / "config" / "verified-change-policy.json", implementation_root=ROOT
    )
    change_producer = LocalGitChangeProducer(
        project_id="project-fixture",
        expected_repository_root=repository,
        policy=change_policy,
    )
    change = change_producer.capture(repository).artifact
    assert change is not None
    ontology = load_canonical_ontology(ROOT / "config" / "ontology" / "canonical-ontology.json")
    profile = load_source_graph_profile(
        ROOT / "config" / "source-profiles" / "salesforce-dx-semantic-graph.json",
        ontology,
    )
    graph_inputs = GraphProductionInputs(
        candidate=change,
        change_producer=change_producer,
        repository_hint=repository,
        ontology=ontology,
        profile=profile,
    )
    graph_producer = LocalTreeGraphProducer(
        load_graph_producer_policy(
            ROOT / "config" / "graph-producer-policy.json", implementation_root=ROOT
        )
    )
    graph = graph_producer.capture(graph_inputs).artifact
    assert graph is not None
    compiler = OperationAwareSeedCompiler(
        load_operation_seed_policy(
            ROOT / "config" / "operation-seed-policy.json", implementation_root=ROOT
        )
    )
    inputs = OperationSeedInputs(
        graph_candidate=graph,
        graph_producer=graph_producer,
        graph_inputs=graph_inputs,
    )
    return compiler, inputs


def _codes(result) -> set[OperationSeedGapCode]:
    return {item.code for item in result.gaps}


@pytest.fixture(autouse=True)
def _fixed_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0)
    monkeypatch.setattr(graph_production, "_utc_now", lambda: T0 + timedelta(minutes=1))
    monkeypatch.setattr(operation_seed, "_utc_now", lambda: T0 + timedelta(minutes=2))


def test_policy_is_self_hashed_and_implementation_pinned() -> None:
    policy = load_operation_seed_policy(
        ROOT / "config" / "operation-seed-policy.json", implementation_root=ROOT
    )
    assert policy.sha256 == DEFAULT_OPERATION_SEED_POLICY_SHA256
    assert (
        operation_seed._implementation_sha256(
            (ROOT / policy.compiler.implementation_locator).read_bytes()
        )
        == policy.compiler.implementation_sha256
    )


def test_add_delete_modify_are_side_qualified_and_completely_partitioned(
    tmp_path: Path,
) -> None:
    compiler, inputs = _system(_repository(tmp_path))
    result = compiler.compile(inputs)
    artifact = result.artifact
    assert artifact is not None
    by_path = {item.change.path: item for item in artifact.bindings}
    nonsemantic_add = by_path["notes/context.json"]
    semantic_add = by_path["package/main/default/objects/Before__c/Before__c.object-meta.xml"]
    semantic_delete = by_path["package/main/default/objects/After__c/After__c.object-meta.xml"]
    modify = by_path["package/main/default/objects/Entity__c/fields/Link__c.field-meta.xml"]
    assert tuple(item.side.value for item in nonsemantic_add.file_evidence) == ("CANDIDATE",)
    assert nonsemantic_add.seed_outcome is SemanticSeedOutcome.NO_SEMANTIC_SEED
    assert {item.side.value for item in semantic_add.seeds} == {"CANDIDATE"}
    assert tuple(item.side.value for item in semantic_delete.file_evidence) == ("BASE",)
    assert {item.side.value for item in semantic_delete.seeds} == {"BASE"}
    assert semantic_delete.tombstone_sha256s
    assert {item.side.value for item in modify.seeds} == {
        "BASE",
        "CANDIDATE",
    }
    assert modify.tombstone_sha256s
    same_id = [item for item in modify.seeds if item.node_id == "field:Entity__c.Link__c"]
    assert len(same_id) == 2
    assert {item.side.value for item in same_id} == {"BASE", "CANDIDATE"}
    assert set(artifact.graph_delta_sha256s) == {
        item.delta_sha256 for item in inputs.graph_candidate.delta
    }
    assert not (
        set(artifact.direct_graph_delta_sha256s) & set(artifact.indirect_graph_delta_sha256s)
    )
    assert artifact.change_seed_scope_attested is False
    assert artifact.path_scope_attested is False
    assert OperationSeedGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE in _codes(result)
    assert OperationSeedGapCode.CHANGE_SEED_SCOPE_NOT_ATTESTED in _codes(result)


def test_format_only_semantic_file_still_seeds_declared_entity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    field = "package/main/default/objects/Entity__c/fields/Link__c.field-meta.xml"
    _write(
        repository,
        field,
        (
            f'<CustomField xmlns="{NS}">\n  <fullName>Link__c</fullName>\n'
            "  <type>Lookup</type>\n  <referenceTo>Before__c</referenceTo>\n"
            "</CustomField>\n"
        ).encode(),
    )
    (repository / "package/main/default/classes/RemovedWorker.cls").write_bytes(
        b"public with sharing class RemovedWorker {}\n"
    )
    _write(
        repository,
        "package/main/default/classes/RemovedWorker.cls-meta.xml",
        _xml("ApexClass", "<apiVersion>67.0</apiVersion>"),
    )
    compiler, inputs = _system(repository)
    artifact = compiler.compile(inputs).artifact
    assert artifact is not None
    binding = next(item for item in artifact.bindings if item.change.path == field)
    assert binding.seed_outcome is SemanticSeedOutcome.SEEDED
    assert {item.node_id for item in binding.seeds} >= {"field:Entity__c.Link__c"}


def test_rehashed_binding_tamper_and_current_repository_mutation_fail_closed(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    compiler, inputs = _system(repository)
    artifact = compiler.compile(inputs).artifact
    assert artifact is not None
    document = artifact.model_dump(mode="json")
    document["project_id"] = "different-project"
    body = dict(document)
    body.pop("artifact_sha256")
    document["artifact_sha256"] = stable_sha256(body)
    tampered = OperationSeedArtifact.model_validate(document)
    rejected = compiler.verify(tampered, inputs)
    assert rejected.artifact is None
    assert OperationSeedGapCode.ARTIFACT_TAMPERED in _codes(rejected)

    _write(repository, "notes/context.json", b"mutated")
    changed = compiler.compile(inputs)
    assert changed.artifact is None
    assert OperationSeedGapCode.GRAPH_REPLAY_FAILED in _codes(changed)


def test_policy_and_compiler_rotation_fail_closed(tmp_path: Path) -> None:
    compiler, inputs = _system(_repository(tmp_path))
    forged_policy = compiler.policy.model_copy(update={"sha256": "f" * 64})
    rejected = OperationAwareSeedCompiler(forged_policy).compile(inputs)
    assert rejected.artifact is None
    assert OperationSeedGapCode.POLICY_ROOT_MISMATCH in _codes(rejected)

    forged_graph = replace(
        inputs,
        graph_producer=LocalTreeGraphProducer(
            inputs.graph_producer.policy.model_copy(update={"sha256": "e" * 64})
        ),
    )
    rejected_graph = compiler.compile(forged_graph)
    assert rejected_graph.artifact is None
    assert OperationSeedGapCode.POLICY_ROOT_MISMATCH in _codes(rejected_graph)


def test_total_delta_reference_capacity_rejects_before_binding_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiler, inputs = _system(_repository(tmp_path))
    document = compiler.policy.model_dump(mode="json", by_alias=True)
    document["maximumBindingDeltaReferences"] = 1
    document["sha256"] = contract_sha256(document)
    constrained = OperationSeedPolicy.model_validate(document)
    monkeypatch.setattr(operation_seed, "DEFAULT_OPERATION_SEED_POLICY_SHA256", constrained.sha256)
    rejected = OperationAwareSeedCompiler(constrained).compile(inputs)
    assert rejected.artifact is None
    assert OperationSeedGapCode.CAPACITY_EXCEEDED in _codes(rejected)


def test_time_authority_historical_root_and_later_refresh_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiler, inputs = _system(_repository(tmp_path))
    artifact = compiler.compile(inputs).artifact
    assert artifact is not None

    document = artifact.model_dump(mode="json")
    document["graph_production_receipt_sha256"] = "f" * 64
    body = dict(document)
    body.pop("artifact_sha256")
    document["artifact_sha256"] = stable_sha256(body)
    substituted = OperationSeedArtifact.model_validate(document)
    rejected = compiler.verify(substituted, inputs)
    assert rejected.artifact is None
    assert OperationSeedGapCode.ARTIFACT_TAMPERED in _codes(rejected)

    monkeypatch.setattr(operation_seed, "_utc_now", lambda: T0 + timedelta(minutes=1))
    future = compiler.verify(artifact, inputs)
    assert future.artifact is None
    assert OperationSeedGapCode.ARTIFACT_FROM_FUTURE in _codes(future)

    monkeypatch.setattr(operation_seed, "_utc_now", lambda: T0 + timedelta(minutes=16))
    expired = compiler.verify(artifact, inputs)
    assert expired.artifact is None
    assert OperationSeedGapCode.ARTIFACT_EXPIRED in _codes(expired)

    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0 + timedelta(minutes=3))
    monkeypatch.setattr(graph_production, "_utc_now", lambda: T0 + timedelta(minutes=4))
    monkeypatch.setattr(operation_seed, "_utc_now", lambda: T0 + timedelta(minutes=5))
    refreshed = compiler.verify(artifact, inputs)
    assert refreshed.artifact is not None
    assert refreshed.artifact.evaluated_at == "2026-09-10T08:05:00Z"

    monkeypatch.setattr(operation_seed, "_utc_now", lambda: datetime(2026, 9, 10, 8, 4))
    naive = compiler.compile(inputs)
    assert naive.artifact is None
    assert OperationSeedGapCode.TIME_AUTHORITY_UNAVAILABLE in _codes(naive)

    rollback_samples = iter(
        (
            T0 + timedelta(minutes=6),
            T0 + timedelta(minutes=5),
            T0 + timedelta(minutes=5),
        )
    )
    monkeypatch.setattr(operation_seed, "_utc_now", lambda: next(rollback_samples))
    rollback = compiler.verify(artifact, inputs)
    assert rollback.artifact is None
    assert OperationSeedGapCode.TIME_AUTHORITY_UNAVAILABLE in _codes(rollback)


def test_compile_then_verify_survives_independent_graph_timestamp_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compiler, inputs = _system(_repository(tmp_path))
    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0 + timedelta(minutes=3))
    monkeypatch.setattr(graph_production, "_utc_now", lambda: T0 + timedelta(minutes=4))
    monkeypatch.setattr(operation_seed, "_utc_now", lambda: T0 + timedelta(minutes=5))
    compiled = compiler.compile(inputs)
    assert compiled.artifact is not None
    assert compiled.artifact.graph_production_receipt_sha256 == (
        inputs.graph_candidate.receipt_sha256
    )

    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0 + timedelta(minutes=6))
    monkeypatch.setattr(graph_production, "_utc_now", lambda: T0 + timedelta(minutes=7))
    monkeypatch.setattr(operation_seed, "_utc_now", lambda: T0 + timedelta(minutes=8))
    verified = compiler.verify(compiled.artifact, inputs)

    assert verified.artifact is not None
    assert verified.artifact.evaluated_at == "2026-09-10T08:08:00Z"
