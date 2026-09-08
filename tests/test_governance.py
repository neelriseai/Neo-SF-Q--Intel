import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.domain import (
    AssuranceRun,
    ChangeIntent,
    ChangeRequest,
    EvidenceRef,
    EvidenceState,
    MetricStatus,
)
from neo_sf_q_intel.domain import (
    TestExecution as ExecutionResult,
)
from neo_sf_q_intel.domain import (
    TestOutcome as ExecutionOutcome,
)
from neo_sf_q_intel.governance import assess_run, build_grounded_claims, decide
from tests.test_workflow import source


def test_stale_evidence_cannot_support_a_material_claim() -> None:
    from neo_sf_q_intel.service import AssuranceService

    run = AssuranceService(source()).analyze(
        ChangeRequest(
            requirement="Change Deal Workbench layout",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )
    run.evidence[0].state = EvidenceState.STALE
    run.claims = build_grounded_claims(run)
    assessment = assess_run(run)

    assert not run.claims[0].supported
    assert not assessment.passed


def test_tampered_graph_relation_cannot_support_a_material_claim() -> None:
    from neo_sf_q_intel.service import AssuranceService

    run = AssuranceService(source()).analyze(
        ChangeRequest(
            requirement="Change Deal Workbench layout",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )
    run.impacts[0].relation = "incoming:fabricated_relation"
    run.claims = build_grounded_claims(run)

    assert not run.claims[0].supported


def test_empty_metric_populations_are_not_applicable_without_false_blocking() -> None:
    run = AssuranceRun(
        request=ChangeRequest(requirement="Explain the current source context"),
        reasoning_policy_version="1.1.0",
        reasoning_policy_sha256="0" * 64,
        reasoning_eval_set_id="unit-fixture",
        reasoning_eval_set_sha256="1" * 64,
        evidence=[
            EvidenceRef(
                evidence_id="source:one",
                kind="source",
                label="Source context",
                source="fixture.json",
                state=EvidenceState.CONFIRMED,
            )
        ],
    )

    assessment = assess_run(run)

    assert assessment.passed
    assert all(item.denominator == 0 for item in assessment.metrics)
    assert all(item.status is MetricStatus.NOT_APPLICABLE for item in assessment.metrics)
    assert all(
        not item.blocking for item in assessment.guardrails if item.outcome == "NOT_APPLICABLE"
    )


def test_unconfirmed_evidence_fails_the_presence_and_coverage_controls() -> None:
    from neo_sf_q_intel.service import AssuranceService

    run = AssuranceService(source()).analyze(
        ChangeRequest(
            requirement="Change Deal Workbench layout",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )
    for item in run.evidence:
        item.state = EvidenceState.STALE
    run.claims = build_grounded_claims(run)

    assessment = assess_run(run)

    assert not assessment.passed
    assert "CONFIRMED_EVIDENCE_NOT_AVAILABLE" in assessment.violations
    assert {item.status for item in assessment.metrics} >= {MetricStatus.FAILED}
    run.governance = assessment
    assert decide(run).code == "INCOMPLETE"


def _analyzed_run() -> AssuranceRun:
    from neo_sf_q_intel.service import AssuranceService

    return AssuranceService(source()).analyze(
        ChangeRequest(
            requirement="Change Deal Workbench layout",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )


def _record_result(
    run: AssuranceRun,
    outcome: ExecutionOutcome,
    *,
    runner_id: str = "verified-test-runner",
    executed_at: datetime | None = None,
) -> None:
    source_snapshot = str(run.evidence[0].attributes["snapshot_id"])
    executed_at = executed_at or datetime.now(UTC)
    valid_until = executed_at + timedelta(hours=1)
    artifact = {
        "test_id": run.selected_tests[0].test_id,
        "outcome": outcome,
        "runner_id": runner_id,
        "source_snapshot": source_snapshot,
        "executed_at": executed_at.isoformat(),
        "valid_until": valid_until.isoformat(),
    }
    result_hash = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    execution_id = f"execution:{result_hash}"
    execution = ExecutionResult(
        test_id=run.selected_tests[0].test_id,
        outcome=outcome,
        runner_id=runner_id,
        result_sha256=result_hash,
        source_snapshot=source_snapshot,
        executed_at=executed_at,
        valid_until=valid_until,
        evidence_ids=[execution_id],
    )
    run.evidence.append(
        EvidenceRef(
            evidence_id=execution_id,
            kind="test-execution",
            label="Verified runner execution receipt",
            source=runner_id,
            state=EvidenceState.CONFIRMED,
            attributes={
                "test_id": run.selected_tests[0].test_id,
                "outcome": outcome,
                "runner_id": runner_id,
                "source_snapshot": source_snapshot,
                "result_sha256": result_hash,
                "source_hash": result_hash,
                "executed_at": execution.executed_at.isoformat(),
                "valid_until": execution.valid_until.isoformat(),
                "result_artifact": artifact,
            },
        )
    )
    run.test_results = [execution]


def test_high_risk_requires_a_mandatory_validation() -> None:
    run = _analyzed_run()
    run.impacts[0].severity = "HIGH"

    assert decide(run).code == "INCOMPLETE"


def test_high_risk_requires_execution_evidence_for_mandatory_validation() -> None:
    run = _analyzed_run()
    run.impacts[0].severity = "HIGH"
    run.selected_tests[0].classification = "MANDATORY"

    assert decide(run).code == "INCOMPLETE"


def test_failed_selected_validation_is_no_go() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.FAILED)

    assert decide(run).code == "NO_GO"


def test_passed_mandatory_validation_is_conditional_for_high_risk() -> None:
    run = _analyzed_run()
    run.impacts[0].severity = "HIGH"
    run.selected_tests[0].classification = "MANDATORY"
    _record_result(run, ExecutionOutcome.PASSED)

    assert decide(run).code == "CONDITIONAL_GO"


def test_all_selected_validations_pass_for_lower_risk() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)

    assert decide(run).code == "GO"


def test_inconclusive_selected_validation_is_incomplete() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.INCONCLUSIVE)

    assert decide(run).code == "INCOMPLETE"


def test_unconfirmed_execution_evidence_is_incomplete() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    execution_evidence_id = run.test_results[0].evidence_ids[0]
    next(
        item for item in run.evidence if item.evidence_id == execution_evidence_id
    ).state = EvidenceState.STALE

    assert decide(run).code == "INCOMPLETE"


def test_selection_graph_evidence_cannot_substitute_for_execution_receipt() -> None:
    run = _analyzed_run()
    result_hash = "a" * 64
    executed_at = datetime.now(UTC)
    run.test_results = [
        ExecutionResult(
            test_id=run.selected_tests[0].test_id,
            outcome=ExecutionOutcome.PASSED,
            runner_id="verified-test-runner",
            result_sha256=result_hash,
            source_snapshot=str(run.evidence[0].attributes["snapshot_id"]),
            executed_at=executed_at,
            valid_until=executed_at + timedelta(hours=1),
            evidence_ids=run.selected_tests[0].evidence_ids,
        )
    ]

    assert decide(run).code == "INCOMPLETE"


def test_future_execution_receipt_is_incomplete() -> None:
    run = _analyzed_run()
    _record_result(
        run,
        ExecutionOutcome.PASSED,
        executed_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert decide(run).code == "INCOMPLETE"


def test_untrusted_runner_cannot_self_confirm_execution() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED, runner_id="self-declared-runner")

    assert decide(run).code == "INCOMPLETE"


def test_execution_receipt_timestamp_must_match_result() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    execution_id = run.test_results[0].evidence_ids[0]
    receipt = next(item for item in run.evidence if item.evidence_id == execution_id)
    receipt.attributes["executed_at"] = "2000-01-01T00:00:00+00:00"

    assert decide(run).code == "INCOMPLETE"


def test_release_rechecks_current_facts_without_treating_missing_proof_as_failure() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    run.impacts[0].evidence_ids = ["missing:evidence"]

    assert decide(run).code == "INCOMPLETE"


def test_release_rechecks_requirement_binding() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    run.request.requirement = "A different planned change with unrelated meaning"

    assert decide(run).code == "INCOMPLETE"


def test_release_rechecks_change_intent_binding() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    run.request.change_intent = ChangeIntent.INFORMATIONAL

    assert decide(run).code == "INCOMPLETE"


def test_graph_derived_impacts_are_bound_to_original_analysis_input() -> None:
    from neo_sf_q_intel.service import AssuranceService
    from tests.test_reasoning_policy import policy_source

    source = policy_source()
    source.graph["nodes"][1]["label"] = "Beta validation"
    source.graph["nodes"] = source.graph["nodes"][:2]
    source.graph["edges"] = source.graph["edges"][:1]
    run = AssuranceService(source).analyze(
        ChangeRequest(
            requirement="Beta validation",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )
    _record_result(run, ExecutionOutcome.PASSED)
    assert decide(run).code == "GO"

    run.request.requirement = "A completely unrelated requirement"

    assert decide(run).code == "INCOMPLETE"


def test_release_rejects_assessment_from_another_policy() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    assert run.governance
    run.governance.policy_sha256 = "0" * 64

    assert decide(run).code == "INCOMPLETE"


def test_release_rechecks_graph_receipt_expiry() -> None:
    run = _analyzed_run()
    _record_result(run, ExecutionOutcome.PASSED)
    selection_evidence_id = run.selected_tests[0].evidence_ids[0]
    selection_evidence = next(
        item for item in run.evidence if item.evidence_id == selection_evidence_id
    )
    selection_evidence.attributes["relevance_receipt"]["valid_until"] = "2000-01-01T00:00:00Z"

    assert decide(run).code == "INCOMPLETE"


def test_evidence_cannot_implicitly_promote_itself_to_confirmed() -> None:
    with pytest.raises(ValidationError, match="state"):
        EvidenceRef(
            evidence_id="unclassified:one",
            kind="source",
            label="Unclassified evidence",
            source="adapter",
        )
