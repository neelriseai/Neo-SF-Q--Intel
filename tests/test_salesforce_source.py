import json
from pathlib import Path

import pytest

from neo_sf_q_intel.salesforce_source import SourceContractError, load_salesforce_source


def write_source(
    root: Path, *, contract_status: str = "implemented", snapshots_match: bool = True
) -> None:
    (root / "contracts").mkdir()
    (root / "knowledge").mkdir()
    (root / "contracts" / "agent-interface.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.4.0",
                "apiVersion": "67.0",
                "capabilities": [{"id": "api.custom.rest", "status": contract_status}],
            }
        ),
        encoding="utf-8",
    )
    (root / "knowledge" / "application-graph.json").write_text(
        json.dumps(
            {
                "apiVersion": "67.0",
                "sourceSnapshot": "current",
                "nodes": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "knowledge" / "project-index.json").write_text(
        json.dumps({"sourceSnapshot": "current" if snapshots_match else "stale"}),
        encoding="utf-8",
    )


def test_loads_fresh_implemented_contract(tmp_path: Path) -> None:
    write_source(tmp_path)
    source = load_salesforce_source(tmp_path)
    assert source.snapshot_id == "current"


def test_rejects_stale_graph(tmp_path: Path) -> None:
    write_source(tmp_path, snapshots_match=False)
    with pytest.raises(SourceContractError, match="snapshots disagree"):
        load_salesforce_source(tmp_path)


def test_rejects_unavailable_required_capability(tmp_path: Path) -> None:
    write_source(tmp_path, contract_status="not implemented")
    with pytest.raises(SourceContractError, match="api.custom.rest"):
        load_salesforce_source(tmp_path)
