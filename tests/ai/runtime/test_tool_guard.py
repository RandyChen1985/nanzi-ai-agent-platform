"""未注册工具调用（ghost tool）拦截说明的回归测试。"""

from types import SimpleNamespace

from app.services.ai.runtime.agentscope.tool_guard import (
    describe_unregistered_tool_call,
)

MCP_PLATFORM_TOOL_NAME = "mcp-public-admin-ivsom-mcp-server:mcp_query_abnormal_list"
MCP_MODEL_TOOL_NAME = "mcp_mcp-public-admin-ivsom-mcp-server_mcp_query_abnor_c243c57fdb"


def _mcp_spec() -> SimpleNamespace:
    return SimpleNamespace(name=MCP_MODEL_TOOL_NAME, display_name=MCP_PLATFORM_TOOL_NAME)


def test_unregistered_tool_call_explains_mcp_alias_mismatch():
    described = describe_unregistered_tool_call(MCP_PLATFORM_TOOL_NAME, [_mcp_spec()])

    assert described["title"] == f"⚠️ 工具调用已拦截: {MCP_PLATFORM_TOOL_NAME}"
    assert "内部标识" in described["details"]
    assert MCP_MODEL_TOOL_NAME in described["details"]
    assert "模型已收到错误反馈" in described["details"]


def test_unregistered_tool_call_keeps_generic_message_for_unknown_name():
    described = describe_unregistered_tool_call("made_up_tool", [_mcp_spec()])

    assert described["title"] == "⚠️ 工具调用已拦截: made_up_tool"
    assert "未在本轮注册" in described["details"]
    assert "未在本智能体注册" not in described["details"]


def test_unregistered_tool_call_tolerates_specs_without_display_name():
    described = describe_unregistered_tool_call("Bash", [SimpleNamespace(name="Read")])

    assert "未在本轮注册" in described["details"]
