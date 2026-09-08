from __future__ import annotations

from neo_sf_q_intel.domain import (
    AnalysisGap,
    ChangeRequest,
    EvidenceRef,
    ImpactFinding,
    TestSelection,
)
from neo_sf_q_intel.policy import ReasoningPolicy
from neo_sf_q_intel.retrieval import EvidenceRetriever, RetrievalHit

Candidate = tuple[dict, str, str, float, dict]


class ChangeIntelligenceService:
    def __init__(self, retriever: EvidenceRetriever, policy: ReasoningPolicy | None = None) -> None:
        self.retriever = retriever
        self.policy = policy or ReasoningPolicy.load()

    def analyze(
        self, request: ChangeRequest
    ) -> tuple[list[EvidenceRef], list[ImpactFinding], list[TestSelection], list[AnalysisGap]]:
        seeds = self.retriever.search(
            request.requirement, request.changed_paths, limit=self.policy.seed_count
        )
        evidence: list[EvidenceRef] = []
        impacts: list[ImpactFinding] = []
        test_selections: list[TestSelection] = []

        direct = [
            (
                hit.node,
                "matched",
                "direct",
                self._direct_confidence(hit),
                {"kind": "query-match", "reasons": list(hit.reasons)},
            )
            for hit in seeds
        ]
        traversal_hits, traversal_gaps, traversal_truncated = self.retriever.expand(
            [str(hit.node["id"]) for hit in seeds],
            depth=self.policy.traversal_depth,
            limit=self.policy.max_traversal_nodes,
            allowed_relations=self.policy.traversable_relations,
        )
        gaps = [
            AnalysisGap(
                code="UNKNOWN_RELATION",
                message="Traversal stopped at a relationship absent from the reviewed policy.",
                entity_id=f"{gap.source_id}->{gap.target_id}",
                relation=gap.relation,
            )
            for gap in traversal_gaps
        ]
        if traversal_truncated:
            gaps.append(
                AnalysisGap(
                    code="TRUNCATED_TRAVERSAL",
                    message="Graph traversal exceeded its reviewed node capacity.",
                )
            )
        expanded = [
            (
                hit.node,
                hit.relation,
                hit.direction,
                max(0.6, 0.9 - (hit.depth * 0.1)),
                {
                    "kind": "graph-edge",
                    "source_id": hit.source_id,
                    "relation": hit.relation,
                    "target_id": hit.target_id,
                    "direction": hit.direction,
                },
            )
            for hit in traversal_hits
        ]
        candidate_tests: list[Candidate] = []
        candidate_impacts: list[Candidate] = []
        for candidate in [*direct, *expanded]:
            kind = str(candidate[0].get("kind", "unknown"))
            kind_policy = self.policy.node_kinds.get(kind)
            if kind_policy is None:
                gaps.append(
                    AnalysisGap(
                        code="UNKNOWN_NODE_KIND",
                        message="A reachable node kind is absent from the reviewed policy.",
                        entity_id=str(candidate[0].get("id", "unknown")),
                    )
                )
                continue
            if kind_policy.role == "VALIDATION":
                candidate_tests.append(candidate)
            elif kind_policy.role == "IMPACT":
                candidate_impacts.append(candidate)

        severity_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        ranked_impacts = sorted(
            self._unique_candidates(candidate_impacts),
            key=lambda item: (
                -severity_rank[self.policy.node_kinds[str(item[0]["kind"])].severity or "MEDIUM"],
                -item[3],
                str(item[0]["id"]),
            ),
        )
        bounded_candidates = ranked_impacts[: self.policy.max_impacts]
        if len(ranked_impacts) > len(bounded_candidates):
            gaps.append(
                AnalysisGap(
                    code="TRUNCATED_IMPACTS",
                    message="Confirmed impacts exceeded the reviewed output capacity.",
                )
            )
        for node, relation, direction, confidence, receipt in bounded_candidates:
            item = self._evidence_with_receipt(node, receipt)
            evidence.append(item)
            kind = str(node.get("kind", "unknown"))
            impacts.append(
                ImpactFinding(
                    entity_id=str(node["id"]),
                    label=str(node.get("label", node["id"])),
                    kind=kind,
                    relation=f"{direction}:{relation}",
                    severity=self.policy.node_kinds[kind].severity or "MEDIUM",
                    confidence=confidence,
                    evidence_ids=[item.evidence_id],
                )
            )

        ranked_tests = sorted(
            self._unique_candidates(candidate_tests),
            key=lambda item: (
                item[0].get("mandatory") is not True,
                -item[3],
                str(item[0]["id"]),
            ),
        )
        mandatory = [item for item in ranked_tests if item[0].get("mandatory") is True]
        recommended = [item for item in ranked_tests if item[0].get("mandatory") is not True]
        recommendation_capacity = max(0, self.policy.max_selected_tests - len(mandatory))
        bounded_tests = [*mandatory, *recommended[:recommendation_capacity]]
        if len(mandatory) > self.policy.max_selected_tests or len(ranked_tests) > len(
            bounded_tests
        ):
            gaps.append(
                AnalysisGap(
                    code="VALIDATION_LIMIT_EXCEEDED",
                    message="Validation obligations exceeded the reviewed execution capacity.",
                )
            )
        for node, relation, _, _, receipt in bounded_tests:
            item = self._evidence_with_receipt(node, receipt)
            evidence.append(item)
            test_selections.append(
                TestSelection(
                    test_id=str(node["id"]),
                    label=str(node.get("label", node["id"])),
                    classification=(
                        "MANDATORY" if node.get("mandatory") is True else "RECOMMENDED"
                    ),
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
            list({(gap.code, gap.entity_id, gap.relation): gap for gap in gaps}.values()),
        )

    @staticmethod
    def _direct_confidence(hit: RetrievalHit) -> float:
        return min(1.0, max(0.6, hit.score))

    def _evidence_with_receipt(self, node: dict, receipt: dict) -> EvidenceRef:
        evidence = self.retriever.evidence_for(node)
        return evidence.model_copy(
            update={"attributes": {**evidence.attributes, "relevance_receipt": receipt}}
        )

    @staticmethod
    def _unique_candidates(candidates: list[Candidate]) -> list[Candidate]:
        return list({str(item[0]["id"]): item for item in candidates}.values())
