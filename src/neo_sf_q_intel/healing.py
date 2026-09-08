from __future__ import annotations

from neo_sf_q_intel.domain import HealingProposal, ImpactFinding

HEALABLE_KINDS = {"data-field", "user-interface"}


def propose_safe_healing(impacts: list[ImpactFinding]) -> list[HealingProposal]:
    """Create proposals only; execution remains behind policy and human approval."""
    proposals = []
    for impact in impacts:
        if impact.kind not in HEALABLE_KINDS:
            continue
        proposals.append(
            HealingProposal(
                target_id=impact.entity_id,
                strategy="metadata-identity-then-accessibility-role",
                rationale=(
                    "Re-identify the target from Salesforce metadata and fresh DOM identity "
                    "signals; abstain when identity is ambiguous."
                ),
                ranking_score=min(0.95, impact.evidence_strength),
                score_basis=f"{impact.strength_basis}:METADATA_STRATEGY_MATCH",
                evidence_ids=impact.evidence_ids,
            )
        )
    return proposals
