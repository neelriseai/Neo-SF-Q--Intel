from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_RECEIPT = Path(".runtime/live-llm-business-healing/live-business-action-llm.json")
DEFAULT_OUTPUT_DIR = Path("Docs/demo-evidence")
REPORT_NAME = "live-llm-healing-report.md"
SUMMARY_NAME = "live-llm-healing-summary.json"
LOG_NAME = "live-llm-healing-log.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish a sanitized live LLM locator-healing showcase report."
    )
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    receipt = _load_json(args.receipt)
    summary = _summary(receipt)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / SUMMARY_NAME).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / REPORT_NAME).write_text(_markdown(summary), encoding="utf-8")
    (args.output_dir / LOG_NAME).write_text(_jsonl_events(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "log": str(args.output_dir / LOG_NAME),
                "status": "PUBLISHED",
                "report": str(args.output_dir / REPORT_NAME),
                "summary": str(args.output_dir / SUMMARY_NAME),
                "sourceReceipt": str(args.receipt),
            },
            sort_keys=True,
        )
    )
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(body, dict):
        raise ValueError("RECEIPT_NOT_OBJECT")
    return body


def _summary(receipt: dict[str, Any]) -> dict[str, Any]:
    business = _object(receipt.get("businessAction"))
    persistence = _object(receipt.get("persistence"))
    fields = _strings(business.get("modelAttemptFields"))
    intent_fits = _field_map(_strings(business.get("modelIntentFits")))
    missing_contexts = _field_map(_strings(business.get("modelMissingContexts")))
    confidence = _field_number_map(fields, _numbers(business.get("modelConfidenceMillis")))
    dom_candidates = _field_number_map(fields, _numbers(business.get("modelDomCandidateCounts")))
    ordinals = _field_number_map(fields, _numbers(business.get("modelCandidateOrdinals")))
    context_plans = _field_number_map(fields, _numbers(business.get("modelContextPlanCounts")))
    signature_found = set(_strings(business.get("signatureLookupFoundFields")))
    signature_saved = set(_strings(business.get("signatureSavedFields")))
    intent_cited = set(_strings(business.get("modelIntentCitedFields")))

    return {
        "schemaVersion": "1.0.0",
        "publishedAtUtc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": {
            "receiptPath": ".runtime/live-llm-business-healing/live-business-action-llm.json",
            "inputDigest": _string_or_none(receipt.get("inputDigest")),
            "executionIdDigest": _string_or_none(receipt.get("executionIdDigest")),
            "evidencePhase": _string_or_none(receipt.get("evidencePhase")),
            "capabilityId": _string_or_none(receipt.get("capabilityId")),
        },
        "claimBoundary": {
            "claim": (
                "Live Salesforce browser/provider proof that forced stale locators for three "
                "Workbench fields were healed by the LLM ordinal-ranking path and then verified "
                "by deterministic browser action and Salesforce persistence readback."
            ),
            "notClaimed": [
                "full live campaign acceptance",
                "release eligibility",
                "proof that a deployed metadata/UI mutation caused the drift",
                "raw DOM or raw Salesforce record values",
            ],
            "sanitization": (
                "Report includes only field API names, counts, booleans, statuses, confidence "
                "numbers and one-way digests from the sanitized receipt."
            ),
        },
        "runResult": {
            "status": _string_or_none(receipt.get("status")),
            "browserStatus": _string_or_none(receipt.get("browserStatus")),
            "forcedModelFieldCount": _int_or_none(receipt.get("forcedModelFieldCount")),
            "modelProposalCount": _int_or_none(business.get("modelProposalCount")),
            "modelAppliedFieldCount": _int_or_none(business.get("modelAppliedFieldCount")),
            "healedFieldCount": _int_or_none(business.get("healedFieldCount")),
            "abstainedFieldCount": _int_or_none(business.get("abstainedFieldCount")),
            "submitted": _bool_or_none(business.get("submitted")),
            "successTextMatched": _bool_or_none(business.get("successTextMatched")),
            "persistenceMatched": _bool_or_none(persistence.get("matched")),
            "errorCode": _string_or_none(receipt.get("errorCode")),
            "modelRejectionCodes": _strings(business.get("modelRejectionCodes")),
        },
        "fieldEvidence": [
            {
                "fieldApiName": field,
                "llmAttempted": True,
                "modelOrdinal": ordinals.get(field),
                "domCandidateCount": dom_candidates.get(field),
                "contextPlanCount": context_plans.get(field),
                "intentCited": field in intent_cited,
                "intentFit": intent_fits.get(field),
                "missingContext": missing_contexts.get(field),
                "confidenceMilli": confidence.get(field),
                "signatureFound": field in signature_found,
                "signatureSaved": field in signature_saved,
            }
            for field in fields
        ],
        "savedSignatureFields": sorted(signature_saved),
    }


