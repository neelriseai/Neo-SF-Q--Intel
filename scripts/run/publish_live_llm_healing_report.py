from __future__ import annotations

import argparse
import json
from pathlib import Path

from neo_sf_q_intel.automation_report import (
    build_live_llm_healing_summary,
    load_receipt,
    publish_report,
)

DEFAULT_RECEIPT = Path(".runtime/live-llm-business-healing/live-business-action-llm.json")
DEFAULT_OUTPUT_DIR = Path("Docs/demo-evidence")
DEFAULT_DASHBOARD_DATA = Path("apps/web/src/data/live-llm-healing-summary.json")
CLAIM = (
    "Live Salesforce browser/provider proof that forced stale locators for the Strategic Deal "
    "Workbench fields were healed by the LLM ordinal-ranking path and then verified by "
    "deterministic browser action and Salesforce persistence readback."
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a sanitized live LLM locator-healing showcase report."
    )
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dashboard-data", type=Path, default=DEFAULT_DASHBOARD_DATA)
    args = parser.parse_args()

    receipt = load_receipt(args.receipt)
    summary = build_live_llm_healing_summary(
        receipt,
        receipt_path=".runtime/live-llm-business-healing/live-business-action-llm.json",
        test_id="live-workbench-forced-three-field-llm-healing",
        test_name="Live Workbench forced three-field LLM locator healing",
        claim=CLAIM,
    )
    published = publish_report(
        summary,
        output_dir=args.output_dir,
        dashboard_data_path=args.dashboard_data,
    )
    print(
        json.dumps(
            {
                "status": "PUBLISHED",
                "sourceReceipt": str(args.receipt),
                **published,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
