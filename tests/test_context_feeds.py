import json
from pathlib import Path

import pytest

from neo_sf_q_intel.context_feeds import (
    ContextFeedError,
    graph_neighborhood,
    knowledge_index,
    knowledge_section,
)

WORKBENCH = """# Strategic Deal Workbench

Intro paragraph.

## Policy Rules

Approval matrix lives here.

### Nested Detail

Nested body text.

#### Too Deep For Index

Deep body text.

## Data Contracts

Contract body.
"""

POLICY_MODULE = """# Strategic Deal Policy

## Approval Chain

Chain body.
"""

IMPACT_MATRIX = """# Field Impact Matrix

## Coverage

Coverage body.
"""

KNOWLEDGE_MAP = """# Knowledge Map

## Index

Map body.
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    knowledge = tmp_path / "knowledge-repo"
    _write(knowledge / "00-knowledge-map.md", KNOWLEDGE_MAP)
    _write(knowledge / "pages" / "strategic-deal-workbench.md", WORKBENCH)
    _write(knowledge / "modules" / "strategic-deal-policy.md", POLICY_MODULE)
    _write(knowledge / "impact" / "field-impact-matrix.md", IMPACT_MATRIX)
    _write(knowledge / "scratch" / "ignored.md", "# Ignored\n")
    return tmp_path


def _graph(tmp_path: Path, edges: list[dict[str, str]], name: str = "graph.json") -> Path:
    path = tmp_path / name
    payload = {"nodes": [], "edges": edges}
    path.write_bytes(json.dumps(payload).encode("utf-8"))
    return path


@pytest.fixture
def graph_path(tmp_path: Path) -> Path:
    edges = [
        {"from": "flow:Strategic_Discount_Approval", "relation": "calls", "to": "apex:Policy"},
        {"from": "flow:Strategic_Discount_Approval", "relation": "reads", "to": "field:Discount"},
        {"from": "apex:Policy", "relation": "writes", "to": "field:ApprovalStatus"},
        {"from": "page:Workbench", "relation": "hosts", "to": "flow:Strategic_Discount_Approval"},
        {"from": "apex:Unrelated", "relation": "calls", "to": "apex:AlsoUnrelated"},
    ]
    return _graph(tmp_path, edges)


def test_knowledge_index_groups_documents_and_headings(repo_root: Path) -> None:
    index = knowledge_index(repo_root)

    assert [document.key for document in index.pages] == ["strategic-deal-workbench"]
    assert [document.key for document in index.modules] == ["strategic-deal-policy"]
    assert [document.key for document in index.impact] == ["field-impact-matrix"]
    assert [document.key for document in index.root] == ["00-knowledge-map"]

    page = index.pages[0]
    assert page.path == "knowledge-repo/pages/strategic-deal-workbench.md"
    assert page.headings == [
        "Strategic Deal Workbench",
        "Policy Rules",
        "Nested Detail",
        "Data Contracts",
    ]


def test_knowledge_index_skips_unknown_top_level_folders(repo_root: Path) -> None:
    index = knowledge_index(repo_root)
    every_key = [
        document.key
        for group in (index.pages, index.modules, index.impact, index.root)
        for document in group
    ]
    assert "ignored" not in every_key


def test_knowledge_section_returns_whole_file_body(repo_root: Path) -> None:
    result = knowledge_section(repo_root, page="strategic-deal-workbench")

    assert result.path == "knowledge-repo/pages/strategic-deal-workbench.md"
    assert result.section is None
    assert result.body == WORKBENCH
    assert result.chars == len(WORKBENCH)
    assert result.truncated is False


def test_knowledge_section_returns_single_heading_block(repo_root: Path) -> None:
    result = knowledge_section(
        repo_root, page="strategic-deal-workbench", section="Policy Rules"
    )

    assert result.section == "Policy Rules"
    assert result.body.startswith("## Policy Rules\n")
    assert "Approval matrix lives here." in result.body
    assert "### Nested Detail" in result.body
    assert "Data Contracts" not in result.body
    assert result.chars == len(result.body)


def test_knowledge_section_heading_match_is_case_insensitive(repo_root: Path) -> None:
    lowered = knowledge_section(
        repo_root, page="strategic-deal-workbench", section="policy rules"
    )
    exact = knowledge_section(
        repo_root, page="strategic-deal-workbench", section="Policy Rules"
    )

    assert lowered.body == exact.body


def test_knowledge_section_supports_module_and_impact_selectors(repo_root: Path) -> None:
    module = knowledge_section(repo_root, module="strategic-deal-policy", section="Approval Chain")
    impact = knowledge_section(repo_root, impact="field-impact-matrix", section="Coverage")

    assert module.path == "knowledge-repo/modules/strategic-deal-policy.md"
    assert "Chain body." in module.body
    assert impact.path == "knowledge-repo/impact/field-impact-matrix.md"
    assert "Coverage body." in impact.body


def test_knowledge_section_rejects_zero_selectors(repo_root: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        knowledge_section(repo_root)
    assert error.value.code == "SELECTOR_INVALID"


def test_knowledge_section_rejects_two_selectors(repo_root: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        knowledge_section(
            repo_root, page="strategic-deal-workbench", module="strategic-deal-policy"
        )
    assert error.value.code == "SELECTOR_INVALID"


@pytest.mark.parametrize(
    "key",
    ("../x", "../../etc/hosts", "/etc/hosts", "c:/windows/system32", "Pages/../secret"),
)
def test_knowledge_section_rejects_escaping_keys(repo_root: Path, key: str) -> None:
    with pytest.raises(ContextFeedError) as error:
        knowledge_section(repo_root, page=key)
    assert error.value.code == "PATH_OUTSIDE_REPO"


def test_knowledge_section_missing_document_is_not_found(repo_root: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        knowledge_section(repo_root, page="absent-page")
    assert error.value.code == "SECTION_NOT_FOUND"


def test_knowledge_section_unmatched_heading_is_not_found(repo_root: Path) -> None:
    with pytest.raises(ContextFeedError) as error:
        knowledge_section(repo_root, page="strategic-deal-workbench", section="No Such Heading")
    assert error.value.code == "SECTION_NOT_FOUND"


def test_knowledge_section_truncates_at_eight_thousand_characters(tmp_path: Path) -> None:
    body = "# Large Page\n\n" + ("filler line\n" * 900)
    assert len(body) > 8000
    _write(tmp_path / "knowledge-repo" / "pages" / "large-page.md", body)

    result = knowledge_section(tmp_path, page="large-page")

    assert result.truncated is True
    assert result.chars == 8000
    assert len(result.body) == 8000
    assert result.body == body[:8000]


def test_graph_neighborhood_single_hop(graph_path: Path) -> None:
    result = graph_neighborhood(graph_path, "flow:Strategic_Discount_Approval")

    assert result.seed == "flow:Strategic_Discount_Approval"
    assert result.hops == 1
    assert result.edges == [
        "flow:Strategic_Discount_Approval -> calls -> apex:Policy",
        "flow:Strategic_Discount_Approval -> reads -> field:Discount",
        "page:Workbench -> hosts -> flow:Strategic_Discount_Approval",
    ]
    assert result.edge_count == 3
    assert result.truncated is False


def test_graph_neighborhood_two_hops_is_superset_of_one_hop(graph_path: Path) -> None:
    one = graph_neighborhood(graph_path, "flow:Strategic_Discount_Approval", hops=1)
    two = graph_neighborhood(graph_path, "flow:Strategic_Discount_Approval", hops=2)

    assert set(one.edges).issubset(set(two.edges))
    assert "apex:Policy -> writes -> field:ApprovalStatus" in two.edges
    assert "apex:Unrelated -> calls -> apex:AlsoUnrelated" not in two.edges
    assert two.edge_count == len(two.edges) == 4
    assert two.hops == 2


def test_graph_neighborhood_accepts_alternate_edge_keys(tmp_path: Path) -> None:
    edges = [
        {"source": "page:Workbench", "relation": "renders", "target": "lwc:DealPanel"},
        {"source": "lwc:DealPanel", "type": "calls", "target": "apex:DealController"},
    ]
    path = _graph(tmp_path, edges, name="alternate.json")

    result = graph_neighborhood(path, "page:Workbench", hops=2)

    assert result.edges == [
        "lwc:DealPanel -> calls -> apex:DealController",
        "page:Workbench -> renders -> lwc:DealPanel",
    ]


@pytest.mark.parametrize("hops", (0, 3, -1, 10))
def test_graph_neighborhood_rejects_invalid_hops(graph_path: Path, hops: int) -> None:
    with pytest.raises(ContextFeedError) as error:
        graph_neighborhood(graph_path, "flow:Strategic_Discount_Approval", hops=hops)
    assert error.value.code == "HOPS_INVALID"


def test_graph_neighborhood_unknown_entity_returns_empty(graph_path: Path) -> None:
    result = graph_neighborhood(graph_path, "apex:DoesNotExist", hops=2)

    assert result.edges == []
    assert result.edge_count == 0
    assert result.truncated is False


def test_graph_neighborhood_is_deterministic(graph_path: Path) -> None:
    first = graph_neighborhood(graph_path, "flow:Strategic_Discount_Approval", hops=2)
    second = graph_neighborhood(graph_path, "flow:Strategic_Discount_Approval", hops=2)

    assert first.model_dump(by_alias=True) == second.model_dump(by_alias=True)
    assert first.edges == sorted(first.edges)
    assert len(first.edges) == len(set(first.edges))


def test_graph_neighborhood_caps_edges_and_marks_truncation(tmp_path: Path) -> None:
    edges = [
        {"from": "flow:Seed", "relation": "calls", "to": f"apex:Target{index:02d}"}
        for index in range(10)
    ]
    path = _graph(tmp_path, edges, name="wide.json")

    result = graph_neighborhood(path, "flow:Seed", maximum_edges=4)

    assert result.truncated is True
    assert result.edge_count == 4
    assert result.edges == [
        "flow:Seed -> calls -> apex:Target00",
        "flow:Seed -> calls -> apex:Target01",
        "flow:Seed -> calls -> apex:Target02",
        "flow:Seed -> calls -> apex:Target03",
    ]


def test_graph_neighborhood_deduplicates_repeated_edges(tmp_path: Path) -> None:
    edges = [
        {"from": "flow:Seed", "relation": "calls", "to": "apex:Policy"},
        {"from": "flow:Seed", "relation": "calls", "to": "apex:Policy"},
    ]
    path = _graph(tmp_path, edges, name="duplicate.json")

    result = graph_neighborhood(path, "flow:Seed")

    assert result.edges == ["flow:Seed -> calls -> apex:Policy"]
    assert result.edge_count == 1
