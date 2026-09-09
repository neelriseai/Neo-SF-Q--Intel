import json
from pathlib import Path

import pytest

from neo_sf_q_intel.ontology import (
    OntologyContractError,
    SourceEvidenceState,
    contract_sha256,
    load_canonical_ontology,
    load_source_graph_profile,
    normalize_source_graph,
)

ROOT = Path(__file__).parents[1]
ONTOLOGY_PATH = ROOT / "config" / "ontology" / "canonical-ontology.json"
PROFILE_PATH = ROOT / "config" / "source-profiles" / "salesforce-application-graph.json"


EXPECTED_SOURCE_NODE_KINDS = {
    "apex-class",
    "apex-test",
    "apex-trigger",
    "api-use-case",
    "approval-process",
    "capability",
    "component-test",
    "custom-metadata-record",
    "field",
    "file",
    "flow",
    "lightning-app",
    "lightning-component",
    "lightning-page",
    "list-view",
    "locator-use-case",
    "manual-use-case",
    "object",
    "permission-set",
    "persona",
    "presentation-configuration",
    "requirement",
    "saved-report",
    "synthetic-dataset",
    "synthetic-fixture",
    "workflow-field-update",
}
EXPECTED_SOURCE_RELATIONS = {
    "appends",
    "authorizes_parent",
    "calls",
    "calls_internal_apex",
    "changes_locator_for",
    "class_access",
    "configures",
    "contains",
    "covers",
    "creates",
    "defined_in",
    "field_grant",
    "guards_save_of",
    "has_field",
    "implemented_by",
    "instance_of",
    "invokes",
    "lists",
    "manages",
    "object_grant",
    "offers_configuration",
    "opportunity_record_page",
    "reads",
    "reads_dataset",
    "reads_private_summary",
    "reads_user_mode",
    "rechecks",
    "references",
    "relevant_change_triggers",
    "routes_via",
    "tests",
    "tests_configuration",
    "tests_requirement",
    "triggers",
    "uses_dataset",
    "uses_final_or_recall_action",
    "workbench_page",
    "writes",
}


def _load_documents() -> tuple[dict, dict]:
    return (
        json.loads(ONTOLOGY_PATH.read_text(encoding="utf-8")),
        json.loads(PROFILE_PATH.read_text(encoding="utf-8")),
    )


