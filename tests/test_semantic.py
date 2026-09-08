from pathlib import Path

import pytest

from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot
from neo_sf_q_intel.semantic import SemanticEvidenceIndex, cosine_similarity


class FakeProvider:
    async def reason_json(self, *, instructions, payload):  # noqa: ANN001
        return {}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if "discount" in text.casefold() else [0.0, 1.0] for text in texts]


def source() -> SalesforceSourceSnapshot:
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={},
        project_index={"sourceSnapshot": "semantic"},
        graph={
            "sourceSnapshot": "semantic",
            "nodes": [
                {"id": "field:Discount", "kind": "field", "label": "Discount"},
                {"id": "field:Amount", "kind": "field", "label": "Amount"},
            ],
            "edges": [],
        },
    )


@pytest.mark.asyncio
async def test_semantic_index_ranks_by_embedding_similarity() -> None:
    index = SemanticEvidenceIndex(source(), FakeProvider())
    hits = await index.search("discount change")

    assert hits[0].evidence.attributes["entity_id"] == "field:Discount"
    assert hits[0].score == pytest.approx(1.0)


def test_cosine_rejects_mismatched_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        cosine_similarity([1.0], [1.0, 2.0])
