from __future__ import annotations

from datetime import UTC, datetime

from neo_sf_q_intel.automation_report import (
    build_live_llm_healing_summary,
    dashboard_projection,
    jsonl_events,
    publish_report,
    render_html,
    render_markdown,
)


def test_live_llm_healing_report_exposes_suite_steps_and_element_details() -> None:
    summary = build_live_llm_healing_summary(
        _receipt(),
        receipt_path=".runtime/live-llm-business-healing/live-business-action-llm.json",
        published_at_utc=datetime(2026, 9, 13, 5, 0, 0, tzinfo=UTC),
    )

    assert summary["reportKind"] == "AUTOMATION_HEALING_SUITE_REPORT"
    assert summary["suite"]["status"] == "PASSED"
    assert summary["suite"]["passedCount"] == 1
    assert summary["suite"]["releaseEligible"] is False
    assert [item["fieldApiName"] for item in summary["fieldEvidence"]] == [
        "Name",
        "Strategic_Deal__c",
        "StageName",
    ]
    assert [item["healingTier"] for item in summary["fieldEvidence"]] == [
        "LLM_ORDINAL",
        "LLM_ORDINAL",
        "LLM_ORDINAL",
    ]
    assert [item["stepId"] for item in summary["steps"]] == [
        "profile-and-session",
        "heal-Name",
        "heal-Strategic_Deal__c",
        "heal-StageName",
        "submit-and-persist",
    ]


def test_published_reports_are_sanitized_and_html_loads_json_payload() -> None:
    receipt = _receipt()
    receipt["fieldValueDigests"] = [{"fieldApiName": "Name", "valueDigest": "a" * 64}]
    summary = build_live_llm_healing_summary(
        receipt,
        receipt_path=".runtime/live-llm-business-healing/live-business-action-llm.json",
        published_at_utc=datetime(2026, 9, 13, 5, 0, 0, tzinfo=UTC),
    )
    markdown = render_markdown(summary)
    html = render_html(summary)
    events = jsonl_events(summary)
    combined = markdown + html + events

    assert "SYN-LLM-LIVE" not in combined
    assert "frontdoor" not in combined.lower()
    assert "sk-" not in combined
    assert "<script id=\"report-data\" type=\"application/json\">" in html
    assert "element_healing_detail" in events
    assert "automation_step" in events


def test_dashboard_projection_removes_scenario_literals() -> None:
    summary = build_live_llm_healing_summary(
        _receipt(),
        receipt_path=".runtime/live-llm-business-healing/live-business-action-llm.json",
        published_at_utc=datetime(2026, 9, 13, 5, 0, 0, tzinfo=UTC),
        claim="Strategic Deal Workbench fields were healed.",
        test_name="Live Strategic Deal Workbench healing",
    )

    projected = dashboard_projection(summary)
    body = str(projected)

    assert "Strategic" not in body
    assert "Discount__c" not in body
    assert [item["fieldApiName"] for item in projected["fieldEvidence"]] == [
        "field-01",
        "field-02",
        "field-03",
    ]


def test_publish_report_writes_suite_artifacts_and_dashboard_projection(tmp_path) -> None:
    summary = build_live_llm_healing_summary(
        _receipt(),
        receipt_path=".runtime/live-llm-business-healing/live-business-action-llm.json",
        published_at_utc=datetime(2026, 9, 13, 5, 0, 0, tzinfo=UTC),
        claim="Strategic Deal Workbench fields were healed.",
        test_name="Live Strategic Deal Workbench healing",
    )
    output_dir = tmp_path / "demo-evidence"
    dashboard_data = tmp_path / "apps" / "web" / "src" / "data" / "live-llm.json"

    published = publish_report(
        summary,
        output_dir=output_dir,
        dashboard_data_path=dashboard_data,
    )

    assert set(published) == {"dashboardData", "html", "log", "report", "summary"}
    assert (output_dir / "live-llm-healing-summary.json").is_file()
    assert (output_dir / "live-llm-healing-log.jsonl").read_text(encoding="utf-8").count(
        "element_healing_detail"
    ) == 3
    html = (output_dir / "live-llm-healing-report.html").read_text(encoding="utf-8")
    markdown = (output_dir / "live-llm-healing-report.md").read_text(encoding="utf-8")
    dashboard = dashboard_data.read_text(encoding="utf-8")

    assert "<table>" in html
    assert "Field-level healing" in html
    assert "| Step | Status | Detail |" in markdown
    assert "Strategic Deal" in markdown
    assert "field-01" in dashboard
    assert "Strategic Deal" not in dashboard


def _receipt() -> dict[str, object]:
    return {
        "status": "PASSED",
        "browserStatus": "PASSED",
        "evidencePhase": "LIVE_BUSINESS_ACTION_BROWSER_ACCEPTANCE",
        "capabilityId": "automation.browser-worker",
        "forcedModelFieldCount": 3,
        "inputDigest": "1" * 64,
        "executionIdDigest": "2" * 64,
        "businessAction": {
            "submitted": True,
            "successTextMatched": True,
            "healedFieldCount": 3,
            "abstainedFieldCount": 0,
            "modelProposalCount": 3,
            "modelAppliedFieldCount": 3,
            "modelRejectionCodes": [],
            "modelCandidateOrdinals": [0, 1, 1],
            "modelDomCandidateCounts": [1, 2, 2],
            "modelAttemptFields": ["Name", "Strategic_Deal__c", "StageName"],
            "modelContextPlanCounts": [2, 2, 2],
            "modelIntentCitedFields": ["Name", "Strategic_Deal__c", "StageName"],
            "modelIntentFits": [
                "Name:SUFFICIENT",
                "Strategic_Deal__c:SUFFICIENT",
                "StageName:SUFFICIENT",
            ],
            "modelMissingContexts": [
                "Name:NONE",
                "Strategic_Deal__c:NONE",
                "StageName:NONE",
            ],
            "modelConfidenceMillis": [865, 996, 957],
            "signatureLookupFoundFields": ["Name", "Strategic_Deal__c", "StageName"],
            "signatureSavedFields": [
                "Name",
                "Strategic_Deal__c",
                "StageName",
                "Amount",
            ],
        },
        "persistence": {"matched": True},
    }
