from __future__ import annotations

from neo_sf_q_intel.domain import (
    ChangeRequest,
    EvidenceRef,
    ImpactFinding,
    TestSelection,
)
from neo_sf_q_intel.retrieval import EvidenceRetriever

RISK_BY_KIND = {
    "permission-set": "HIGH",
    "flow": "HIGH",
    "approval-process": "HIGH",
    "apex-trigger": "HIGH",
    "apex-class": "MEDIUM",
    "field": "MEDIUM",
    "lightning-component": "MEDIUM",
    "custom-metadata-record": "HIGH",
}
NON_MATERIAL_KINDS = {"file", "fixture"}
MAX_IMPACTS = 24
MAX_SELECTED_TESTS = 12


class ChangeIntelligenceService:
    def __init__(self, retriever: EvidenceRetriever) -> None:
        self.retriever = retriever

    def analyze(
        self, request: ChangeRequest
    ) -> tuple[list[EvidenceRef], list[ImpactFinding], list[TestSelection]]:
        seeds = self.retriever.search(request.requirement, request.changed_paths, limit=6)
        evidence: list[EvidenceRef] = []
        impacts: list[ImpactFinding] = []
        test_selections: list[TestSelection] = []

        candidates = [(hit.node, "matched", "direct") for hit in seeds]
        candidates.extend(self.retriever.expand([str(hit.node["id"]) for hit in seeds], depth=2))
        candidate_tests: list[tuple[dict, str, str]] = []
        candidate_impacts: list[tuple[dict, str, str]] = []
        for candidate in candidates:
            kind = str(candidate[0].get("kind", "unknown"))
            if kind in {"apex-test", "manual-use-case", "api-use-case"}:
                candidate_tests.append(candidate)
            elif kind not in NON_MATERIAL_KINDS:
                candidate_impacts.append(candidate)

        bounded_candidates = candidate_impacts[:MAX_IMPACTS]
        bounded_ids = {str(node["id"]) for node, _, _ in bounded_candidates}
        bounded_candidates.extend(
            candidate
            for candidate in candidate_tests[:MAX_SELECTED_TESTS]
            if str(candidate[0]["id"]) not in bounded_ids
        )

        for node, relation, direction in bounded_candidates:
            item = self.retriever.evidence_for(node)
            evidence.append(item)
            kind = str(node.get("kind", "unknown"))
            impacts.append(
                ImpactFinding(
                    entity_id=str(node["id"]),
                    label=str(node.get("label", node["id"])),
                    kind=kind,
                    relation=f"{direction}:{relation}",
                    severity=RISK_BY_KIND.get(kind, "LOW"),
                    confidence=1.0,
                    evidence_ids=[item.evidence_id],
                )
            )
            if kind in {"apex-test", "manual-use-case", "api-use-case"}:
                test_selections.append(
                    TestSelection(
                        test_id=str(node["id"]),
                        label=str(node.get("label", node["id"])),
                        classification="MANDATORY",
                        reason=f"Graph-connected through {relation}",
                        evidence_ids=[item.evidence_id],
                    )
                )

        unique_evidence = {item.evidence_id: item for item in evidence}
        unique_impacts = {item.entity_id: item for item in impacts}
        unique_tests = {item.test_id: item for item in test_selections}
        return (
            list(unique_evidence.values()),
            list(unique_impacts.values()),
            list(unique_tests.values()),
        )
