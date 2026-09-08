from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from neo_sf_q_intel.domain import (
    AssuranceRun,
    Claim,
    DecisionCode,
    EvidenceState,
    GovernanceAssessment,
    GovernanceMetric,
    GuardrailDecision,
    GuardrailOutcome,
    MetricComparator,
    MetricStatus,
    ReleaseDecision,
    RiskSeverity,
    TestClassification,
    TestOutcome,
)
from neo_sf_q_intel.governance_policy import (
    ControlEvaluator,
    GovernancePolicy,
    MetricDefinition,
    MetricEvaluator,
    MetricGate,
    ZeroDenominatorAction,
)


def build_grounded_claims(run: AssuranceRun) -> list[Claim]:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    claims = []
    for index, impact in enumerate(run.impacts, start=1):
        evidence = [evidence_by_id.get(item) for item in impact.evidence_ids]
        supported = bool(evidence) and all(
            _supports_impact(item, impact.entity_id, impact.relation, run.request)
            for item in evidence
        )
        planned = run.request.change_intent == "PLANNED_CHANGE"
        claims.append(
            Claim(
                claim_id=f"impact:{index}",
                text=(
                    f"Planned change may impact {impact.label} through {impact.relation}."
                    if planned
                    else f"{impact.label} is impacted through {impact.relation}."
                ),
                evidence_ids=impact.evidence_ids,
                supported=supported,
            )
        )
    return claims


def _supports_impact(
    evidence: object,
    entity_id: str,
    asserted_relation: str,
    request,
) -> bool:
    if not _valid_evidence_at_release(evidence):
        return False
    if evidence.attributes.get("entity_id") != entity_id:
        return False
    receipt = evidence.attributes.get("relevance_receipt", {})
    direction, _, relation = asserted_relation.partition(":")
    if receipt.get("kind") == "declared-change":
        return (
            direction == "direct"
            and relation == "declared"
            and receipt.get("change_intent") == request.change_intent
            and receipt.get("requirement_hash")
            == hashlib.sha256(request.requirement.strip().encode()).hexdigest()
            and receipt.get("project_id") == request.project_id
            and receipt.get("source_ref") == request.source_ref
            and receipt.get("source_snapshot") == evidence.attributes.get("snapshot_id")
            and receipt.get("source_hash") == evidence.attributes.get("source_hash")
        )
    if receipt.get("kind") != "graph-edge":
        return False
    if receipt.get("direction") != direction or receipt.get("relation") != relation:
        return False
    expected_entity = (
        receipt.get("target_id") if direction == "outgoing" else receipt.get("source_id")
    )
    return expected_entity == entity_id


def _valid_evidence_at_release(evidence: object) -> bool:
    if evidence is None or not hasattr(evidence, "attributes"):
        return False
    if evidence.state not in {EvidenceState.CONFIRMED, EvidenceState.HUMAN_CONFIRMED}:
        return False
    receipt = evidence.attributes.get("relevance_receipt", {})
    if receipt.get("kind") != "graph-edge":
        return True
    if receipt.get("evidence_state") not in {
        EvidenceState.CONFIRMED,
        EvidenceState.HUMAN_CONFIRMED,
    }:
        return False
    source_hash = receipt.get("source_hash")
    if not source_hash or source_hash != evidence.attributes.get("source_hash"):
        return False
    if receipt.get("source_snapshot") != evidence.attributes.get("snapshot_id"):
        return False
    valid_until = receipt.get("valid_until")
    if not valid_until:
        return True
    try:
        expires = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > datetime.now(UTC)


