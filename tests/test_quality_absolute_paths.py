import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "config" / "quality-policy.json").read_text())
PATTERNS = [re.compile(pattern) for pattern in POLICY["absolutePathPatterns"]]


@pytest.mark.parametrize(
    "value",
    (
        "D:/repository/source.py",
        "C:\\ProgramData\\application\\state.json",
        "\\\\server\\share\\artifact.json",
        "/home/operator/project",
        "path=/tmp/run-output",
        "/workspace/repository/source.py",
        "file:///var/private/item",
    ),
)
def test_quality_policy_detects_machine_specific_absolute_paths(value: str) -> None:
    assert any(pattern.search(value) for pattern in PATTERNS)


@pytest.mark.parametrize(
    "value",
    (
        "docs/design.md",
        "src/neo_sf_q_intel/api.py",
        "/api/v1/resource",
        "/services/data/v67.0/limits",
        "https://example.test/workspace/resource",
    ),
)
def test_quality_policy_allows_relative_paths_and_api_routes(value: str) -> None:
    assert not any(pattern.search(value) for pattern in PATTERNS)
