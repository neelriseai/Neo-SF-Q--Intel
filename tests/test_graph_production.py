from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import neo_sf_q_intel.change_verification as change_verification
import neo_sf_q_intel.graph_production as graph_production
from neo_sf_q_intel.change_verification import (
    LocalGitChangeProducer,
    load_verified_change_policy,
)
from neo_sf_q_intel.edge_envelope import stable_sha256
from neo_sf_q_intel.graph_production import (
    DEFAULT_GRAPH_PRODUCER_POLICY_SHA256,
    GraphDeltaOperation,
    GraphProductionArtifact,
    GraphProductionGapCode,
    GraphProductionInputs,
    LocalTreeGraphProducer,
    TreeSide,
    load_graph_producer_policy,
)
from neo_sf_q_intel.ontology import load_canonical_ontology, load_source_graph_profile
from neo_sf_q_intel.salesforce_graph_adapter import (
    SalesforceGraphAdapterError,
    SalesforceSemanticGraphAdapter,
)

ROOT = Path(__file__).parents[1]
T0 = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
NS = "http://soap.sforce.com/2006/04/metadata"


def _run(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        shell=False,
    ).stdout


def _xml(root: str, body: str = "") -> bytes:
    return f'<{root} xmlns="{NS}">{body}</{root}>'.encode()


