from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "quality-policy.json"
SCOPE_PATH = ROOT / "config" / "capability-scope.json"


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    message: str


def _repo_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return sorted(path.replace("\\", "/") for path in completed.stdout.splitlines())


def _is_allowed(path: str, prefixes: list[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def _imports(path: Path) -> tuple[set[str], str | None]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return set(), str(exc)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names, None


def _head_scope() -> dict[str, dict] | None:
    completed = subprocess.run(
        ["git", "show", "HEAD:config/capability-scope.json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    body = json.loads(completed.stdout)
    return {item["id"]: item for item in body.get("capabilities", [])}


def _changed_paths() -> set[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    tracked = {item.replace("\\", "/") for item in completed.stdout.splitlines()}
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return tracked | {item.replace("\\", "/") for item in untracked.stdout.splitlines()}


def check_repository() -> list[Finding]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    scope = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
    paths = _repo_files()
    findings: list[Finding] = []
    runtime_roots = policy["runtimeRoots"]
    allowed = policy["scenarioAllowedPaths"]
    exempt = policy["scanExemptPaths"]

    for path in paths:
        if path in exempt:
            continue
        file_path = ROOT / path
        try:
            body = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in policy["absolutePathPatterns"]:
            if re.search(pattern, body):
                findings.append(
                    Finding("ABSOLUTE_PATH", path, f"Matched forbidden path pattern {pattern}")
                )
        for pattern in policy["secretPatterns"]:
            if re.search(pattern, body):
                findings.append(
                    Finding("POSSIBLE_SECRET", path, "Matched a forbidden credential pattern")
                )
        if _is_allowed(path, allowed) or not _is_allowed(path, runtime_roots):
            continue
        for pattern in policy["scenarioLiteralPatterns"]:
            if re.search(pattern, body):
                findings.append(
                    Finding(
                        "SCENARIO_LITERAL",
                        path,
                        f"Runtime code matched scenario-only pattern {pattern}",
                    )
                )

    example_values: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        example_values[key.strip()] = value.strip()
    for key in policy["blankExampleSecrets"]:
        if example_values.get(key):
            findings.append(
                Finding("POPULATED_EXAMPLE_SECRET", ".env.example", f"{key} must be blank")
            )
    if example_values.get("ALLOW_SALESFORCE_WRITES", "").casefold() != "false":
        findings.append(
            Finding(
                "UNSAFE_EXAMPLE_DEFAULT",
                ".env.example",
                "ALLOW_SALESFORCE_WRITES must default to false",
            )
        )

    for path, forbidden in policy["layerImportRules"].items():
        actual, parse_error = _imports(ROOT / path)
        if parse_error:
            findings.append(Finding("UNPARSEABLE_LAYER", path, parse_error))
            continue
        for dependency in sorted(actual & set(forbidden)):
            findings.append(
                Finding(
                    "LAYER_IMPORT",
                    path,
                    f"Layer may not import {dependency}",
                )
            )

    capabilities = scope.get("capabilities", [])
    identifiers = [item.get("id") for item in capabilities]
    if len(identifiers) != len(set(identifiers)):
        findings.append(
            Finding("DUPLICATE_CAPABILITY", "config/capability-scope.json", "IDs must be unique")
        )
    valid_statuses = {"IMPLEMENTED", "FOUNDATION", "NEXT", "DEFERRED"}
    missing_capabilities = set(policy["requiredCapabilityIds"]) - set(identifiers)
    for identifier in sorted(missing_capabilities):
        findings.append(
            Finding(
                "MISSING_CAPABILITY",
                "config/capability-scope.json",
                f"Required capability {identifier} was removed",
            )
        )
    for item in capabilities:
        if item.get("status") not in valid_statuses:
            findings.append(
                Finding(
                    "INVALID_CAPABILITY_STATUS",
                    "config/capability-scope.json",
                    f"{item.get('id')} has an invalid status",
                )
            )
        if not item.get("genericity"):
            findings.append(
                Finding(
                    "MISSING_GENERICITY_PROOF",
                    "config/capability-scope.json",
                    f"{item.get('id')} lacks a genericity statement",
                )
            )
        if item.get("status") == "IMPLEMENTED" and not item.get("verification"):
            findings.append(
                Finding(
                    "UNVERIFIED_IMPLEMENTATION",
                    "config/capability-scope.json",
                    f"{item.get('id')} is IMPLEMENTED without verification",
                )
            )
        if item.get("status") == "IMPLEMENTED":
            cases = item.get("verificationCases", {})
            for case_class in ("positive", "negative", "failure"):
                if not cases.get(case_class):
                    findings.append(
                        Finding(
                            "INCOMPLETE_VERIFICATION_MATRIX",
                            "config/capability-scope.json",
                            f"{item.get('id')} lacks {case_class} verification",
                        )
                    )
        referenced_paths = [*item.get("implementation", []), *item.get("verification", [])]
        for referenced_path in referenced_paths:
            if not (ROOT / referenced_path).is_file():
                findings.append(
                    Finding(
                        "MISSING_CAPABILITY_EVIDENCE",
                        "config/capability-scope.json",
                        f"{item.get('id')} references missing {referenced_path}",
                    )
                )

    head_scope = _head_scope()
    current_scope = {item["id"]: item for item in capabilities if item.get("id")}
    if head_scope is not None:
        rank = {"DEFERRED": 0, "NEXT": 1, "FOUNDATION": 2, "IMPLEMENTED": 3}
        for identifier, previous in head_scope.items():
            current = current_scope.get(identifier)
            if current is None:
                findings.append(
                    Finding(
                        "CAPABILITY_REMOVED",
                        "config/capability-scope.json",
                        f"Capability {identifier} exists on HEAD and may not be deleted",
                    )
                )
            elif rank[current["status"]] < rank[previous["status"]]:
                findings.append(
                    Finding(
                        "CAPABILITY_REGRESSION",
                        "config/capability-scope.json",
                        f"Capability {identifier} regressed from {previous['status']}",
                    )
                )

    changed = _changed_paths()
    controlled_changed = changed & set(policy["scopeControlledPaths"])
    review_changed = any(path.startswith(policy["reviewLedgerRoot"]) for path in changed)
    if controlled_changed and not review_changed:
        findings.append(
            Finding(
                "MISSING_SCOPE_REVIEW",
                ", ".join(sorted(controlled_changed)),
                "Scope/policy changes require a review-ledger change",
            )
        )
    return findings


def _knowledge_check() -> Finding | None:
    completed = subprocess.run(
        [sys.executable, "scripts/catalog/build_project_index.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode == 0:
        return None
    detail = (completed.stdout or completed.stderr).strip()
    return Finding("STALE_KNOWLEDGE", "knowledge/", detail)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-knowledge", action="store_true")
    args = parser.parse_args()
    findings = check_repository()
    if not args.skip_knowledge:
        knowledge_finding = _knowledge_check()
        if knowledge_finding:
            findings.append(knowledge_finding)
    if args.json:
        print(json.dumps([asdict(finding) for finding in findings], indent=2))
    elif findings:
        for finding in findings:
            print(f"{finding.code}: {finding.path}: {finding.message}")
    else:
        print("Genericity, layer boundaries, scope manifest and knowledge freshness passed")
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
