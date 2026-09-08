from copy import deepcopy
from pathlib import Path

import pytest

from neo_sf_q_intel.analysis import ChangeIntelligenceService
from neo_sf_q_intel.domain import ChangeIntent, ChangeRequest
from neo_sf_q_intel.retrieval import EvidenceRetriever
from neo_sf_q_intel.salesforce_source import SalesforceSourceSnapshot
from neo_sf_q_intel.service import AssuranceService
from tests.graph_fixtures import add_trusted_envelopes, fixture_digest


def topology(prefix: str) -> SalesforceSourceSnapshot:
    source_hash = fixture_digest(f"{prefix.casefold()}-graph")
    graph = {
        "sourceSnapshot": prefix,
        "nodes": [
            {
                "id": f"config:{prefix}",
                "kind": "custom-metadata-record",
                "label": f"{prefix} policy",
            },
            {"id": f"flow:{prefix}", "kind": "flow", "label": f"{prefix} automation"},
            {"id": f"test:{prefix}", "kind": "apex-test", "label": f"{prefix} validation"},
        ],
        "edges": [
            {"from": f"flow:{prefix}", "relation": "reads", "to": f"config:{prefix}"},
            {"from": f"test:{prefix}", "relation": "tests", "to": f"flow:{prefix}"},
        ],
    }
    add_trusted_envelopes(
        graph,
        snapshot_id=prefix,
        source_hash=source_hash,
    )
    return SalesforceSourceSnapshot(
        root=Path("."),
        contract={"application": f"{prefix} Fixture"},
        project_index={"sourceSnapshot": prefix},
        trusted_graph_sha256=source_hash,
        graph=graph,
    )


def analyze(source: SalesforceSourceSnapshot, requirement: str):
    return ChangeIntelligenceService(EvidenceRetriever(source)).analyze(
        ChangeRequest(requirement=requirement, change_intent=ChangeIntent.PLANNED_CHANGE)
    )


@pytest.mark.parametrize(
    "requirement",
    ["change something", "refactor the thing", "add a field", "change permission access"],
)
def test_vague_common_token_request_abstains(requirement: str) -> None:
    evidence, impacts, tests, gaps = analyze(topology("Alpha"), requirement)
    assert (evidence, impacts, tests, gaps) == ([], [], [], [])


def test_renaming_business_entities_preserves_result_shape() -> None:
    alpha = analyze(topology("Alpha"), "Alpha policy")
    beta = analyze(topology("Beta"), "Beta policy")
    assert [item.kind for item in alpha[1]] == [item.kind for item in beta[1]]
    assert [item.classification for item in alpha[2]] == [item.classification for item in beta[2]]


def test_disconnected_nodes_do_not_change_results() -> None:
    base = topology("Alpha")
    extended = deepcopy(base)
    extended.graph["nodes"].append(
        {"id": "object:Disconnected", "kind": "object", "label": "Unrelated ledger"}
    )
    before = analyze(base, "Alpha policy")
    after = analyze(extended, "Alpha policy")
    assert [item.entity_id for item in before[1]] == [item.entity_id for item in after[1]]
    assert [item.test_id for item in before[2]] == [item.test_id for item in after[2]]


def test_ambiguous_high_overlap_request_abstains() -> None:
    source = topology("Alpha")
    source.graph["nodes"] = [
        {
            "id": f"permission:{index}",
            "kind": "permission-set",
            "label": f"Permission access profile {index}",
        }
        for index in range(8)
    ]
    source.graph["edges"] = []

    evidence, impacts, tests, gaps = analyze(source, "change permission access")

    assert (evidence, impacts, tests, gaps) == ([], [], [], [])


def test_unmapped_relation_touching_material_endpoint_blocks_reasoning() -> None:
    source = topology("Alpha")
    source.graph["nodes"].append(
        {"id": "file:Guide", "kind": "file", "label": "Architecture guide"}
    )
    source.graph["edges"].append(
        {"from": "file:Guide", "relation": "describes", "to": "config:Alpha"}
    )
    add_trusted_envelopes(
        source.graph,
        snapshot_id=source.snapshot_id,
        source_hash=source.trusted_graph_sha256 or "",
    )

    run = AssuranceService(source).analyze(
        ChangeRequest(
            requirement="Alpha policy",
            change_intent=ChangeIntent.PLANNED_CHANGE,
        )
    )

    assert ("UNMAPPED_RELATION", True) in {(item.code, item.blocking) for item in run.analysis_gaps}
    assert run.governance and not run.governance.passed


def test_unmapped_context_only_relation_is_visible_and_nonblocking() -> None:
    source = topology("Alpha")
    source.graph["nodes"].extend(
        [
            {"id": "file:One", "kind": "file", "label": "One"},
            {"id": "file:Two", "kind": "file", "label": "Two"},
        ]
    )
    source.graph["edges"].append({"from": "file:One", "relation": "describes", "to": "file:Two"})
    add_trusted_envelopes(
        source.graph,
        snapshot_id=source.snapshot_id,
        source_hash=source.trusted_graph_sha256 or "",
    )

    run = AssuranceService(source).analyze(
        ChangeRequest(requirement="One", change_intent=ChangeIntent.PLANNED_CHANGE)
    )

    assert ("UNMAPPED_RELATION", False) in {
        (item.code, item.blocking) for item in run.analysis_gaps
    }
