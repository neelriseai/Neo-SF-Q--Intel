import json

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
        "inspect_salesforce",
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
