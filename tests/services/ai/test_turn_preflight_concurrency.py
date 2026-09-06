import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.services.ai.agent_service import AgentService, TurnPreflightContext


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_gather_turn_preflight_context_runs_concurrently(monkeypatch):
    """验证前置 I/O 任务是并发执行（max_active >= 2），而不是串行累加等待。"""
    active_count = 0
    max_active = 0

    async def mock_async_work(result, sleep_sec=0.01):
        nonlocal active_count, max_active
        active_count += 1
        max_active = max(max_active, active_count)
        await asyncio.sleep(sleep_sec)
        active_count -= 1
        return result

    service = AgentService()

    async def mock_skills(**kwargs):
        return await mock_async_work(["skill_a", "skill_b"])

    async def mock_memory(**kwargs):
        return await mock_async_work(({"ltm": True}, {}, "hint", "preloaded_text"))

    async def mock_user_ctx(user_info):
        return await mock_async_work({"content": "用户画像信息"})

    async def mock_catalog(**kwargs):
        return await mock_async_work("可用权限资源目录")

    async def mock_tools(*args, **kwargs):
        return await mock_async_work(["tool1", "tool2"])

    monkeypatch.setattr(service, "_inject_skills", mock_skills)
    monkeypatch.setattr(service, "_load_memory_context", mock_memory)
    monkeypatch.setattr(service, "_build_user_context_msg", mock_user_ctx)
    monkeypatch.setattr(
        "app.services.ai.accessible_resource_catalog.build_accessible_resource_catalog",
        mock_catalog,
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_assembler.resolve_effective_prompt_tool_names_for_turn",
        mock_tools,
    )

    fake_agent_config = MagicMock()
    fake_agent_config.agent_id = "agent_123"
    fake_agent_config.agent_name = "test_agent"
    fake_agent_config.system_prompt = "你是测试智能体"
    fake_agent_config.tools = []

    fake_turn_decision = MagicMock()
    fake_turn_decision.turn_kind = "general"
    fake_turn_decision.accessible_resources = None

    context: TurnPreflightContext = await service._gather_turn_preflight_context(
        agent_config=fake_agent_config,
        user_info={"user_id": 100, "username": "tester", "role": "user"},
        user_query="测试查询",
        turn_decision=fake_turn_decision,
        messages=[{"role": "user", "content": "测试查询"}],
        debug_options=None,
    )

    # 验证并发度
    assert max_active >= 2, f"Expected concurrency max_active >= 2, got {max_active}"

    # 验证提取的上下文完整性
    assert context.skills_injection == ["skill_a", "skill_b"]
    assert context.ltm_profile == {"ltm": True}
    assert context.memory_recall_hint == "hint"
    assert context.preloaded_memories_text == "preloaded_text"
    assert context.user_profile == "用户画像信息"
    assert context.accessible_resources == "可用权限资源目录"
    assert context.effective_prompt_tool_names == ["tool1", "tool2"]


@pytest.mark.asyncio
async def test_gather_turn_preflight_context_fault_tolerance(monkeypatch):
    """验证当任一前置 I/O（如记忆或目录）抛出异常时，不会阻断其他协程，并且安全回退。"""
    service = AgentService()

    # 模拟技能正常，但记忆和目录抛出异常
    monkeypatch.setattr(
        service,
        "_inject_skills",
        AsyncMock(return_value=["resilient_skill"]),
    )
    monkeypatch.setattr(
        service,
        "_load_memory_context",
        AsyncMock(side_effect=RuntimeError("Redis connection timeout")),
    )
    monkeypatch.setattr(
        "app.services.ai.accessible_resource_catalog.build_accessible_resource_catalog",
        AsyncMock(side_effect=Exception("Database lock error")),
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_assembler.resolve_effective_prompt_tool_names_for_turn",
        AsyncMock(return_value=["safe_tool"]),
    )

    fake_agent_config = MagicMock()
    fake_agent_config.agent_id = "agent_fault"
    fake_agent_config.agent_name = "test_agent"
    fake_agent_config.system_prompt = "你是容错智能体"
    fake_agent_config.tools = []

    fake_turn_decision = MagicMock()
    fake_turn_decision.turn_kind = "general"
    fake_turn_decision.accessible_resources = None

    context: TurnPreflightContext = await service._gather_turn_preflight_context(
        agent_config=fake_agent_config,
        user_info={"user_id": 101, "username": "tester2"},
        user_query="容错测试",
        turn_decision=fake_turn_decision,
        messages=[{"role": "user", "content": "容错测试"}],
        debug_options=None,
    )

    # 正常协程的结果依然能够正确拿到
    assert context.skills_injection == ["resilient_skill"]
    assert context.effective_prompt_tool_names == ["safe_tool"]

    # 异常协程的结果平稳回退至空值，主流程不崩溃
    assert context.ltm_profile is None
    assert context.memory_recall_hint is None
    assert context.accessible_resources is None
