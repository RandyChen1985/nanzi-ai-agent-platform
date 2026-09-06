import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.agent_service import AgentService
from app.services.ai.pipeline import PipelineContext
from app.services.ai.pipeline.steps.execution_step import ExecutionStep


@pytest.mark.asyncio
async def test_ltm_applied_in_stream_meta():
    """
    测试 LTM 被成功加载并注入 SSE stream meta 块的场景。
    """
    service = AgentService()
    mock_ltm_data = {
        "user_preferred_city": "临港",
        "work_style": "premium"
    }

    # 1. 验证 _load_memory_context 正常加载 LTM
    with patch("app.services.ai.memory_service.ltm_service.fetch_memory", new_callable=AsyncMock, return_value=mock_ltm_data):
        ltm_profile, ltm_loaded_data, _, _ = await service._load_memory_context(
            early_turn_kind="chat",
            user_info={"user_id": "test_user_99"},
            user_query="你好",
            debug_options=None,
        )
        assert ltm_loaded_data == mock_ltm_data
        assert ltm_profile is not None

    # 2. 验证 ExecutionStep 正确将 LTM 数据注入 meta 事件
    class DummyAgentConfig:
        name = "test_agent"
        agent_name = "test_agent"
        agent_display_name = "测试助手"
        system_prompt = "helper"
        engine_type = "LOCAL"
        model_name = "test-model"

    class DummyExecutor:
        async def execute(self, messages):
            yield {"content": "hello"}

    context = PipelineContext(
        messages=[{"role": "user", "content": "你好"}],
        user_info={"user_id": "test_user_99"},
        conversation_id="test_conv",
    )
    context.shared_state["agent_config"] = DummyAgentConfig()
    context.ltm_profile = ltm_profile
    context.ltm_loaded_data = ltm_loaded_data

    mock_service = MagicMock()
    mock_service._dispatch_executor = AsyncMock(return_value=DummyExecutor())

    step = ExecutionStep(mock_service)
    chunks = []
    async for chunk in step.run(context):
        chunks.append(chunk)

    meta_events = [c for c in chunks if c.get("type") == "meta"]
    assert len(meta_events) == 1
    assert meta_events[0].get("ltm_applied") is True
    assert meta_events[0].get("ltm_data") == mock_ltm_data


@pytest.mark.asyncio
async def test_ignore_ltm_in_stream_meta():
    """
    测试当在 debug_options 中传入 ignore_ltm=True 时，跳过加载和注入 LTM。
    """
    service = AgentService()
    with patch("app.services.ai.memory_service.ltm_service.fetch_memory", new_callable=AsyncMock) as mock_fetch:
        ltm_profile, ltm_loaded_data, _, _ = await service._load_memory_context(
            early_turn_kind="chat",
            user_info={"user_id": "test_user_99"},
            user_query="你好",
            debug_options={"ignore_ltm": True},
        )
        assert ltm_loaded_data is None
        assert ltm_profile is None
        mock_fetch.assert_not_called()

    # 验证当 context 中无 LTM 时 ExecutionStep meta 不包含 ltm_applied
    class DummyAgentConfig:
        name = "test_agent"
        agent_name = "test_agent"
        agent_display_name = "测试助手"
        system_prompt = "helper"
        engine_type = "LOCAL"
        model_name = "test-model"

    class DummyExecutor:
        async def execute(self, messages):
            yield {"content": "hello"}

    context = PipelineContext(
        messages=[{"role": "user", "content": "你好"}],
        user_info={"user_id": "test_user_99"},
        conversation_id="test_conv",
    )
    context.shared_state["agent_config"] = DummyAgentConfig()
    context.ltm_profile = None
    context.ltm_loaded_data = None

    mock_service = MagicMock()
    mock_service._dispatch_executor = AsyncMock(return_value=DummyExecutor())

    step = ExecutionStep(mock_service)
    chunks = []
    async for chunk in step.run(context):
        chunks.append(chunk)

    meta_events = [c for c in chunks if c.get("type") == "meta"]
    assert len(meta_events) == 1
    assert "ltm_applied" not in meta_events[0]
    assert "ltm_data" not in meta_events[0]
