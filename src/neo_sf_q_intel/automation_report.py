from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_NAME = "live-llm-healing-report.md"
HTML_REPORT_NAME = "live-llm-healing-report.html"
SUMMARY_NAME = "live-llm-healing-summary.json"
LOG_NAME = "live-llm-healing-log.jsonl"


def load_receipt(path: Path) -> dict[str, Any]:
    body = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(body, dict):
        raise ValueError("RECEIPT_NOT_OBJECT")
    return body


def build_live_llm_healing_summary(
    receipt: dict[str, Any],
    *,
    receipt_path: str,
    published_at_utc: datetime | None = None,
    suite_id: str = "live-llm-business-healing",
    test_id: str = "live-forced-field-llm-healing",
    test_name: str = "Live forced-field LLM locator healing",
    claim: str | None = None,
) -> dict[str, Any]:
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
    now = published_at_utc or datetime.now(UTC)

    field_evidence = [
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
            "healingTier": "LLM_ORDINAL",
        }
        for field in fields
    ]
    passed = (
        receipt.get("status") == "PASSED"
        and receipt.get("browserStatus") == "PASSED"
        and business.get("submitted") is True
        and business.get("successTextMatched") is True
        and persistence.get("matched") is True
    )
    claim_text = claim or (
        "Live browser/provider proof that forced stale locators for selected fields were healed "
        "by the LLM ordinal-ranking path and then verified by deterministic browser action and "
        "persistence readback."
    )

    return {
        "schemaVersion": "1.0.0",
        "reportKind": "AUTOMATION_HEALING_SUITE_REPORT",
        "publishedAtUtc": now.replace(microsecond=0).isoformat(),
        "source": {
            "receiptPath": receipt_path,
            "inputDigest": _string_or_none(receipt.get("inputDigest")),
            "executionIdDigest": _string_or_none(receipt.get("executionIdDigest")),
            "evidencePhase": _string_or_none(receipt.get("evidencePhase")),
            "capabilityId": _string_or_none(receipt.get("capabilityId")),
        },
        "claimBoundary": {
            "claim": claim_text,
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
        "suite": {
            "suiteId": suite_id,
            "status": "PASSED" if passed else "FAILED",
            "testCount": 1,
            "passedCount": 1 if passed else 0,
            "failedCount": 0 if passed else 1,
            "diagnosticOnly": True,
            "releaseEligible": False,
            "tests": [
                {
                    "testId": test_id,
                    "name": test_name,
                    "status": "PASSED" if passed else "FAILED",
                    "stepCount": len(fields) + 2,
                    "healedElementCount": _int_or_none(business.get("healedFieldCount")),
                    "llmHealedElementCount": _int_or_none(business.get("modelAppliedFieldCount")),
                    "deterministicHealedElementCount": 0,
                }
            ],
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
        "steps": [
            {
                "stepId": "profile-and-session",
                "name": "Acquire governed live browser session",
                "status": "PASSED" if receipt.get("browserStatus") == "PASSED" else "FAILED",
                "detail": "Trusted profile and one-shot session handoff completed before action.",
            },
            *[
                {
                    "stepId": f"heal-{field['fieldApiName']}",
                    "name": f"Heal {field['fieldApiName']}",
                    "status": "PASSED" if field["signatureFound"] else "FAILED",
                    "detail": (
                        f"{field['healingTier']} selected ordinal {field['modelOrdinal']} "
                        f"from {field['domCandidateCount']} candidate(s)."
                    ),
                }
                for field in field_evidence
            ],
            {
                "stepId": "submit-and-persist",
                "name": "Submit and verify Salesforce persistence",
                "status": "PASSED" if passed else "FAILED",
                "detail": "Configured success text and independent Salesforce persistence matched.",
            },
        ],
        "fieldEvidence": field_evidence,
        "savedSignatureFields": sorted(signature_saved),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    run = summary["runResult"]
    source = summary["source"]
    boundary = summary["claimBoundary"]
    suite = summary["suite"]
    rows = "\n".join(_field_row(field) for field in summary["fieldEvidence"])
    steps = "\n".join(
        f"| {step['stepId']} | {step['status']} | {step['detail']} |"
        for step in summary["steps"]
    )
    saved = ", ".join(summary["savedSignatureFields"]) or "none"
    field_header = (
        "| Field | Tier | Ordinal | DOM candidates | Context plans | Intent cited | "
        "Intent fit | Missing context | Confidence | Signature found | Signature saved |\n"
        "|---|---|---:|---:|---:|---|---|---|---:|---|---|"
    )
    return f"""# Live LLM locator-healing showcase report

Published UTC: `{summary["publishedAtUtc"]}`

## Suite result

| Metric | Value |
|---|---|
| Suite | `{suite["suiteId"]}` |
| Suite status | `{suite["status"]}` |
| Tests | `{suite["passedCount"]}/{suite["testCount"]} passed` |
| Diagnostic only | `{suite["diagnosticOnly"]}` |
| Release eligible | `{suite["releaseEligible"]}` |

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

## Step log

| Step | Status | Detail |
|---|---|---|
{steps}

## Field-level healing evidence

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
| HTML report | `Docs/demo-evidence/{HTML_REPORT_NAME}` |

## Sanitization boundary

{boundary["sanitization"]}
"""


def render_html(summary: dict[str, Any]) -> str:
    payload = json.dumps(summary, indent=2, sort_keys=True).replace("</", "<\\/")
    metrics_js = """
const metrics = [
  ["Suite", report.suite.status],
  ["Model applied", report.runResult.modelAppliedFieldCount],
  ["Healed", report.runResult.healedFieldCount],
  ["Submitted", report.runResult.submitted],
  ["Persisted", report.runResult.persistenceMatched],
];
document.getElementById("metrics").innerHTML = metrics.map(([label, value]) =>
  `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`
).join("");
document.getElementById("fields").innerHTML = report.fieldEvidence.map((field) =>
  `<tr><td>${field.fieldApiName}</td><td>${field.healingTier}</td>` +
  `<td>${field.modelOrdinal}</td><td>${field.domCandidateCount}</td>` +
  `<td>${field.intentFit}</td><td>${field.missingContext}</td>` +
  `<td>${field.confidenceMilli}</td>` +
  `<td>${field.signatureFound ? "found" : "missing"}</td></tr>`
).join("");
document.getElementById("steps").innerHTML = report.steps.map((step) =>
  `<tr><td>${step.stepId}</td>` +
  `<td class="${step.status === "PASSED" ? "pass" : ""}">${step.status}</td>` +
  `<td>${step.detail}</td></tr>`
).join("");
document.getElementById("not-claimed").innerHTML = report.claimBoundary.notClaimed.map((item) =>
  `<li>${item}</li>`
).join("");
"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Neo live LLM healing report</title>
  <style>
    body {{
      font-family: Inter, Arial, sans-serif;
      margin: 0;
      color: #20302d;
      background: #f6f8f5;
    }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px; }}
    .hero, .card {{
      background: #fff;
      border: 1px solid #dce4de;
      border-radius: 18px 6px;
      padding: 24px;
      margin-bottom: 18px;
    }}
    h1, h2 {{ font-family: Georgia, serif; margin: 0 0 12px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }}
    .metric {{ background: #eef7f3; border-radius: 14px 4px; padding: 14px; }}
    .metric span {{ display: block; color: #63726d; font-size: 12px; text-transform: uppercase; }}
    .metric strong {{ font-size: 28px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      border-bottom: 1px solid #dce4de;
      padding: 10px;
      text-align: left;
      font-size: 14px;
    }}
    th {{ color: #63726d; font-size: 12px; text-transform: uppercase; }}
    .pass {{ color: #087f70; font-weight: 800; }}
    .note {{ color: #63726d; }}
    code {{ background: #edf2ed; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <h1>Live LLM locator-healing showcase</h1>
    <p id="claim"></p>
    <div class="metrics" id="metrics"></div>
  </section>
  <section class="card">
    <h2>Field-level healing</h2>
    <table>
      <thead>
        <tr>
          <th>Field</th><th>Tier</th><th>Ordinal</th><th>Candidates</th>
          <th>Intent</th><th>Missing</th><th>Confidence</th><th>Signature</th>
        </tr>
      </thead>
      <tbody id="fields"></tbody>
    </table>
  </section>
  <section class="card">
    <h2>Step log</h2>
    <table>
      <thead><tr><th>Step</th><th>Status</th><th>Detail</th></tr></thead>
      <tbody id="steps"></tbody>
    </table>
  </section>
  <section class="card note">
    <h2>Claim boundary</h2>
    <ul id="not-claimed"></ul>
    <p id="sanitization"></p>
  </section>
</main>
<script id="report-data" type="application/json">{payload}</script>
<script>
const report = JSON.parse(document.getElementById("report-data").textContent);
document.getElementById("claim").textContent = report.claimBoundary.claim;
{metrics_js}
document.getElementById("sanitization").textContent = report.claimBoundary.sanitization;
</script>
</body>
</html>
"""


def jsonl_events(summary: dict[str, Any]) -> str:
    source = summary["source"]
    events: list[dict[str, Any]] = [
        {
            "event": "automation_healing_suite_observed",
            "suiteId": summary["suite"]["suiteId"],
            "status": summary["suite"]["status"],
            "capabilityId": source["capabilityId"],
            "evidencePhase": source["evidencePhase"],
            "inputDigest": source["inputDigest"],
            "executionIdDigest": source["executionIdDigest"],
        }
    ]
    for step in summary["steps"]:
        events.append({"event": "automation_step", **step})
    for field in summary["fieldEvidence"]:
        events.append({"event": "element_healing_detail", **field})
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


def publish_report(
    summary: dict[str, Any],
    *,
    output_dir: Path,
    dashboard_data_path: Path | None = None,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    (output_dir / SUMMARY_NAME).write_text(summary_text, encoding="utf-8")
    (output_dir / REPORT_NAME).write_text(render_markdown(summary), encoding="utf-8")
    (output_dir / HTML_REPORT_NAME).write_text(render_html(summary), encoding="utf-8")
    (output_dir / LOG_NAME).write_text(jsonl_events(summary), encoding="utf-8")
    if dashboard_data_path is not None:
        dashboard_data_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard_summary = dashboard_projection(summary)
        dashboard_data_path.write_text(
            json.dumps(dashboard_summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    result = {
        "report": str(output_dir / REPORT_NAME),
        "html": str(output_dir / HTML_REPORT_NAME),
        "summary": str(output_dir / SUMMARY_NAME),
        "log": str(output_dir / LOG_NAME),
    }
    if dashboard_data_path is not None:
        result["dashboardData"] = str(dashboard_data_path)
    return result


def dashboard_projection(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a runtime-dashboard-safe projection with scenario literals removed."""

    projected = json.loads(json.dumps(summary))
    field_aliases = {
        field["fieldApiName"]: f"field-{index + 1:02d}"
        for index, field in enumerate(projected.get("fieldEvidence", []))
        if isinstance(field, dict) and isinstance(field.get("fieldApiName"), str)
    }
    projected["claimBoundary"]["claim"] = (
        "Live browser/provider proof that forced stale locators for selected business fields were "
        "healed by the LLM ordinal-ranking path and then verified by deterministic browser action "
        "and persistence readback."
    )
    for test in projected.get("suite", {}).get("tests", []):
        if isinstance(test, dict):
            test["testId"] = "live-forced-field-llm-healing"
            test["name"] = "Live forced-field LLM locator healing"
    for field in projected.get("fieldEvidence", []):
        if isinstance(field, dict):
            field_name = field.get("fieldApiName")
            field["fieldApiName"] = field_aliases.get(field_name, "field")
    projected["savedSignatureFields"] = [
        field_aliases.get(field, f"signature-{index + 1:02d}")
        for index, field in enumerate(projected.get("savedSignatureFields", []))
    ]
    for step in projected.get("steps", []):
        if not isinstance(step, dict):
            continue
        for original, alias in field_aliases.items():
            step["stepId"] = str(step.get("stepId", "")).replace(original, alias)
            step["name"] = str(step.get("name", "")).replace(original, alias)
            step["detail"] = str(step.get("detail", "")).replace(original, alias)
    return projected


def _field_row(field: dict[str, Any]) -> str:
    return (
        "| {fieldApiName} | {healingTier} | {modelOrdinal} | {domCandidateCount} | "
        "{contextPlanCount} | {intentCited} | {intentFit} | {missingContext} | "
        "{confidenceMilli} | {signatureFound} | {signatureSaved} |"
    ).format(**field)


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
