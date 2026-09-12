import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.mcp_server import build_mcp
from neo_sf_q_intel.service import AssuranceService
from tests.test_workflow import source


async def test_mcp_2_server_registers_and_executes_shared_services() -> None:
    server = build_mcp(Settings(_env_file=None, allow_llm=False), AssuranceService(source()))

    tools = await server.list_tools()
    result = await server.call_tool(
        "analyze_change", {"requirement": "Change Deal Workbench layout"}
    )

    assert {tool.name for tool in tools} == {
        "analyze_change",
        "analyze_current_candidate",
        "graph_neighborhood",
        "inspect_salesforce",
        "knowledge_index",
        "knowledge_section",
        "run_live_salesforce_baseline",
        "search_evidence",
    }
    assert result.content
    body = json.loads(result.content[0].text)
    assert body["request"]["change_intent"] == "INFORMATIONAL"
    assert body["impacts"] == []


@pytest.mark.parametrize("alias", [None, "unclassified-org", "production", "../unsafe-org"])
async def test_salesforce_inspection_is_unavailable_without_any_transport(
    alias, monkeypatch
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Unavailable Salesforce inspection must not resolve or invoke a transport")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("neo_sf_q_intel.salesforce_cli.SalesforceCLI.org_status", forbidden)
    monkeypatch.setattr("neo_sf_q_intel.salesforce_cli.SalesforceCLI.rest_get", forbidden)
    monkeypatch.setattr(Settings, "require_operator_alias", forbidden)
    settings = Settings(_env_file=None, allow_llm=False, sf_operator_alias=alias)
    server = build_mcp(settings, AssuranceService(source()))

    result = await server.call_tool("inspect_salesforce", {})

    assert not result.is_error
    assert json.loads(result.content[0].text) == {
        "schema_version": "1.0.0",
        "status": "UNAVAILABLE",
        "capability_id": "runtime.salesforce-live-evidence",
        "reason_code": "SALESFORCE_ORG_CLASSIFICATION_UNAVAILABLE",
        "execution_status": "NOT_RUN",
        "evidence_ids": [],
        "release_eligible": False,
    }


async def test_unknown_mcp_tool_cannot_invoke_salesforce_transport(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Unknown tools must not invoke subprocesses")

    monkeypatch.setattr("subprocess.run", forbidden)
    server = build_mcp(Settings(_env_file=None, allow_llm=False), AssuranceService(source()))

    with pytest.raises(ToolError, match="Unknown tool"):
        await server.call_tool("inspect_production_salesforce", {})


WORKBENCH = """# Strategic Deal Workbench

## Policy Rules

Approval matrix body.
"""


def _knowledge_repo(root: Path) -> Path:
    pages = root / "knowledge-repo" / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    (pages / "strategic-deal-workbench.md").write_bytes(WORKBENCH.encode("utf-8"))
    return root


def _application_graph(root: Path) -> Path:
    knowledge = root / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": [],
        "edges": [{"from": "flow:Approval", "relation": "calls", "to": "apex:Policy"}],
    }
    (knowledge / "application-graph.json").write_bytes(json.dumps(payload).encode("utf-8"))
    return root


async def test_knowledge_index_tool_reads_the_host_repository_knowledge_repo(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(_knowledge_repo(tmp_path))
    server = build_mcp(Settings(_env_file=None, allow_llm=False), AssuranceService(source()))

    result = await server.call_tool("knowledge_index", {})

    assert not result.is_error
    body = json.loads(result.content[0].text)
    assert [document["key"] for document in body["pages"]] == ["strategic-deal-workbench"]
    assert body["pages"][0]["headings"] == ["Strategic Deal Workbench", "Policy Rules"]


async def test_knowledge_section_tool_returns_one_heading_block(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(_knowledge_repo(tmp_path))
    server = build_mcp(Settings(_env_file=None, allow_llm=False), AssuranceService(source()))

    result = await server.call_tool(
        "knowledge_section", {"page": "strategic-deal-workbench", "section": "Policy Rules"}
    )

    assert not result.is_error
    body = json.loads(result.content[0].text)
    assert body["section"] == "Policy Rules"
    assert "Approval matrix body." in body["body"]


@pytest.mark.parametrize(
    "arguments",
    ({}, {"page": "strategic-deal-workbench", "module": "strategic-deal-policy"}),
)
async def test_knowledge_section_tool_rejects_invalid_selectors(
    tmp_path: Path, monkeypatch, arguments: dict
) -> None:
    monkeypatch.chdir(_knowledge_repo(tmp_path))
    server = build_mcp(Settings(_env_file=None, allow_llm=False), AssuranceService(source()))

    with pytest.raises(ToolError) as error:
        await server.call_tool("knowledge_section", arguments)
    assert str(error.value).endswith("SELECTOR_INVALID")


async def test_context_feed_error_code_surfaces_without_any_path_text(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(_knowledge_repo(tmp_path))
    server = build_mcp(Settings(_env_file=None, allow_llm=False), AssuranceService(source()))

    with pytest.raises(ToolError) as error:
        await server.call_tool("knowledge_section", {"page": "absent-page"})

    message = str(error.value)
    assert message.endswith("SECTION_NOT_FOUND")
    assert "/" not in message
    assert "\\" not in message
    assert tmp_path.name not in message
    assert "knowledge-repo" not in message


async def test_graph_neighborhood_tool_reads_the_configured_salesforce_application_graph(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        allow_llm=False,
        salesforce_app_root=_application_graph(tmp_path),
    )
    server = build_mcp(settings, AssuranceService(source()))

    result = await server.call_tool("graph_neighborhood", {"entity_id": "flow:Approval"})

    assert not result.is_error
    body = json.loads(result.content[0].text)
    assert body["edges"] == ["flow:Approval -> calls -> apex:Policy"]
    assert body["edgeCount"] == 1


async def test_graph_neighborhood_tool_returns_no_edges_for_an_unknown_entity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        allow_llm=False,
        salesforce_app_root=_application_graph(tmp_path),
    )
    server = build_mcp(settings, AssuranceService(source()))

    result = await server.call_tool(
        "graph_neighborhood", {"entity_id": "apex:DoesNotExist", "hops": 2}
    )

    assert not result.is_error
    body = json.loads(result.content[0].text)
    assert body["edges"] == []
    assert body["edgeCount"] == 0
    assert body["truncated"] is False
