"""Generate/check the closed source operation declaration schema; no runtime operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from neo_sf_q_intel.candidate_target_compiler import SourceOperationDeclarations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    path = Path(__file__).resolve().parents[2] / (
        "config/source-profiles/source-operation-declarations.schema.json"
    )
    content = (
        json.dumps(SourceOperationDeclarations.model_json_schema(), indent=2) + "\n"
    ).encode()
    if arguments.check:
        return 0 if path.read_bytes() == content else 1
    path.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
