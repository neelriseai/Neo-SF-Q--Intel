from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.salesforce_source import SourceContractError, load_salesforce_source
from neo_sf_q_intel.subprocess_environment import build_subprocess_environment

Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _tool_check(
    name: str,
    arguments: list[str],
    *,
    runner: Runner | None = None,
    environment: Mapping[str, str] | None = None,
    which: Which | None = None,
) -> Check:
    executable = (which or shutil.which)(name)
    if not executable:
        return Check(name, False, "not found on PATH")
    completed = (runner or subprocess.run)(
        [executable, *arguments],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        env=build_subprocess_environment(
            environment,
            controls=(
                {
                    "SF_AUTOUPDATE_DISABLE": "true",
                    "SF_DISABLE_TELEMETRY": "true",
                }
                if name.casefold() == "sf"
                else None
            ),
        ),
    )
    detail = (completed.stdout or completed.stderr).strip().splitlines()[0]
    return Check(name, completed.returncode == 0, detail)


def _minimum_version(check: Check, minimum: tuple[int, ...]) -> Check:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", check.detail)
    if not check.passed or not match:
        return check
    actual = tuple(int(part or 0) for part in match.groups())
    required = ".".join(str(part) for part in minimum)
    return Check(
        check.name,
        actual >= minimum,
        check.detail if actual >= minimum else f"{check.detail}; requires >= {required}",
    )


def _hook_check(
    repository_root: Path,
    *,
    runner: Runner | None = None,
    environment: Mapping[str, str] | None = None,
) -> Check:
    completed = (runner or subprocess.run)(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        env=build_subprocess_environment(environment),
    )
    configured = completed.stdout.strip().replace("\\", "/")
    return Check(
        "review-hook",
        completed.returncode == 0 and configured == ".githooks",
        configured or "not configured; run: git config core.hooksPath .githooks",
    )


def collect_checks(
    settings: Settings,
    repository_root: Path,
    *,
    runner: Runner | None = None,
    environment: Mapping[str, str] | None = None,
    which: Which | None = None,
) -> list[Check]:
    checks = [
        Check(
            "python",
            sys.version_info[:2] >= (3, 14),
            sys.version.split()[0],
        ),
        _minimum_version(
            _tool_check(
                "node",
                ["--version"],
                runner=runner,
                environment=environment,
                which=which,
            ),
            (26, 0, 0),
        ),
        _minimum_version(
            _tool_check(
                "sf",
                ["--version"],
                runner=runner,
                environment=environment,
                which=which,
            ),
            tuple(int(part) for part in settings.sf_min_cli_version.split(".")),
        ),
        _hook_check(repository_root, runner=runner, environment=environment),
    ]
    try:
        source = load_salesforce_source(
            settings.resolved_salesforce_root(repository_root),
            minimum_contract_version=settings.source_min_contract_version,
            required_capabilities=settings.required_capabilities,
            ontology_path=settings.resolved_canonical_ontology_path(repository_root),
            source_profile_path=settings.resolved_source_graph_profile_path(repository_root),
            expected_source_profile_sha256=settings.require_source_profile_sha256(),
        )
        checks.append(Check("salesforce-source", True, source.snapshot_id))
    except (SourceContractError, ValueError) as exc:
        checks.append(Check("salesforce-source", False, str(exc)))
    checks.append(
        Check(
            "postgresql-config",
            settings.database_url is not None,
            "configured" if settings.database_url else "DATABASE_URL is not configured",
        )
    )
    return checks


def main() -> None:
    settings = Settings(allow_llm=False)
    checks = collect_checks(settings, Path.cwd())
    print(json.dumps([asdict(check) for check in checks], indent=2))
    if not all(check.passed for check in checks if check.name != "postgresql-config"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