def _write_hashed(path: Path, document: dict) -> None:
    document["sha256"] = contract_sha256(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def _load_default_contracts():
    ontology = load_canonical_ontology(ONTOLOGY_PATH)
    return ontology, load_source_graph_profile(PROFILE_PATH, ontology)


def _trusted_item(**values):
    return {
        "evidenceState": "CONFIRMED",
        "sourceSnapshot": "snapshot-1",
        "sourceHash": "a" * 64,
        "extractorId": "fixture-parser-v1",
        "extractorVersion": "1.0.0",
        "extractorImplementationSha256": "b" * 64,
        "sourceArtifactSha256": "c" * 64,
        "source": "fixtures/source.json",
        **values,
    }


def test_loads_versioned_hashed_contracts_and_exhaustive_source_vocabulary() -> None:
    ontology, profile = _load_default_contracts()

    assert ontology.ontology_id == "change-evidence-core"
    assert ontology.ontology_version == "1.0.0"
    assert profile.ontology.ontology_sha256 == ontology.sha256
    assert profile.source_selector.source_type == "salesforce-application-graph"
    assert set(profile.node_mapping) == EXPECTED_SOURCE_NODE_KINDS
    assert set(profile.relation_mapping) == EXPECTED_SOURCE_RELATIONS
    assert all(not hasattr(item, "severity") for item in ontology.node_classes)
    assert all(not hasattr(item, "propagation") for item in ontology.relation_classes)


def test_recomputed_profile_self_hash_does_not_bypass_external_pin(tmp_path: Path) -> None:
    ontology_doc, profile_doc = _load_documents()
    ontology_path = tmp_path / "ontology.json"
    profile_path = tmp_path / "profile.json"
    _write_hashed(ontology_path, ontology_doc)
    ontology = load_canonical_ontology(ontology_path)
    trusted_digest = profile_doc["sha256"]
    profile_doc["nodeMappings"][0]["canonicalClass"] = "configuration"
    _write_hashed(profile_path, profile_doc)

    with pytest.raises(OntologyContractError, match="trusted digest"):
        load_source_graph_profile(
            profile_path, ontology, expected_sha256=trusted_digest
        )


def test_raw_relation_cannot_borrow_another_mapping_endpoint_signature() -> None:
    ontology, profile = _load_default_contracts()
    graph = {
        "nodes": [
            _trusted_item(id="class:a", kind="apex-class", label="A"),
            _trusted_item(id="object:b", kind="object", label="B"),
        ],
        "edges": [
            _trusted_item(
                id="edge:1",
                **{"from": "class:a", "relation": "reads_dataset", "to": "object:b"},
            )
        ],
    }

    normalized = normalize_source_graph(graph, ontology, profile)

    assert normalized.edges == ()
    assert [gap.code for gap in normalized.mapping_gaps] == ["ILLEGAL_ENDPOINT_SIGNATURE"]


@pytest.mark.parametrize("contract_name", ["ontology", "profile"])
def test_rejects_tampered_contract_content(tmp_path: Path, contract_name: str) -> None:
    ontology_doc, profile_doc = _load_documents()
    ontology_path = tmp_path / "ontology.json"
    profile_path = tmp_path / "profile.json"
    ontology_path.write_text(json.dumps(ontology_doc), encoding="utf-8")
    profile_path.write_text(json.dumps(profile_doc), encoding="utf-8")
    ontology = load_canonical_ontology(ontology_path)

    if contract_name == "ontology":
        ontology_doc["ontologyVersion"] = "1.0.1"
        ontology_path.write_text(json.dumps(ontology_doc), encoding="utf-8")
        with pytest.raises(OntologyContractError, match="digest"):
            load_canonical_ontology(ontology_path)
    else:
        profile_doc["profileVersion"] = "1.0.1"
        profile_path.write_text(json.dumps(profile_doc), encoding="utf-8")
        with pytest.raises(OntologyContractError, match="digest"):
            load_source_graph_profile(profile_path, ontology)


def test_rejects_missing_contract_and_duplicate_json_key(tmp_path: Path) -> None:
    with pytest.raises(OntologyContractError, match="missing"):
        load_canonical_ontology(tmp_path / "missing.json")

    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text('{"schemaVersion":"1.0.0","schemaVersion":"2.0.0"}')
    with pytest.raises(OntologyContractError, match="Duplicate JSON key"):
        load_canonical_ontology(duplicate_path)


def test_rejects_profile_with_missing_or_unknown_ontology_reference(tmp_path: Path) -> None:
    ontology_doc, profile_doc = _load_documents()
    ontology_path = tmp_path / "ontology.json"
    profile_path = tmp_path / "profile.json"
    _write_hashed(ontology_path, ontology_doc)
    ontology = load_canonical_ontology(ontology_path)

    profile_doc["ontology"]["ontologyId"] = "missing-ontology"
    _write_hashed(profile_path, profile_doc)
    with pytest.raises(OntologyContractError, match="does not pin"):
        load_source_graph_profile(profile_path, ontology)

    profile_doc["ontology"] = {
        "ontologyId": ontology.ontology_id,
        "ontologyVersion": ontology.ontology_version,
        "ontologySha256": ontology.sha256,
    }
    profile_doc["nodeMappings"][0]["canonicalClass"] = "unknown-class"
    _write_hashed(profile_path, profile_doc)
    with pytest.raises(OntologyContractError, match="unknown canonical class"):
        load_source_graph_profile(profile_path, ontology)


def test_rejects_unknown_canonical_signature_reference(tmp_path: Path) -> None:
    ontology_doc, _ = _load_documents()
    ontology_doc["relationClasses"][0]["legalEndpoints"][0]["targetClass"] = "unknown"
    path = tmp_path / "ontology.json"
    _write_hashed(path, ontology_doc)

    with pytest.raises(OntologyContractError, match="unknown canonical node class"):
        load_canonical_ontology(path)


@pytest.mark.parametrize(
    "mapping_name,raw_field",
    [("nodeMappings", "rawKind"), ("relationMappings", "rawRelation")],
)
def test_rejects_ambiguous_raw_mappings(tmp_path: Path, mapping_name: str, raw_field: str) -> None:
    ontology_doc, profile_doc = _load_documents()
    ontology_path = tmp_path / "ontology.json"
    profile_path = tmp_path / "profile.json"
    _write_hashed(ontology_path, ontology_doc)
    ontology = load_canonical_ontology(ontology_path)
    duplicate = dict(profile_doc[mapping_name][0])
    duplicate[raw_field] = profile_doc[mapping_name][0][raw_field]
    profile_doc[mapping_name].append(duplicate)
    _write_hashed(profile_path, profile_doc)

    with pytest.raises(OntologyContractError, match="Ambiguous duplicate"):
        load_source_graph_profile(profile_path, ontology)


def test_unknown_source_terms_and_illegal_signature_become_blocking_mapping_gaps() -> None:
    ontology, profile = _load_default_contracts()
    graph = {
        "nodes": [
            _trusted_item(id="known-a", kind="object", label="A"),
            _trusted_item(id="known-b", kind="object", label="B"),
            _trusted_item(id="unknown", kind="vendor-new-kind", label="Unknown"),
        ],
        "edges": [
            _trusted_item(
                id="unknown-relation",
                **{"from": "known-a", "relation": "vendor-new-relation", "to": "known-b"},
            ),
            _trusted_item(
                id="illegal-signature",
                **{"from": "known-a", "relation": "calls", "to": "known-b"},
            ),
        ],
    }

    normalized = normalize_source_graph(graph, ontology, profile)

    assert {gap.code for gap in normalized.mapping_gaps} == {
        "UNMAPPED_NODE_KIND",
        "UNMAPPED_RELATION",
        "ILLEGAL_ENDPOINT_SIGNATURE",
    }
    assert all(gap.blocking for gap in normalized.mapping_gaps)
    assert [node.node_id for node in normalized.nodes] == ["known-a", "known-b"]
    assert normalized.edges == ()


def test_normalization_is_order_independent_and_preserves_raw_terms() -> None:
    ontology, profile = _load_default_contracts()
    nodes = [
        _trusted_item(id="class:b", kind="apex-class", label="B", vendor={"z": 2}),
        _trusted_item(id="class:a", kind="apex-class", label="A", vendor={"a": 1}),
    ]
    edges = [
        _trusted_item(
            id="edge:2",
            **{"from": "class:b", "relation": "rechecks", "to": "class:a"},
        ),
        _trusted_item(
            id="edge:1",
            **{"from": "class:a", "relation": "rechecks", "to": "class:b"},
        ),
    ]

    first = normalize_source_graph(
        {"sourceSnapshot": "snapshot-1", "nodes": nodes, "edges": edges},
        ontology,
        profile,
    )
    second = normalize_source_graph(
        {
            "sourceSnapshot": "snapshot-1",
            "nodes": list(reversed(nodes)),
            "edges": list(reversed(edges)),
        },
        ontology,
        profile,
    )

    assert first == second
    assert first.graph_sha256 == second.graph_sha256
    assert [node.node_id for node in first.nodes] == ["class:a", "class:b"]
    assert first.nodes[0].raw_kind == "apex-class"
    assert first.edges[0].raw_relation == "rechecks"
    assert first.edges[0].canonical_relation == "revalidates"
    assert first.trust_gaps == ()


def test_normalization_never_invents_trust_or_provenance() -> None:
    ontology, profile = _load_default_contracts()
    normalized = normalize_source_graph(
        {
            "sourceSnapshot": "graph-level-snapshot",
            "nodes": [{"id": "node", "kind": "apex-class", "label": "Node"}],
            "edges": [],
        },
        ontology,
        profile,
    )

    node = normalized.nodes[0]
    assert normalized.source_snapshot == "graph-level-snapshot"
    assert node.evidence_state is SourceEvidenceState.UNVERIFIED
    assert node.source_snapshot is None
    assert node.source_hash is None
    assert node.extractor_id is None
    assert {gap.code for gap in normalized.trust_gaps} == {
        "MISSING_EVIDENCE_STATE",
        "MISSING_SOURCE_ARTIFACT",
        "MISSING_SOURCE_SNAPSHOT",
        "MISSING_SOURCE_HASH",
        "MISSING_EXTRACTOR_ID",
    }


def test_edge_envelope_field_names_on_nodes_remain_source_attributes() -> None:
    ontology, profile = _load_default_contracts()
    node = _trusted_item(id="node", kind="apex-class", label="Node")

    normalized = normalize_source_graph({"nodes": [node], "edges": []}, ontology, profile)

    assert normalized.nodes[0].attributes == {
        "extractorImplementationSha256": "b" * 64,
        "extractorVersion": "1.0.0",
        "sourceArtifactSha256": "c" * 64,
    }


def test_profile_cannot_promote_evidence_and_raw_human_confirmation_is_not_trusted(
    tmp_path: Path,
) -> None:
    ontology_doc, profile_doc = _load_documents()
    ontology_path = tmp_path / "ontology.json"
    profile_path = tmp_path / "profile.json"
    _write_hashed(ontology_path, ontology_doc)
    ontology = load_canonical_ontology(ontology_path)
    profile_doc["nodeMappings"][0]["evidenceState"] = "CONFIRMED"
    _write_hashed(profile_path, profile_doc)
    with pytest.raises(OntologyContractError, match="extra"):
        load_source_graph_profile(profile_path, ontology)

    profile = load_source_graph_profile(PROFILE_PATH, ontology)
    human_claim = _trusted_item(
        id="node", kind="apex-class", label="Node", evidenceState="HUMAN_CONFIRMED"
    )
    normalized = normalize_source_graph({"nodes": [human_claim], "edges": []}, ontology, profile)
    node = normalized.nodes[0]
    assert node.raw_evidence_state == "HUMAN_CONFIRMED"
    assert node.evidence_state is SourceEvidenceState.UNVERIFIED
    assert SourceEvidenceState.CONFIRMED not in [node.evidence_state]
    assert [gap.code for gap in normalized.trust_gaps] == ["UNVERIFIED_HUMAN_CONFIRMATION"]

    edge_graph = normalize_source_graph(
        {
            "nodes": [
                _trusted_item(id="a", kind="apex-class", label="A"),
                _trusted_item(id="b", kind="apex-class", label="B"),
            ],
            "edges": [
                _trusted_item(
                    id="e",
                    evidenceState="HUMAN_CONFIRMED",
                    **{"from": "a", "relation": "rechecks", "to": "b"},
                )
            ],
        },
        ontology,
        profile,
    )
    assert edge_graph.edges[0].raw_evidence_state == "HUMAN_CONFIRMED"
    assert edge_graph.edges[0].evidence_state is SourceEvidenceState.UNVERIFIED
    assert [gap.code for gap in edge_graph.trust_gaps] == ["UNVERIFIED_HUMAN_CONFIRMATION"]


def test_renamed_source_profile_produces_equivalent_canonical_topology(
    tmp_path: Path,
) -> None:
    ontology_doc, profile_doc = _load_documents()
    ontology_path = tmp_path / "ontology.json"
    profile_path = tmp_path / "renamed-profile.json"
    _write_hashed(ontology_path, ontology_doc)
    ontology = load_canonical_ontology(ontology_path)
    baseline_profile = load_source_graph_profile(PROFILE_PATH, ontology)

    profile_doc["profileId"] = "renamed-source-graph"
    for item in profile_doc["nodeMappings"]:
        item["rawKind"] = f"renamed.{item['rawKind']}"
    for item in profile_doc["relationMappings"]:
        item["rawRelation"] = f"renamed.{item['rawRelation']}"
    _write_hashed(profile_path, profile_doc)
    renamed_profile = load_source_graph_profile(profile_path, ontology)

    baseline = normalize_source_graph(
        {
            "nodes": [
                _trusted_item(id="a", kind="apex-class", label="A"),
                _trusted_item(id="b", kind="apex-class", label="B"),
            ],
            "edges": [_trusted_item(id="e", **{"from": "a", "relation": "rechecks", "to": "b"})],
        },
        ontology,
        baseline_profile,
    )
    renamed = normalize_source_graph(
        {
            "nodes": [
                _trusted_item(id="a", kind="renamed.apex-class", label="A"),
                _trusted_item(id="b", kind="renamed.apex-class", label="B"),
            ],
            "edges": [
                _trusted_item(id="e", **{"from": "a", "relation": "renamed.rechecks", "to": "b"})
            ],
        },
        ontology,
        renamed_profile,
    )

    def topology(graph):
        node_classes = {node.node_id: node.canonical_class for node in graph.nodes}
        return (
            node_classes,
            [(edge.source_id, edge.canonical_relation, edge.target_id) for edge in graph.edges],
        )

    assert topology(baseline) == topology(renamed)
    assert baseline.graph_sha256 != renamed.graph_sha256
