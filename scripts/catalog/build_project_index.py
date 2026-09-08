from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "knowledge"
INDEX_PATH = KNOWLEDGE_DIR / "project-index.json"
GRAPH_PATH = KNOWLEDGE_DIR / "application-graph.json"
GENERATED = {
    "knowledge/project-index.json",
    "knowledge/application-graph.json",
}
EXCLUDED_SUFFIXES = {".png", ".txt", ".tsbuildinfo", ".winmd", ".zip"}
READ_ORDER = [
    "AGENTS.md",
    "Docs/README.md",
    "Docs/00-product-scope.md",
    "Docs/01-architecture.md",
    "Docs/02-domain-contracts.md",
    "Docs/03-data-memory-and-retrieval.md",
    "Docs/04-agent-orchestration.md",
    "Docs/05-connectors-and-mcp.md",
    "Docs/06-ui-automation-and-healing.md",
    "Docs/07-governance-and-evaluation.md",
    "Docs/08-roadmap-24h.md",
    "Docs/09-demo-scenarios.md",
    "Docs/15-development-assurance-process.md",
    "config/capability-scope.json",
]
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)")
TS_IMPORT = re.compile(r"(?:from\s+|import\s*)[\"']([^\"']+)[\"']")


def _repo_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return sorted(
        path.replace("\\", "/")
        for path in completed.stdout.splitlines()
        if path
        and path.replace("\\", "/") not in GENERATED
        and Path(path).suffix.casefold() not in EXCLUDED_SUFFIXES
    )


def _category(path: str) -> str:
    if path.startswith("src/"):
        return "python-runtime"
    if path.startswith("apps/web/src/"):
        return "web-runtime"
    if path.startswith("packages/browser/src/"):
        return "browser-runtime"
    if path.startswith("tests/") or "/tests/" in path:
        return "test"
    if path.startswith("Docs/"):
        return "canonical-documentation" if path in READ_ORDER else "reference-documentation"
    if path.startswith("config/"):
        return "policy"
    if path.startswith("migrations/"):
        return "persistence"
    return "project"


def _normalized_bytes(path: Path) -> bytes:
    body = path.read_bytes()
    try:
        return body.decode("utf-8").replace("\r\n", "\n").encode()
    except UnicodeDecodeError:
        return body


def _sha256(path: Path) -> str:
    return hashlib.sha256(_normalized_bytes(path)).hexdigest()


def _authority(category: str) -> str:
    if category in {"python-runtime", "web-runtime", "browser-runtime", "policy", "persistence"}:
        return "authoritative-source"
    if category == "canonical-documentation":
        return "canonical-guidance"
    if category == "test":
        return "verification"
    if category == "reference-documentation":
        return "reference-only"
    return "supporting"


def _snapshot(files: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item['path']}:{item['sha256']}\n".encode())
    return digest.hexdigest()


def _resolve_relative(source: str, target: str) -> str | None:
    if target.startswith(("http://", "https://", "mailto:", "#", "/")):
        return None
    candidate = ((ROOT / source).parent / target).resolve()
    try:
        relative = candidate.relative_to(ROOT).as_posix()
    except ValueError:
        return None
    if candidate.is_dir():
        for name in ("index.ts", "index.tsx", "README.md"):
            child = candidate / name
            if child.is_file():
                return child.relative_to(ROOT).as_posix()
    if candidate.is_file():
        return relative
    for suffix in (".py", ".ts", ".tsx", ".json", ".md"):
        suffixed = candidate.with_suffix(suffix)
        if suffixed.is_file():
            return suffixed.relative_to(ROOT).as_posix()
    return None


def _python_imports(path: str, body: str) -> list[str]:
    targets = []
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return targets
    for node in ast.walk(tree):
        module = node.module if isinstance(node, ast.ImportFrom) else None
        if not module or not module.startswith("neo_sf_q_intel"):
            continue
        parts = module.split(".")[1:]
        candidate = ROOT / "src" / "neo_sf_q_intel" / Path(*parts)
        target = candidate.with_suffix(".py")
        if not target.is_file():
            target = candidate / "__init__.py"
        if target.is_file():
            targets.append(target.relative_to(ROOT).as_posix())
    return targets


