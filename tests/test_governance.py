from neo_sf_q_intel.domain import ChangeRequest, EvidenceState
from neo_sf_q_intel.governance import assess_run, build_grounded_claims
from tests.test_workflow import source


def test_stale_evidence_cannot_support_a_material_claim() -> None:
    from neo_sf_q_intel.service import AssuranceService

    run = AssuranceService(source()).analyze(
        ChangeRequest(requirement="Change Deal Workbench layout")
    )
    run.evidence[0].state = EvidenceState.STALE
    run.claims = build_grounded_claims(run)
    assessment = assess_run(run)

    assert not run.claims[0].supported
    assert not assessment.passed


def test_tampered_graph_relation_cannot_support_a_material_claim() -> None:
    from neo_sf_q_intel.service import AssuranceService

    run = AssuranceService(source()).analyze(
        ChangeRequest(requirement="Change Deal Workbench layout")
    )
    run.impacts[0].relation = "incoming:fabricated_relation"
    run.claims = build_grounded_claims(run)

    assert not run.claims[0].supported
