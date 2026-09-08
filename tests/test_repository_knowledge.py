import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

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
