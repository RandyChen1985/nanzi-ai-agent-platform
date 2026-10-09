"""未注册工具调用（ghost tool）拦截说明的回归测试。"""

from types import SimpleNamespace

import pytest

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


def test_unregistered_tool_call_reports_effective_version_and_registered_count():
    """排障要能一眼看出「对话实际用的是哪个版本」。

    配置页保存的是草稿，只有发布后的版本才在对话中生效。旧文案只说
    "可能未发布"，却不给出当前生效的版本，用户只能靠猜。
    """
    described = describe_unregistered_tool_call(
        "made_up_tool",
        [_mcp_spec(), SimpleNamespace(name="Read")],
        agent_version="v3",
    )

    assert "未在本轮注册" in described["details"]
    assert "本轮生效配置 v3" in described["details"]
    assert "共注册 2 个工具" in described["details"]


def test_unregistered_tool_call_without_version_still_reports_count():
    described = describe_unregistered_tool_call(
        "made_up_tool",
        [SimpleNamespace(name="Read")],
    )

    assert "共注册 1 个工具" in described["details"]
    assert "本轮生效配置" not in described["details"]


@pytest.mark.asyncio
async def test_event_stream_ghost_tool_card_carries_effective_version():
    """事件流拦截卡片要带上本轮生效版本，用户才能对照配置页里未发布的草稿。"""
    from app.services.ai.runtime.agentscope.event_stream import (
        map_standard_agentscope_event,
        new_native_stream_state,
    )

    state = new_native_stream_state()
    event = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="t1",
        tool_call_name="search_knowledge_base",
    )

    chunks = [
        chunk
        async for chunk in map_standard_agentscope_event(
            event,
            state=state,
            tools=[SimpleNamespace(name="Read")],
            agent_version="v3",
        )
    ]

    error_chunks = [
        chunk
        for chunk in chunks
        if chunk.get("status") == "error" and chunk.get("category") == "tool"
    ]
    assert len(error_chunks) == 1
    assert error_chunks[0]["title"] == "⚠️ 工具调用已拦截: search_knowledge_base"
    assert "本轮生效配置 v3" in error_chunks[0]["details"]
    assert "共注册 1 个工具" in error_chunks[0]["details"]
