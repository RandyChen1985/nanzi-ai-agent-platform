from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def chat_config():
    return ChatConfig(
        agent_id="sys-agent-chat",
        agent_name="main",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a test agent.",
        tools=["test_tool"],
    )


def _main_runner(chat_config, **kwargs):
    chat_config.agent_id = "sys-agent-chat"
    chat_config.agent_name = "main"
    return AssistantAgentRunner(
        config=chat_config,
        trace_id="test-data-guard",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        **kwargs,
    )


def test_data_query_capability_target_uses_agent_name(chat_config):
    """工具预检应按 data_query 能力动态选择可委派智能体标识，而非固定 chat-bi。"""
    runner = _main_runner(chat_config)
    agents = [
        SimpleNamespace(
            id="agent-data-custom",
            name="biz-data-agent",
            display_name="业务数据专家",
            capabilities=["data_query"],
        ),
        SimpleNamespace(
            id="agent-knowledge",
            name="knowledge-base",
            display_name="知识库助手",
            capabilities=["knowledge_base"],
        ),
    ]

    assert runner._build_sub_agent_targets_by_capability(agents) == {
        "data_query": "biz-data-agent",
        "knowledge_base": "knowledge-base",
    }


@pytest.mark.asyncio
async def test_pending_tool_attempt_skips_intercept(chat_config):
    """待确认工具调用算已尝试，弱查数词（查一下）也不应误拦。"""
    runner = _main_runner(chat_config)

    async def fake_core(_history):
        yield {
            "type": "permission_required",
            "status": "pending",
            "title": "需要确认工具调用: Bash",
            "tool_call": {"name": "Bash", "args": {"command": "uptime"}},
        }

    with patch.object(runner, "_execute_core", fake_core):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下系统负载"}]):
            events.append(chunk)

    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert any(e.get("type") == "permission_required" for e in events)


@pytest.mark.asyncio
async def test_expert_mode_still_uses_platform_grounding_gate(chat_config):
    """专家模式可跳过旧查数规则，但不能绕过平台级事实取证门禁。"""
    hallucinated = (
        "| 主机名 | IP 地址 |\n"
        "| --- | --- |\n"
        "| app-01 | 192.168.1.10 |\n"
    )
    runner = _main_runner(
        chat_config,
        route_hints={"direct_agent_selection": True},
    )

    async def fake_core(_history):
        yield {"content": hallucinated}

    with patch.object(runner, "_execute_core", fake_core):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下资产列表"}]):
            events.append(chunk)

    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    content = "".join(str(e.get("content") or "") for e in events)
    assert hallucinated in content
    assert content.count("风险提示") == 1
    assert not any(e.get("type") == "grounding_blocked" for e in events)


@pytest.mark.asyncio
async def test_web_search_table_without_internal_signals_not_intercepted(chat_config):
    """联网搜索后整理的普通表格，不应被当成编造业务数据。"""
    web_table = (
        "根据联网搜索结果整理如下：\n"
        "| 标题 | 来源 |\n"
        "| --- | --- |\n"
        "| AI 新进展 | example.com |\n"
    )
    runner = _main_runner(chat_config)

    async def fake_core(_history):
        yield {"content": web_table}

    with patch.object(runner, "_execute_core", fake_core):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "联网搜一下最新 AI 新闻"}]):
            events.append(chunk)

    assert not any(e.get("title") == "引导切换数据智能体" for e in events)


@pytest.mark.asyncio
async def test_auto_main_preserves_internal_asset_table_with_risk_warning(chat_config):
    """自动路由主助手的疑似编造资产表应保留正文并追加高风险提示。"""
    hallucinated = (
        "为您查询到以下机器配置信息：\n"
        "| 主机名 | IP 地址 | 配置 |\n"
        "| --- | --- | --- |\n"
        "| app-01 | 192.168.1.10 | 8C16G |\n"
    )
    runner = _main_runner(chat_config)
    data_agent = SimpleNamespace(
        id="agent-data-custom",
        display_name="业务数据专家",
        name="biz-data-agent",
        capabilities=["data_query"],
    )

    async def fake_core(_history):
        yield {"content": hallucinated}

    with patch.object(runner, "_execute_core", fake_core), patch(
        "app.services.ai.runners.assistant_agent_runner.AgentManagerService.list_allowed_agents",
        AsyncMock(return_value=[data_agent]),
    ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下资产列表和设备清单"}]):
            events.append(chunk)

    content = "".join(str(e.get("content") or "") for e in events)
    assert hallucinated in content
    assert content.count("风险提示") == 1
    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert not any(e.get("type") == "grounding_blocked" for e in events)