def _write(repository: Path, locator: str, content: bytes) -> None:
    target = repository.joinpath(*locator.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir(parents=True)
    _run(repository, "init", "--quiet")
    _run(repository, "config", "user.email", "fixture@example.invalid")
    _run(repository, "config", "user.name", "Fixture")
    _run(repository, "config", "core.autocrlf", "false")
    _write(
        repository,
        "sfdx-project.json",
        json.dumps({"packageDirectories": [{"path": "source-one"}]}).encode(),
    )
    _write(
        repository,
        "source-one/main/default/objects/Entity__c/Entity__c.object-meta.xml",
        _xml("CustomObject", "<label>Private Entity Label</label>"),
    )
    _write(
        repository,
        "source-one/main/default/objects/Entity__c/fields/Link__c.field-meta.xml",
        _xml(
            "CustomField",
            "<fullName>Link__c</fullName><type>Lookup</type>"
            "<referenceTo>Target__c</referenceTo>",
        ),
    )
    _write(
        repository,
        "source-one/main/default/classes/Worker.cls",
        b"public with sharing class Worker { public static void run() {} }\n",
    )
    _write(
        repository,
        "source-one/main/default/classes/Worker.cls-meta.xml",
        _xml("ApexClass", "<apiVersion>67.0</apiVersion><status>Active</status>"),
    )
    _write(repository, "assets/removed.bin", b"removed\x00")
    _run(repository, "add", "--all")
    _run(repository, "commit", "--quiet", "-m", "base")
    _write(
        repository,
        "source-one/main/default/objects/Entity__c/fields/Link__c.field-meta.xml",
        _xml(
            "CustomField",
            "<fullName>Link__c</fullName><type>Lookup</type>"
            "<referenceTo>Alternate__c</referenceTo>",
        ),
    )
    (repository / "assets" / "removed.bin").unlink()
    _write(repository, "assets/added.json", b'{"enabled":true}\n')
    return repository


def _policy():
    return load_graph_producer_policy(
        ROOT / "config" / "graph-producer-policy.json", implementation_root=ROOT
    )


def _system(repository: Path):
    change_policy = load_verified_change_policy(
        ROOT / "config" / "verified-change-policy.json", implementation_root=ROOT
    )
    change_producer = LocalGitChangeProducer(
        project_id="project-fixture",
        expected_repository_root=repository,
        policy=change_policy,
    )
    captured = change_producer.capture(repository)
    assert captured.artifact is not None
    ontology = load_canonical_ontology(
        ROOT / "config" / "ontology" / "canonical-ontology.json"
    )
    profile = load_source_graph_profile(
        ROOT / "config" / "source-profiles" / "salesforce-dx-semantic-graph.json",
        ontology,
    )
    inputs = GraphProductionInputs(
        candidate=captured.artifact,
        change_producer=change_producer,
        repository_hint=repository,
        ontology=ontology,
        profile=profile,
    )
    return LocalTreeGraphProducer(_policy()), inputs


def _codes(result) -> set[GraphProductionGapCode]:
    return {gap.code for gap in result.gaps}


@pytest.fixture(autouse=True)
def _fixed_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(change_verification, "_utc_now", lambda: T0)
    monkeypatch.setattr(graph_production, "_utc_now", lambda: T0 + timedelta(minutes=1))


def test_policy_is_self_hashed_and_pins_every_executable_root() -> None:
    policy = _policy()
    assert policy.sha256 == DEFAULT_GRAPH_PRODUCER_POLICY_SHA256
    producer = ROOT / policy.producer.implementation_locator
    assert graph_production._implementation_sha256(producer.read_bytes()) == (
        policy.producer.implementation_sha256
    )
    for pin in (policy.adapter, policy.adapter_contract, policy.normalizer):
        actual = hashlib.sha256((ROOT / pin.implementation_locator).read_bytes()).hexdigest()
        assert actual == pin.implementation_sha256


def test_complete_tree_produces_semantic_graph_and_edge_tombstones(tmp_path: Path) -> None:
    producer, inputs = _system(_repository(tmp_path))
    result = producer.capture(inputs)
    artifact = result.artifact
    assert artifact is not None
    assert result.file_inventory_input_tree_attested is True
    assert result.supported_semantic_graph_input_tree_attested is True
    assert artifact.base.side is TreeSide.BASE
    assert artifact.candidate.side is TreeSide.CANDIDATE
    assert artifact.base.raw_node_count > artifact.base.input_file_count
    assert artifact.base.raw_edge_count > 0
    assert "object:Entity__c" in {item.node_id for item in artifact.base.nodes}
    assert "field:Entity__c.Link__c" in {item.node_id for item in artifact.base.nodes}
    operations = {(item.entity_type, item.operation, item.entity_id) for item in artifact.delta}
    assert ("NODE", GraphDeltaOperation.DELETE, "object:Target__c") in operations
    assert ("NODE", GraphDeltaOperation.ADD, "object:Alternate__c") in operations
    assert any(
        item.entity_type == "EDGE" and item.operation is GraphDeltaOperation.DELETE
        for item in artifact.delta
    )
    assert {item.entity_type for item in artifact.tombstones} == {"NODE", "EDGE"}
    assert GraphProductionGapCode.SEMANTIC_SOURCE_FAMILY_COVERAGE_INCOMPLETE in _codes(
        result
    )
    assert GraphProductionGapCode.RELEASE_EVIDENCE_MODEL_INCOMPLETE in _codes(result)


def test_all_files_accounted_and_binary_bytes_not_normalized(tmp_path: Path) -> None:
    producer, inputs = _system(_repository(tmp_path))
    artifact = producer.capture(inputs).artifact
    assert artifact is not None
    for side, manifest in (
        (artifact.base, inputs.candidate.base_files),
        (artifact.candidate, inputs.candidate.candidate_files),
    ):
        assert tuple(item.path for item in side.dispositions) == tuple(
            item.path for item in manifest
        )
        assert all(item.disposition == "ACCOUNTED" for item in side.dispositions)
    binary = next(item for item in artifact.base.dispositions if item.path.endswith(".bin"))
    assert binary.content_kind == "BINARY"
    assert binary.normalized_text_sha256 is None


def test_adapter_uses_package_roots_and_ignores_lookalikes() -> None:
    graph = SalesforceSemanticGraphAdapter().extract(
        {
            "sfdx-project.json": b'{"packageDirectories":[{"path":"pkg-a"}]}',
            "pkg-a/main/default/objects/Real__c/Real__c.object-meta.xml": _xml(
                "CustomObject"
            ),
            "examples/objects/Fake__c/Fake__c.object-meta.xml": _xml("CustomObject"),
        },
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    assert "object:Real__c" in {item.node_id for item in graph.nodes}
    assert "object:Fake__c" not in {item.node_id for item in graph.nodes}


def test_package_rename_and_input_order_do_not_change_semantic_projection() -> None:
    def source(root: str) -> dict[str, bytes]:
        return {
            "sfdx-project.json": json.dumps(
                {"packageDirectories": [{"path": root}]}
            ).encode(),
            f"{root}/main/default/objects/Entity__c/Entity__c.object-meta.xml": _xml(
                "CustomObject"
            ),
            f"{root}/main/default/objects/Entity__c/fields/Link__c.field-meta.xml": (
                _xml(
                    "CustomField",
                    "<fullName>Link__c</fullName><referenceTo>Target__c</referenceTo>",
                )
            ),
        }

    adapter = SalesforceSemanticGraphAdapter()
    first_files = source("package-a")
    first = adapter.extract(
        first_files,
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    reversed_first = adapter.extract(
        dict(reversed(tuple(first_files.items()))),
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    renamed = adapter.extract(
        source("package-b"),
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    assert first == reversed_first
    def node_projection(graph):
        return {
            (item.node_id, item.raw_kind, repr(item.attributes), item.evidence_state)
            for item in graph.nodes
        }

    def edge_projection(graph):
        return {
            (item.source_id, item.raw_relation, item.target_id, item.evidence_state)
            for item in graph.edges
            if item.raw_relation != "defined_in"
        }
    assert node_projection(first) == node_projection(renamed)
    assert edge_projection(first) == edge_projection(renamed)
    target = next(item for item in first.nodes if item.node_id == "object:Entity__c")
    assert target.owner_paths == (
        "package-a/main/default/objects/Entity__c/Entity__c.object-meta.xml",
    )


@pytest.mark.parametrize(
    "descriptor",
    [
        b"{}",
        b'{"packageDirectories":[{"path":"../escape"}]}',
        b'{"packageDirectories":[{"path":"Pkg"},{"path":"pkg"}]}',
        b'{"packageDirectories":[{"path":"pkg"},{"path":"pkg/nested"}]}',
    ],
)
def test_invalid_package_roots_fail_closed(descriptor: bytes) -> None:
    with pytest.raises(SalesforceGraphAdapterError):
        SalesforceSemanticGraphAdapter().extract(
            {"sfdx-project.json": descriptor},
            maximum_nodes=10,
            maximum_edges=10,
            maximum_work_units=10000,
        )


def test_unknown_salesforce_family_blocks_attestation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write(
        repository,
        "source-one/main/default/aura/Bundle/Bundle.cmp",
        b"<aura:component/>",
    )
    producer, inputs = _system(repository)
    result = producer.capture(inputs)
    assert result.artifact is None
    assert GraphProductionGapCode.UNSUPPORTED_SOURCE_FAMILY in _codes(result)


def test_false_permissions_do_not_become_authorization_edges() -> None:
    path = "pkg/main/default/permissionsets/ReadNothing.permissionset-meta.xml"
    graph = SalesforceSemanticGraphAdapter().extract(
        {
            "sfdx-project.json": b'{"packageDirectories":[{"path":"pkg"}]}',
            path: _xml(
                "PermissionSet",
                "<objectPermissions><object>Entity__c</object>"
                "<allowRead>false</allowRead></objectPermissions>"
                "<fieldPermissions><field>Entity__c.Secret__c</field>"
                "<readable>false</readable><editable>false</editable></fieldPermissions>"
                "<classAccesses><apexClass>Worker</apexClass>"
                "<enabled>false</enabled></classAccesses>",
            ),
        },
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    assert not {"object_grant", "field_grant", "class_access"} & {
        item.raw_relation for item in graph.edges
    }


def test_comments_and_strings_do_not_create_confirmed_code_facts() -> None:
    graph = SalesforceSemanticGraphAdapter().extract(
        {
            "sfdx-project.json": b'{"packageDirectories":[{"path":"pkg"}]}',
            "pkg/main/default/classes/Worker.cls": (
                b"// @isTest class Fake {}\npublic class Worker { "
                b"String x='SELECT Id FROM Hidden__c'; }"
            ),
            "pkg/main/default/classes/Worker.cls-meta.xml": _xml("ApexClass"),
            "pkg/main/default/lwc/panel/panel.js": (
                b"const decoy = \"from '@salesforce/apex/Fake.run'\";"
            ),
            "pkg/main/default/lwc/panel/panel.html": (
                b"<template><!-- <c-fake> --></template>"
            ),
            "pkg/main/default/lwc/panel/panel.js-meta.xml": _xml(
                "LightningComponentBundle"
            ),
        },
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    worker = next(item for item in graph.nodes if item.node_id == "apex:Worker")
    assert worker.raw_kind == "apex-class"
    assert "object:Hidden__c" not in {item.node_id for item in graph.nodes}
    assert not {"calls_internal_apex", "contains"} & {
        item.raw_relation for item in graph.edges
    }


def test_configuration_values_are_digested_never_plaintext() -> None:
    secret = "password=not-a-real-secret"
    graph = SalesforceSemanticGraphAdapter().extract(
        {
            "sfdx-project.json": b'{"packageDirectories":[{"path":"pkg"}]}',
            "pkg/main/default/customMetadata/Config.Sample.md-meta.xml": _xml(
                "CustomMetadata",
                f"<values><field>Endpoint__c</field><value>{secret}</value></values>",
            ),
        },
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    rendered = repr(graph)
    assert secret not in rendered
    assert hashlib.sha256(secret.encode()).hexdigest() in rendered


def test_field_identity_and_polymorphic_references_are_exact() -> None:
    adapter = SalesforceSemanticGraphAdapter()
    common = {"sfdx-project.json": b'{"packageDirectories":[{"path":"pkg"}]}'}
    path = "pkg/main/default/objects/Source__c/fields/Owner__c.field-meta.xml"
    graph = adapter.extract(
        {
            **common,
            path: _xml(
                "CustomField",
                "<fullName>Owner__c</fullName><type>Lookup</type>"
                "<referenceTo>User</referenceTo><referenceTo>Group</referenceTo>",
            ),
        },
        maximum_nodes=100,
        maximum_edges=100,
        maximum_work_units=100000,
    )
    references = {
        item.target_id for item in graph.edges if item.raw_relation == "references"
    }
    assert references == {"object:Group", "object:User"}
    with pytest.raises(SalesforceGraphAdapterError):
        adapter.extract(
            {
                **common,
                path: _xml("CustomField", "<fullName>Different__c</fullName>"),
            },
            maximum_nodes=100,
            maximum_edges=100,
            maximum_work_units=100000,
        )


def test_format_only_change_updates_file_evidence_not_semantic_entity(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    locator = "source-one/main/default/objects/Entity__c/Entity__c.object-meta.xml"
    _write(
        repository,
        locator,
        (
            f'<CustomObject xmlns="{NS}">\n'
            "  <label>Private Entity Label</label>\n"
            "</CustomObject>\n"
        ).encode(),
    )
    producer, inputs = _system(repository)
    artifact = producer.capture(inputs).artifact
    assert artifact is not None
    changed = {item.entity_id for item in artifact.delta}
    assert f"file:{locator}" in changed
    assert "object:Entity__c" not in changed


def test_current_verifier_rejects_rehashed_nested_tamper(tmp_path: Path) -> None:
    producer, inputs = _system(_repository(tmp_path))
    artifact = producer.capture(inputs).artifact
    assert artifact is not None
    document = artifact.model_dump(mode="json")
    document["candidate"]["raw_graph_sha256"] = "f" * 64
    candidate_body = dict(document["candidate"])
    candidate_body.pop("side_receipt_sha256")
    document["candidate"]["side_receipt_sha256"] = stable_sha256(candidate_body)
    artifact_body = dict(document)
    artifact_body.pop("receipt_sha256")
    document["receipt_sha256"] = stable_sha256(artifact_body)
    tampered = GraphProductionArtifact.model_validate(document)
    rejected = producer.verify(tampered, inputs)
    assert rejected.artifact is None
    assert GraphProductionGapCode.CAPTURE_TAMPERED in _codes(rejected)


def test_model_rejects_swapped_sides_even_when_rehashed(tmp_path: Path) -> None:
    producer, inputs = _system(_repository(tmp_path))
    artifact = producer.capture(inputs).artifact
    assert artifact is not None
    document = artifact.model_dump(mode="json")
    document["base"], document["candidate"] = document["candidate"], document["base"]
    body = dict(document)
    body.pop("receipt_sha256")
    document["receipt_sha256"] = stable_sha256(body)
    with pytest.raises(ValidationError):
        GraphProductionArtifact.model_validate(document)


def test_repository_mutation_after_capture_fails_closed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    producer, inputs = _system(repository)
    _write(repository, "assets/added.json", b"mutated")
    result = producer.capture(inputs)
    assert result.artifact is None
    assert GraphProductionGapCode.CHANGE_REPLAY_FAILED in _codes(result)


def test_forged_ontology_and_profile_with_stale_declared_hash_fail_closed(
    tmp_path: Path,
) -> None:
    producer, inputs = _system(_repository(tmp_path))
    forged_ontology = inputs.ontology.model_copy(
        update={"node_classes": inputs.ontology.node_classes[:-1]}
    )
    ontology_result = producer.capture(replace(inputs, ontology=forged_ontology))
    assert ontology_result.artifact is None
    assert GraphProductionGapCode.ONTOLOGY_POLICY_MISMATCH in _codes(ontology_result)

    forged_profile = inputs.profile.model_copy(
        update={"node_mappings": inputs.profile.node_mappings[:-1]}
    )
    profile_result = producer.capture(replace(inputs, profile=forged_profile))
    assert profile_result.artifact is None
    assert GraphProductionGapCode.SOURCE_PROFILE_POLICY_MISMATCH in _codes(profile_result)


def test_backward_final_clock_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer, inputs = _system(_repository(tmp_path))
    samples = iter((T0 + timedelta(minutes=2), T0 + timedelta(minutes=1)))
    monkeypatch.setattr(graph_production, "_utc_now", lambda: next(samples))
    result = producer.capture(inputs)
    assert result.artifact is None
    assert GraphProductionGapCode.TIME_AUTHORITY_UNAVAILABLE in _codes(result)


def test_policy_duplicate_key_and_external_pin_fail_closed(tmp_path: Path) -> None:
    original = (ROOT / "config" / "graph-producer-policy.json").read_text()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        original.replace(
            '{\n  "schemaVersion"',
            '{\n  "schemaVersion": "1.0.0",\n  "schemaVersion"',
            1,
        )
    )
    with pytest.raises(graph_production.GraphProductionContractError):
        load_graph_producer_policy(duplicate, implementation_root=ROOT)
    with pytest.raises(graph_production.GraphProductionContractError):
        load_graph_producer_policy(
            ROOT / "config" / "graph-producer-policy.json",
            implementation_root=ROOT,
            expected_sha256="f" * 64,
        )
