import hashlib
import json
from pathlib import Path

import pytest

from neo_sf_q_intel.ontology import contract_sha256
from neo_sf_q_intel.salesforce_source import SourceContractError, load_salesforce_source


def write_source(
    root: Path,
    *,
    contract_status: str = "implemented",
    snapshots_match: bool = True,
    schema_version: str = "1.4.0",
    graph_schema_version: str = "1.0.0",
) -> str:
    (root / "contracts").mkdir()
    (root / "knowledge").mkdir()
    (root / "contracts" / "agent-interface.json").write_text(
        json.dumps(
            {
                "schemaVersion": schema_version,
                "apiVersion": "67.0",
                "application": "Example Salesforce Application",
                "capabilities": [{"id": "api.custom.rest", "status": contract_status}],
            }
        ),
        encoding="utf-8",
    )
    contract_path = root / "contracts" / "agent-interface.json"
    contract_hash = hashlib.sha256(
        contract_path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
    ).hexdigest()
    inventory = [
        {
            "path": "contracts/agent-interface.json",
            "category": "agent-contract",
            "sha256NormalizedLf": contract_hash,
        }
    ]
    snapshot = hashlib.sha256(json.dumps(inventory, separators=(",", ":")).encode()).hexdigest()
    graph_path = root / "knowledge" / "application-graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "schemaVersion": graph_schema_version,
                "apiVersion": "67.0",
                "application": "Example Salesforce Application",
                "sourceSnapshot": snapshot,
                "nodes": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "knowledge" / "project-index.json").write_text(
        json.dumps(
            {
                "pathBase": "project-root",
                "files": inventory,
                "sourceSnapshot": snapshot if snapshots_match else "stale",
            }
        ),
        encoding="utf-8",
    )
    return hashlib.sha256(graph_path.read_text(encoding="utf-8").encode()).hexdigest()


def test_loads_fresh_implemented_contract(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path)
    source = load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)
    assert source.project_id == "example-salesforce-application"
    assert source.trusted_graph_sha256 == graph_hash


def test_accepts_compatible_newer_contract_minor_version(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path, schema_version="1.5.0")

    source = load_salesforce_source(
        tmp_path,
        expected_graph_sha256=graph_hash,
        minimum_contract_version="1.4.0",
    )

    assert source.contract["schemaVersion"] == "1.5.0"


@pytest.mark.parametrize("graph_schema", ["0.9.0", "2.0.0"])
def test_rejects_graph_schema_outside_source_profile_range(
    tmp_path: Path, graph_schema: str
) -> None:
    graph_hash = write_source(tmp_path, graph_schema_version=graph_schema)

    with pytest.raises(SourceContractError, match="graph schema"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)


def test_rejects_source_profile_for_a_different_adapter(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path)
    profile = json.loads(
        Path("config/source-profiles/salesforce-application-graph.json").read_text(
            encoding="utf-8"
        )
    )
    profile["sourceSelector"]["sourceType"] = "different-graph-adapter"
    profile["sha256"] = contract_sha256(profile)
    profile_path = tmp_path / "wrong-profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(SourceContractError, match="source adapter"):
        load_salesforce_source(
            tmp_path,
            expected_graph_sha256=graph_hash,
            source_profile_path=profile_path,
            expected_source_profile_sha256=profile["sha256"],
        )


def test_rejects_duplicate_semantic_or_trust_json_keys(tmp_path: Path) -> None:
    write_source(tmp_path)
    graph_path = tmp_path / "knowledge" / "application-graph.json"
    graph_path.write_text(
        '{"schemaVersion":"1.0.0","apiVersion":"67.0",'
        '"application":"Example","sourceSnapshot":"first",'
        '"sourceSnapshot":"second","nodes":[],"edges":[]}',
        encoding="utf-8",
    )
    graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()

    with pytest.raises(SourceContractError, match="Duplicate JSON key"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)


def test_rejects_stale_graph(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path, snapshots_match=False)
    with pytest.raises(SourceContractError, match="snapshot"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)


def test_rejects_unavailable_required_capability(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path, contract_status="not implemented")
    with pytest.raises(SourceContractError, match="api.custom.rest"):
        load_salesforce_source(
            tmp_path,
            expected_graph_sha256=graph_hash,
            required_capabilities=("api.custom.rest",),
        )


def test_rejects_graph_edges_with_unknown_nodes(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path)
    graph_path = tmp_path / "knowledge" / "application-graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["edges"] = [{"from": "missing", "relation": "calls", "to": "also-missing"}]
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    with pytest.raises(SourceContractError, match="unknown node"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)


def test_rejects_changed_indexed_source_file(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path)
    contract_path = tmp_path / "contracts" / "agent-interface.json"
    contract_path.write_text(contract_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(SourceContractError, match="stale"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)


def test_rejects_index_path_outside_repository(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path)
    index_path = tmp_path / "knowledge" / "project-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["files"][0]["path"] = "../outside.txt"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(SourceContractError, match="outside its repository"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)


def test_rejects_validly_shaped_graph_content_tampering(tmp_path: Path) -> None:
    graph_hash = write_source(tmp_path)
    graph_path = tmp_path / "knowledge" / "application-graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["nodes"].append({"id": "object:Injected", "kind": "object", "label": "Injected"})
    graph_path.write_text(json.dumps(graph), encoding="utf-8")

    with pytest.raises(SourceContractError, match="trusted digest"):
        load_salesforce_source(tmp_path, expected_graph_sha256=graph_hash)
