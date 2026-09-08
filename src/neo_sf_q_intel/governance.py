from __future__ import annotations

from neo_sf_q_intel.domain import (
    AssuranceRun,
    Claim,
    DecisionCode,
    EvidenceState,
    GovernanceAssessment,
    GovernanceMetric,
    ReleaseDecision,
)


def build_grounded_claims(run: AssuranceRun) -> list[Claim]:
    evidence_by_id = {item.evidence_id: item for item in run.evidence}
    claims = []
    for index, impact in enumerate(run.impacts, start=1):
        evidence = [evidence_by_id.get(item) for item in impact.evidence_ids]
        supported = bool(evidence) and all(
            _supports_impact(item, impact.entity_id, impact.relation) for item in evidence
        )
        claims.append(
            Claim(
                claim_id=f"impact:{index}",
                text=f"{impact.label} is impacted through {impact.relation}.",
                evidence_ids=impact.evidence_ids,
                supported=supported,
            )
        )
    return claims


def _supports_impact(evidence: object, entity_id: str, asserted_relation: str) -> bool:
    if evidence is None or not hasattr(evidence, "attributes"):
        return False
    if evidence.state not in {EvidenceState.CONFIRMED, EvidenceState.HUMAN_CONFIRMED}:
        return False
    if evidence.attributes.get("entity_id") != entity_id:
        return False
    receipt = evidence.attributes.get("relevance_receipt", {})
    direction, _, relation = asserted_relation.partition(":")
    if receipt.get("kind") == "query-match":
        return direction == "direct" and relation == "matched" and bool(receipt.get("reasons"))
    if receipt.get("kind") != "graph-edge":
        return False
    if receipt.get("direction") != direction or receipt.get("relation") != relation:
        return False
    expected_entity = (
        receipt.get("target_id") if direction == "outgoing" else receipt.get("source_id")
    )
    return expected_entity == entity_id


def assess_run(run: AssuranceRun) -> GovernanceAssessment:
    evidence_ids = {
        item.evidence_id
        for item in run.evidence
        if item.state in {EvidenceState.CONFIRMED, EvidenceState.HUMAN_CONFIRMED}
    }
    material_claims = [claim for claim in run.claims if claim.material]
    supported_claims = [
        claim
        for claim in material_claims
        if claim.supported
        and claim.evidence_ids
        and all(item in evidence_ids for item in claim.evidence_ids)
    ]
    claim_denominator = max(1, len(material_claims))
    metrics = [
        GovernanceMetric(
            metric="material_claim_evidence_coverage",
            numerator=len(supported_claims),
            denominator=claim_denominator,
            target=1.0,
        ),
        GovernanceMetric(
            metric="impact_evidence_coverage",
            numerator=sum(
                bool(item.evidence_ids)
                and all(evidence_id in evidence_ids for evidence_id in item.evidence_ids)
                for item in run.impacts
            ),
            denominator=max(1, len(run.impacts)),
            target=1.0,
        ),
    ]
    violations = []
    if material_claims and len(supported_claims) != len(material_claims):
        violations.append("One or more material claims lack valid evidence")
    if not run.evidence:
        violations.append("No evidence was retrieved")
    if run.analysis_gaps:
        violations.append("Analysis encountered unreviewed node or relationship semantics")
    return GovernanceAssessment(
        metrics=metrics,
        violations=violations,
        passed=not violations and all(metric.passed for metric in metrics),
    )


def decide(run: AssuranceRun) -> ReleaseDecision:
    if not run.evidence:
        return ReleaseDecision(code=DecisionCode.INCOMPLETE, reasons=["No evidence available"])
    if run.governance is None or not run.governance.passed:
        return ReleaseDecision(
            code=DecisionCode.INCOMPLETE,
            reasons=["Governance gates are incomplete or failed"],
            evidence_ids=[item.evidence_id for item in run.evidence[:10]],
        )
    high_risk = [finding for finding in run.impacts if finding.severity == "HIGH"]
    if high_risk and not run.selected_tests:
        return ReleaseDecision(
            code=DecisionCode.NO_GO,
            reasons=["High-risk impact has no selected validation"],
            evidence_ids=[evidence_id for item in high_risk for evidence_id in item.evidence_ids],
        )
    if high_risk:
        return ReleaseDecision(
            code=DecisionCode.CONDITIONAL_GO,
            reasons=["High-risk impact requires successful selected tests and human review"],
            evidence_ids=[evidence_id for item in high_risk for evidence_id in item.evidence_ids],
        )
    return ReleaseDecision(
        code=DecisionCode.CONDITIONAL_GO,
        reasons=["Selected validation has not yet produced live execution evidence"],
        evidence_ids=[item.evidence_id for item in run.evidence[:10]],
    )
