"""会话工作区并发预热（Workspace Prewarm）的契约测试。

优化 A（TTFT）：把 `assistant_agent_runner._build_native_agent` 中首句必做、
且阻塞在首个 token 之前的工作区构建（建目录 + 技能硬链接 + LocalWorkspace
初始化），并入 AssembleStep 已有的 turn preflight 并发池提前触发一次，从而
让 execute 阶段 `get_local_workspace` 命中进程内 `_workspace_cache`，首句不再
串行累加这段 I/O 在 token 前。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.ai.agent_service import AgentService


pytestmark = pytest.mark.no_infrastructure


def _fake_agent_config(**overrides) -> MagicMock:
    config = MagicMock()
    config.agent_id = "agent_123"
    config.agent_name = "test_agent"
    config.system_prompt = "你是测试智能体"
    config.tools = []
    config.skills_custom = True
    config.skills = ["skill_a", "skill_b"]
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


def _fake_turn_decision() -> MagicMock:
    decision = MagicMock()
    decision.turn_kind = "general"
    decision.accessible_resources = None
    return decision


async def _call_preflight(service: AgentService, **kwargs) -> object:
    base_kwargs = {
        "agent_config": _fake_agent_config(),
        "user_info": {"user_id": 100, "username": "tester", "role": "user"},
        "user_query": "测试查询",
        "turn_decision": _fake_turn_decision(),
        "messages": [{"role": "user", "content": "测试查询"}],
        "debug_options": None,
    }
    base_kwargs.update(kwargs)
    return await service._gather_turn_preflight_context(**base_kwargs)


@pytest.mark.asyncio
async def test_preflight_prewarms_workspace_when_conversation_id_provided(monkeypatch):
    """提供 conversation_id 时应并发预热工作区，且参数与 runner 完全对齐。"""
    seen: dict = {}

    async def fake_get_local_workspace(**kwargs):
        seen.update(kwargs)
        return (None, MagicMock())

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.get_local_workspace",
        fake_get_local_workspace,
    )
    service = AgentService()
    # 其余预取协程加速，避免真实 I/O
    monkeypatch.setattr(service, "_inject_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_load_memory_context",
        AsyncMock(return_value=(None, None, None, None)),
    )
    monkeypatch.setattr(
        service,
        "_build_user_context_msg",
        AsyncMock(return_value={"content": "用户画像信息"}),
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_assembler.resolve_effective_prompt_tool_names_for_turn",
        AsyncMock(return_value=[]),
    )

    await _call_preflight(service, conversation_id="conv-abc")

    assert seen.get("conversation_id") == "conv-abc"
    assert seen.get("user_id") == "100"
    assert seen.get("user_name") == "tester"
    # 与 runner._build_native_agent 的 skills 参数保持一致
    assert seen.get("skills_custom") is True
    assert seen.get("allowed_global_skills") == ["skill_a", "skill_b"]


@pytest.mark.asyncio
async def test_preflight_skips_workspace_prewarm_without_conversation_id(monkeypatch):
    """缺少 conversation_id 时不触发工作区预热（get_local_workspace 无法建目录）。"""
    called = False

    async def fake_get_local_workspace(**kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.get_local_workspace",
        fake_get_local_workspace,
    )
    monkeypatch.setattr(service := AgentService(), "_inject_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_load_memory_context",
        AsyncMock(return_value=(None, None, None, None)),
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_assembler.resolve_effective_prompt_tool_names_for_turn",
        AsyncMock(return_value=[]),
    )

    await _call_preflight(service)

    assert called is False


@pytest.mark.asyncio
async def test_preflight_workspace_prewarm_failure_is_fault_tolerant(monkeypatch):
    """工作区预热抛异常时不得阻断其它预取协程与主流程。"""
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.get_local_workspace",
        AsyncMock(side_effect=RuntimeError("docker init failed")),
    )
    service = AgentService()
    monkeypatch.setattr(service, "_inject_skills", AsyncMock(return_value=["resilient_skill"]))
    monkeypatch.setattr(
        service,
        "_load_memory_context",
        AsyncMock(side_effect=RuntimeError("redis timeout")),
    )
    monkeypatch.setattr(
        "app.services.ai.accessible_resource_catalog.build_accessible_resource_catalog",
        AsyncMock(side_effect=Exception("db lock")),
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_assembler.resolve_effective_prompt_tool_names_for_turn",
        AsyncMock(return_value=["safe_tool"]),
    )

    context = await _call_preflight(service, conversation_id="conv-abc")

    # 其它正常协程结果不受影响
    assert context.skills_injection == ["resilient_skill"]
    assert context.effective_prompt_tool_names == ["safe_tool"]
    # 异常协程安全回退
    assert context.ltm_profile is None
    assert context.accessible_resources is None


@pytest.mark.asyncio
async def test_preflight_workspace_prewarm_handles_none_user_info(monkeypatch):
    """当 user_info 为 None 时，工作区预热不抛 AttributeError 且正常调用。"""
    seen: dict = {}

    async def fake_get_local_workspace(**kwargs):
        seen.update(kwargs)
        return (None, MagicMock())

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.get_local_workspace",
        fake_get_local_workspace,
    )
    service = AgentService()
    monkeypatch.setattr(service, "_inject_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        service,
        "_load_memory_context",
        AsyncMock(return_value=(None, None, None, None)),
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_assembler.resolve_effective_prompt_tool_names_for_turn",
        AsyncMock(return_value=[]),
    )

    await _call_preflight(service, conversation_id="conv-none-user", user_info=None)

    assert seen.get("conversation_id") == "conv-none-user"
    assert seen.get("user_id") is None
    assert seen.get("user_name") is None