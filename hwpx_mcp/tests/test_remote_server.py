from hwpx_mcp.remote_server import mcp
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
    assert tools["fill_hwpx_template"].meta["ui"]["resourceUri"] == WIDGET_URI


def test_remote_server_registers_optional_mcp_app_resource():
    resources = mcp._resource_manager.list_resources()

    assert len(resources) == 1
    assert str(resources[0].uri) == WIDGET_URI
    assert resources[0].mime_type == "text/html;profile=mcp-app"
