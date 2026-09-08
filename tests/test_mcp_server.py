from neo_sf_q_intel.config import Settings
from neo_sf_q_intel.mcp_server import build_mcp
from neo_sf_q_intel.service import AssuranceService
from tests.test_workflow import source


async def test_mcp_2_server_registers_and_executes_shared_services() -> None:
    server = build_mcp(Settings(allow_llm=False), AssuranceService(source()))

    tools = await server.list_tools()
    result = await server.call_tool(
        "analyze_change", {"requirement": "Change Deal Workbench layout"}
    )

    assert {tool.name for tool in tools} == {
        "analyze_change",
        "inspect_salesforce",
        "search_evidence",
    }
    assert result.content