def _references(path: str) -> list[tuple[str, str]]:
    file_path = ROOT / path
    if file_path.suffix.lower() not in {".py", ".ts", ".tsx", ".md"}:
        return []
    body = file_path.read_text(encoding="utf-8")
    if file_path.suffix == ".py":
        return [(target, "imports") for target in _python_imports(path, body)]
    pattern = MARKDOWN_LINK if file_path.suffix == ".md" else TS_IMPORT
    relation = "references" if file_path.suffix == ".md" else "imports"
    resolved = (_resolve_relative(path, match) for match in pattern.findall(body))
    return [(target, relation) for target in resolved if target]


def build_documents() -> tuple[dict[str, Any], dict[str, Any]]:
    paths = _repo_files()
    files = [
        {"path": path, "category": _category(path), "sha256": _sha256(ROOT / path)}
        for path in paths
    ]
    snapshot = _snapshot(files)
    nodes: list[dict[str, Any]] = [
        {
            "id": f"file:{item['path']}",
            "kind": "file",
            "label": Path(item["path"]).name,
            "source": item["path"],
            "category": item["category"],
            "authority": _authority(item["category"]),
        }
        for item in files
    ]
    edges = []
    known = set(paths)
    for path in paths:
        for target, relation in _references(path):
            if target in known:
                edges.append(
                    {
                        "from": f"file:{path}",
                        "relation": relation,
                        "to": f"file:{target}",
                    }
                )
    scope = json.loads((ROOT / "config" / "capability-scope.json").read_text())
    owners = sorted({item["owner"] for item in scope["capabilities"]})
    nodes.extend(
        {"id": f"owner:{owner}", "kind": "owner", "label": owner, "authority": "policy"}
        for owner in owners
    )
    for capability in scope["capabilities"]:
        capability_id = f"capability:{capability['id']}"
        nodes.append(
            {
                "id": capability_id,
                "kind": "capability",
                "label": capability["id"],
                "status": capability["status"],
                "summary": capability["genericity"],
                "authority": "policy",
            }
        )
        edges.append(
            {
                "from": capability_id,
                "relation": "owned_by",
                "to": f"owner:{capability['owner']}",
            }
        )
        edges.extend(
            {
                "from": capability_id,
                "relation": "implemented_by",
                "to": f"file:{path}",
            }
            for path in capability.get("implementation", [])
            if path in known
        )
        edges.extend(
            {
                "from": capability_id,
                "relation": "verified_by",
                "to": f"file:{path}",
            }
            for path in capability.get("verification", [])
            if path in known
        )
    index = {
        "schemaVersion": "1.0.0",
        "project": "Neo SF Q-Intel",
        "pathBase": "repository-root",
        "sourceSnapshot": snapshot,
        "generatedOutputs": sorted(GENERATED),
        "readOrder": [path for path in READ_ORDER if path in known],
        "capabilities": scope["capabilities"],
        "files": files,
    }
    graph = {
        "schemaVersion": "1.0.0",
        "project": "Neo SF Q-Intel",
        "sourceSnapshot": snapshot,
        "provenance": "Static repository structure and explicit import/reference relationships.",
        "authorization": "Discovery only; graph edges grant no tool or write authority.",
        "nodes": nodes,
        "edges": sorted(edges, key=lambda edge: (edge["from"], edge["relation"], edge["to"])),
    }
    return index, graph


def _serialized(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    index, graph = build_documents()
    expected = {INDEX_PATH: _serialized(index), GRAPH_PATH: _serialized(graph)}
    if args.check:
        stale = [
            path
            for path, body in expected.items()
            if not path.is_file() or path.read_text() != body
        ]
        if stale:
            names = ", ".join(path.relative_to(ROOT).as_posix() for path in stale)
            raise SystemExit(f"Generated knowledge is stale: {names}")
        print(f"Knowledge is current: {index['sourceSnapshot']}")
        return
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    for path, body in expected.items():
        path.write_text(body, encoding="utf-8", newline="\n")
    print(f"Generated {len(index['files'])} files and {len(graph['edges'])} edges")


if __name__ == "__main__":
    main()
