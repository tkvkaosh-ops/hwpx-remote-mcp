from hwpx_mcp.remote_server import mcp
from mcp.server.fastmcp import FastMCP
from hwpx_mcp.tools.remote_documents import register_remote_document_tools
from hwpx_mcp.tools.hwpx_widget import WIDGET_URI


def test_remote_server_exposes_only_high_level_tools():
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    assert set(tools) == {
        "create_hwpx_document",
        "inspect_hwpx_template",
        "fill_hwpx_template",
    }
    assert tools["inspect_hwpx_template"].meta["openai/fileParams"] == [
        "template_file"
    ]
    assert tools["fill_hwpx_template"].meta["openai/fileParams"] == [
        "template_file"
    ]
    assert "ui" not in tools["fill_hwpx_template"].meta
    assert "openai/outputTemplate" not in tools["fill_hwpx_template"].meta


def test_remote_widget_requires_explicit_host_opt_in(monkeypatch):
    monkeypatch.setenv("HWPX_ENABLE_WIDGET", "true")
    optional_server = FastMCP("widget-opt-in-test")
    register_remote_document_tools(optional_server)
    tools = {tool.name: tool for tool in optional_server._tool_manager.list_tools()}
    assert tools["fill_hwpx_template"].meta["ui"]["resourceUri"] == WIDGET_URI
    assert tools["fill_hwpx_template"].meta["openai/fileParams"] == ["template_file"]


def test_remote_server_registers_optional_mcp_app_resource():
    resources = mcp._resource_manager.list_resources()

    assert len(resources) == 1
    assert str(resources[0].uri) == WIDGET_URI
    assert resources[0].mime_type == "text/html;profile=mcp-app"