def _artifact_sha256(artifact: object) -> str | None:
    if not isinstance(artifact, dict):
        return None
    canonical = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _analysis_input_sha256(run: AssuranceRun) -> str:
    source_snapshots = sorted(
        {
            str(item.attributes["snapshot_id"])
            for item in run.evidence
            if item.attributes.get("snapshot_id")
        }
    )
    body = {
        "project_id": run.request.project_id,
        "source_ref": run.request.source_ref,
        "source_snapshots": source_snapshots,
        "change_intent": run.request.change_intent,
        "requirement": run.request.requirement.strip(),
        "changed_paths": sorted(path.replace("\\", "/") for path in run.request.changed_paths),
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _metric_status(definition: MetricDefinition, numerator: int, denominator: int) -> MetricStatus:
    if denominator == 0:
        return (
            MetricStatus.NOT_APPLICABLE
            if definition.zero_denominator is ZeroDenominatorAction.NOT_APPLICABLE
            else MetricStatus.FAILED
        )
    if denominator < definition.minimum_sample_size:
        return MetricStatus.INSUFFICIENT_SAMPLE
    value = numerator / denominator
    passed = (
        value >= definition.target
        if definition.comparator is MetricComparator.AT_LEAST
        else value <= definition.target
    )
    return MetricStatus.PASSED if passed else MetricStatus.FAILED


def _metric(definition: MetricDefinition, numerator: int, denominator: int) -> GovernanceMetric:
    status = _metric_status(definition, numerator, denominator)
    return GovernanceMetric(
        metric=definition.metric,
        numerator=numerator,
        denominator=denominator,
        target=definition.target,
        comparator=definition.comparator,
        minimum_sample_size=definition.minimum_sample_size,
        status=status,
        blocking=definition.gate is MetricGate.BLOCK,
    )


def _metric_decision(control, metric: GovernanceMetric) -> GuardrailDecision:
    if metric.status is MetricStatus.NOT_APPLICABLE:
        outcome = GuardrailOutcome.NOT_APPLICABLE
        reason_code = "NO_APPLICABLE_POPULATION"
    elif metric.status is MetricStatus.PASSED:
        outcome = GuardrailOutcome.ALLOW
        reason_code = "CONTROL_SATISFIED"
    else:
        outcome = control.failure_outcome
        reason_code = control.failure_reason_code
    return GuardrailDecision(
        control_id=control.control_id,
        control_version=control.version,
        stage=control.stage,
        outcome=outcome,
        reason_code=reason_code,
        blocking=control.blocking
        and outcome
        not in {
            GuardrailOutcome.ALLOW,
            GuardrailOutcome.NOT_APPLICABLE,
        },
    )


def assess_run(run: AssuranceRun, policy: GovernancePolicy | None = None) -> GovernanceAssessment:
    active_policy = policy or GovernancePolicy.load()
    evidence_ids = {item.evidence_id for item in run.evidence if _valid_evidence_at_release(item)}
    material_claims = [claim for claim in run.claims if claim.material]
    supported_claims = [
        claim
        for claim in material_claims
        if claim.supported
        and claim.evidence_ids
        and all(item in evidence_ids for item in claim.evidence_ids)
    ]
    counts = {
        MetricEvaluator.MATERIAL_CLAIM_EVIDENCE_COVERAGE: (
            len(supported_claims),
            len(material_claims),
        ),
        MetricEvaluator.IMPACT_EVIDENCE_COVERAGE: (
            sum(
                bool(item.evidence_ids)
                and all(evidence_id in evidence_ids for evidence_id in item.evidence_ids)
                for item in run.impacts
            ),
            len(run.impacts),
        ),
        MetricEvaluator.SELECTED_TEST_EVIDENCE_COVERAGE: (
            sum(
                bool(item.evidence_ids)
                and all(evidence_id in evidence_ids for evidence_id in item.evidence_ids)
                for item in run.selected_tests
            ),
            len(run.selected_tests),
        ),
    }
    metrics = [
        _metric(definition, *counts[definition.metric]) for definition in active_policy.metrics
    ]
    metric_by_id = {item.metric: item for item in metrics}
    decisions: list[GuardrailDecision] = []
    for control in active_policy.controls:
        if control.evaluator is ControlEvaluator.METRIC_THRESHOLD:
            decisions.append(_metric_decision(control, metric_by_id[control.metric]))
            continue
        if control.evaluator is ControlEvaluator.CONFIRMED_EVIDENCE_PRESENT:
            satisfied = bool(evidence_ids)
            supporting_evidence = sorted(evidence_ids)[:20]
            observations = []
        else:
            blocking_gaps = [item.code for item in run.analysis_gaps if item.blocking]
            satisfied = not blocking_gaps
            supporting_evidence = []
            observations = sorted({item.code for item in run.analysis_gaps})
        outcome = GuardrailOutcome.ALLOW if satisfied else control.failure_outcome
        if satisfied and observations:
            reason_code = "NON_BLOCKING_GAPS_OBSERVED"
        else:
            reason_code = "CONTROL_SATISFIED" if satisfied else control.failure_reason_code
        decisions.append(
            GuardrailDecision(
                control_id=control.control_id,
                control_version=control.version,
                stage=control.stage,
                outcome=outcome,
                reason_code=reason_code,
                evidence_ids=supporting_evidence,
                observations=observations,
                blocking=control.blocking and not satisfied,
            )
        )
    violations = [item.reason_code for item in decisions if item.blocking]
    return GovernanceAssessment(
        policy_version=active_policy.schema_version,
        policy_sha256=active_policy.sha256,
        analysis_input_sha256=_analysis_input_sha256(run),
        metrics=metrics,
        guardrails=decisions,
        violations=violations,
        passed=not violations,
    )


def decide(run: AssuranceRun, policy: GovernancePolicy | None = None) -> ReleaseDecision:
    candidate_policy = policy or GovernancePolicy.load()
    try:
        active_policy = GovernancePolicy.model_validate(candidate_policy.model_dump(mode="python"))
    except ValidationError:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["GOVERNANCE_POLICY_INVALID"],
        )
    if not run.evidence:
        return ReleaseDecision(code=DecisionCode.INCOMPLETE, reasons=["No evidence available"])
    if run.governance is None:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["Governance assessment is not available"],
            evidence_ids=[item.evidence_id for item in run.evidence[:10]],
        )
    if (
        run.governance.policy_version != active_policy.schema_version
        or run.governance.policy_sha256 != active_policy.sha256
    ):
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["Stored governance assessment uses a different policy"],
        )
    current_run = run.model_copy(update={"claims": build_grounded_claims(run)}, deep=True)
    current_assessment = assess_run(current_run, active_policy)
    if run.governance.analysis_input_sha256 != current_assessment.analysis_input_sha256:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["Analysis input changed after governance assessment"],
        )
    if not active_policy.release_authority.enabled:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=[active_policy.release_authority.reason_code],
        )
    blocking = [item for item in current_assessment.guardrails if item.blocking]
    if blocking:
        mapped = [active_policy.decision_mapping.get(item.outcome) for item in blocking]
        if DecisionCode.NO_GO in mapped:
            code = DecisionCode.NO_GO
        elif DecisionCode.INCOMPLETE in mapped:
            code = DecisionCode.INCOMPLETE
        else:
            code = DecisionCode.CONDITIONAL_GO
        return ReleaseDecision(
            code=code,
            reasons=[item.reason_code for item in blocking],
            evidence_ids=sorted(
                {evidence_id for item in blocking for evidence_id in item.evidence_ids}
            ),
        )

    confirmed_ids = {
        item.evidence_id
        for item in run.evidence
        if item.state in {EvidenceState.CONFIRMED, EvidenceState.HUMAN_CONFIRMED}
    }
    selected_by_id = {item.test_id: item for item in run.selected_tests}
    result_counts: dict[str, int] = {}
    for result in run.test_results:
        result_counts[result.test_id] = result_counts.get(result.test_id, 0) + 1
    duplicate_ids = sorted(test_id for test_id, count in result_counts.items() if count > 1)
    if duplicate_ids:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["Duplicate test results require reconciliation"],
        )
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    source_snapshots = {
        item.attributes.get("snapshot_id")
        for item in run.evidence
        if item.attributes.get("snapshot_id")
    }
    valid_results = {}
    now = datetime.now(UTC)
    for result in run.test_results:
        receipts = [evidence_by_id.get(evidence_id) for evidence_id in result.evidence_ids]
        runner = active_policy.trusted_runner_by_id.get(result.runner_id)
        if (
            runner is not None
            and result.test_id in selected_by_id
            and result.source_snapshot in source_snapshots
            and result.executed_at <= now + timedelta(seconds=runner.maximum_clock_skew_seconds)
            and result.executed_at >= now - timedelta(seconds=runner.maximum_result_age_seconds)
            and result.valid_until > now
            and receipts
            and all(
                receipt is not None
                and receipt.evidence_id in confirmed_ids
                and receipt.kind == "test-execution"
                and receipt.source == runner.evidence_source
                and receipt.attributes.get("test_id") == result.test_id
                and receipt.attributes.get("outcome") == result.outcome
                and receipt.attributes.get("runner_id") == result.runner_id
                and receipt.attributes.get("source_snapshot") == result.source_snapshot
                and receipt.attributes.get("result_sha256") == result.result_sha256
                and receipt.attributes.get("source_hash") == result.result_sha256
                and receipt.attributes.get("executed_at") == result.executed_at.isoformat()
                and receipt.attributes.get("valid_until") == result.valid_until.isoformat()
                and _artifact_sha256(receipt.attributes.get("result_artifact"))
                == result.result_sha256
                for receipt in receipts
            )
        ):
            valid_results[result.test_id] = result
    failed = [item for item in valid_results.values() if item.outcome is TestOutcome.FAILED]
    if failed:
        return ReleaseDecision(
            code=DecisionCode.NO_GO,
            reasons=["At least one selected validation failed"],
            evidence_ids=sorted(
                {evidence_id for item in failed for evidence_id in item.evidence_ids}
            ),
        )

    high_risk = [finding for finding in run.impacts if finding.severity is RiskSeverity.HIGH]
    mandatory = [
        item for item in run.selected_tests if item.classification is TestClassification.MANDATORY
    ]
    if high_risk and not mandatory:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["High-risk impact has no mandatory validation selected"],
            evidence_ids=[evidence_id for item in high_risk for evidence_id in item.evidence_ids],
        )
    required = mandatory if high_risk else run.selected_tests
    missing_or_unverified = [item.test_id for item in required if item.test_id not in valid_results]
    if missing_or_unverified:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["Selected validation lacks a unique result backed by confirmed evidence"],
        )
    inconclusive = [
        valid_results[item.test_id]
        for item in required
        if valid_results[item.test_id].outcome is TestOutcome.INCONCLUSIVE
    ]
    if inconclusive:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["At least one required validation was inconclusive"],
            evidence_ids=sorted(
                {evidence_id for item in inconclusive for evidence_id in item.evidence_ids}
            ),
        )
    if high_risk:
        return ReleaseDecision(
            code=DecisionCode.CONDITIONAL_GO,
            reasons=["Mandatory validations passed; high-risk impact requires human approval"],
            evidence_ids=sorted(
                {
                    evidence_id
                    for item in mandatory
                    for evidence_id in valid_results[item.test_id].evidence_ids
                }
            ),
        )
    if required:
        return ReleaseDecision(
            code=DecisionCode.GO,
            reasons=["All selected validations passed with confirmed execution evidence"],
            evidence_ids=sorted(
                {
                    evidence_id
                    for item in required
                    for evidence_id in valid_results[item.test_id].evidence_ids
                }
            ),
        )
    return ReleaseDecision(
        code=DecisionCode.INCOMPLETE,
        reasons=["No validation was selected for the release decision"],
        evidence_ids=[item.evidence_id for item in run.evidence[:10]],
    )