def _markdown(summary: dict[str, Any]) -> str:
    run = summary["runResult"]
    source = summary["source"]
    boundary = summary["claimBoundary"]
    fields = summary["fieldEvidence"]
    rows = "\n".join(
        "| {fieldApiName} | {modelOrdinal} | {domCandidateCount} | {contextPlanCount} | "
        "{intentCited} | {intentFit} | {missingContext} | {confidenceMilli} | "
        "{signatureFound} | {signatureSaved} |".format(**field)
        for field in fields
    )
    field_header = (
        "| Field | Ordinal | DOM candidates | Context plans | Intent cited | Intent fit | "
        "Missing context | Confidence | Signature found | Signature saved |\n"
        "|---|---:|---:|---:|---|---|---|---:|---|---|"
    )
    saved = ", ".join(summary["savedSignatureFields"]) or "none"
    return f"""# Live LLM locator-healing showcase report

Published UTC: `{summary["publishedAtUtc"]}`

## Executive result

| Metric | Value |
|---|---|
| Status | `{run["status"]}` |
| Browser status | `{run["browserStatus"]}` |
| Forced LLM fields | `{run["forcedModelFieldCount"]}` |
| Model proposals | `{run["modelProposalCount"]}` |
| Model-applied fields | `{run["modelAppliedFieldCount"]}` |
| Healed fields | `{run["healedFieldCount"]}` |
| Abstained fields | `{run["abstainedFieldCount"]}` |
| Submitted | `{run["submitted"]}` |
| Success text matched | `{run["successTextMatched"]}` |
| Salesforce persistence matched | `{run["persistenceMatched"]}` |
| Error code | `{run["errorCode"]}` |
| Model rejection codes | `{", ".join(run["modelRejectionCodes"]) or "none"}` |

## What this proves

{boundary["claim"]}

## What this does not claim

{_bullets(boundary["notClaimed"])}

## Field-level LLM healing evidence

{field_header}
{rows}

## Signature memory

Refreshed stripped signatures: `{saved}`

## Sanitized evidence pointers

| Evidence | Value |
|---|---|
| Evidence phase | `{source["evidencePhase"]}` |
| Capability | `{source["capabilityId"]}` |
| Input digest | `{source["inputDigest"]}` |
| Execution digest | `{source["executionIdDigest"]}` |
| Runtime receipt | `{source["receiptPath"]}` |
| Structured log | `Docs/demo-evidence/{LOG_NAME}` |

## Sanitization boundary

{boundary["sanitization"]}
"""


def _jsonl_events(summary: dict[str, Any]) -> str:
    source = summary["source"]
    events: list[dict[str, Any]] = [
        {
            "event": "live_llm_healing_run_observed",
            "status": summary["runResult"]["status"],
            "browserStatus": summary["runResult"]["browserStatus"],
            "capabilityId": source["capabilityId"],
            "evidencePhase": source["evidencePhase"],
            "inputDigest": source["inputDigest"],
            "executionIdDigest": source["executionIdDigest"],
        }
    ]
    for field in summary["fieldEvidence"]:
        events.append(
            {
                "event": "field_healed_by_llm_ordinal",
                "fieldApiName": field["fieldApiName"],
                "modelOrdinal": field["modelOrdinal"],
                "domCandidateCount": field["domCandidateCount"],
                "contextPlanCount": field["contextPlanCount"],
                "intentFit": field["intentFit"],
                "missingContext": field["missingContext"],
                "confidenceMilli": field["confidenceMilli"],
                "signatureFound": field["signatureFound"],
                "signatureSaved": field["signatureSaved"],
            }
        )
    events.append(
        {
            "event": "business_action_verified",
            "submitted": summary["runResult"]["submitted"],
            "successTextMatched": summary["runResult"]["successTextMatched"],
            "persistenceMatched": summary["runResult"]["persistenceMatched"],
            "releaseEligible": False,
        }
    )
    return "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _numbers(value: Any) -> list[int]:
    return [item for item in value if isinstance(item, int)] if isinstance(value, list) else []


def _field_map(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        if key and value:
            result[key] = value
    return result


def _field_number_map(fields: list[str], values: list[int]) -> dict[str, int]:
    return {field: values[index] for index, field in enumerate(fields) if index < len(values)}


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


if __name__ == "__main__":
    raise SystemExit(main())
