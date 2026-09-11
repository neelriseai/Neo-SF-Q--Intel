import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from scripts.catalog.build_project_index import _python_imports, _python_module_target

ROOT = Path(__file__).resolve().parents[1]


def test_generated_project_knowledge_is_current() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/catalog/build_project_index.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_index_paths_are_relative_and_graph_edges_are_integral() -> None:
    index = json.loads((ROOT / "knowledge" / "project-index.json").read_text())
    graph = json.loads((ROOT / "knowledge" / "application-graph.json").read_text())
    paths = [item["path"] for item in index["files"]]
    node_ids = {node["id"] for node in graph["nodes"]}

    assert paths
    assert all(not PurePosixPath(path).is_absolute() and ":" not in path for path in paths)
    assert all(edge["from"] in node_ids and edge["to"] in node_ids for edge in graph["edges"])
    assert index["sourceSnapshot"] == graph["sourceSnapshot"]


def test_canonical_reasoning_doc_is_in_read_order_and_audit_is_advisory() -> None:
    index = json.loads((ROOT / "knowledge" / "project-index.json").read_text())
    graph = json.loads((ROOT / "knowledge" / "application-graph.json").read_text())
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert "Docs/10-graph-grounded-agent-reasoning.md" in index["readOrder"]
    assert "Docs/16-deferred-operator-actions.md" in index["readOrder"]
    assert "Docs/18-live-salesforce-demo-execution-contract.md" in index["readOrder"]
    assert "Docs/17-solution-readiness-audit.md" not in index["readOrder"]
    assert nodes["file:Docs/17-solution-readiness-audit.md"]["authority"] == "reference-only"


def test_web_alias_imports_are_present_in_repository_graph() -> None:
    graph = json.loads((ROOT / "knowledge" / "application-graph.json").read_text())
    edges = {(edge["from"], edge["relation"], edge["to"]) for edge in graph["edges"]}

    assert (
        "file:apps/web/src/app/page.tsx",
        "imports",
        "file:apps/web/src/components/foundation-evidence-panel.tsx",
    ) in edges


def test_repository_graph_has_no_duplicate_edges() -> None:
    graph = json.loads((ROOT / "knowledge" / "application-graph.json").read_text())
    edge_keys = [(edge["from"], edge["relation"], edge["to"]) for edge in graph["edges"]]

    assert len(edge_keys) == len(set(edge_keys))


def test_capability_verification_edges_match_scope_registry() -> None:
    scope = json.loads((ROOT / "config" / "capability-scope.json").read_text())
    graph = json.loads((ROOT / "knowledge" / "application-graph.json").read_text())
    indexed_paths = {
        item["path"]
        for item in json.loads((ROOT / "knowledge" / "project-index.json").read_text())["files"]
    }
    expected = {
        (f"capability:{capability['id']}", f"file:{path}")
        for capability in scope["capabilities"]
        for path in capability.get("verification", [])
        if path in indexed_paths
    }
    actual = {
        (edge["from"], edge["to"]) for edge in graph["edges"] if edge["relation"] == "verified_by"
    }

    assert actual == expected


def test_python_module_resolution_requires_exact_package_boundary() -> None:
    assert _python_module_target("neo_sf_q_intelligence.foundation_pipeline") is None
    assert _python_module_target("neo_sf_q_intel.foundation_pipeline") == (
        "src/neo_sf_q_intel/foundation_pipeline.py"
    )


def test_python_import_indexing_fails_closed_on_invalid_syntax() -> None:
    with pytest.raises(ValueError, match="Cannot index Python imports"):
        _python_imports("src/neo_sf_q_intel/broken.py", "def broken(:")
