"""Tests for AgentService pipeline steps (Phase 1)."""

import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.pipeline.steps.finalize_step import FinalizeStep
from app.services.ai.pipeline.steps.preflight_step import PreflightStep


@pytest.mark.asyncio
async def test_preflight_step_init_chunk_and_success():
    """Verify that PreflightStep emits initial chunk and passes when quota is ok."""
    context = PipelineContext(
        messages=[{"role": "user", "content": "hello world"}],
        user_info={"user_id": 123, "username": "tester"},
        conversation_id="conv_test_1",
    )
    context.lane_user_id = 123

    step = PreflightStep()

    with patch.object(step, "_quota_block_message", new_callable=AsyncMock) as mock_quota, \
         patch("app.services.ai.runtime.session_run_lane.conversation_run_lane.is_locked", new_callable=AsyncMock) as mock_is_locked:
        mock_quota.return_value = None
        mock_is_locked.return_value = False

        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert chunks[0]["status"] == "init"
        assert chunks[0]["trace_id"] == context.trace_id
        assert context.execution_status == "success"
        assert context.messages[0]["content"] == "hello world"


@pytest.mark.asyncio
async def test_preflight_step_quota_block():
    """Verify that PreflightStep stops early when user quota is exceeded."""
    context = PipelineContext(
        messages=[{"role": "user", "content": "run query"}],
        user_info={"user_id": 999, "username": "no_quota"},
        conversation_id="conv_test_quota",
    )
    context.lane_user_id = 999

    step = PreflightStep()

    with patch.object(step, "_quota_block_message", new_callable=AsyncMock) as mock_quota:
        mock_quota.return_value = "账户 Token 配额已耗尽，请充值后重试。"

        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0]["status"] == "init"
        assert chunks[1]["type"] == "error"
        assert chunks[1]["status"] == "quota_exceeded"
        assert "配额已耗尽" in chunks[1]["content"]
        assert context.execution_status == "quota_exceeded"


@pytest.mark.asyncio
async def test_finalize_step_token_aggregation_and_status():
    """Verify that FinalizeStep aggregates tokens and reports run status."""
    from app.schemas.agent import AgentExecutionStep

    context = PipelineContext(
        messages=[{"role": "user", "content": "analyze table"}],
        user_info={"user_id": 123},
        conversation_id="conv_test_finalize",
    )
    context.lane_user_id = 123
    context.full_response_content = "Here is your data analysis result."
    context.execution_status = "success"
    context.trace_buffer = [
        AgentExecutionStep(step_number=1, event_type="thought", raw_log="analyzing")
    ]

    step = FinalizeStep()

    with patch("app.services.ai.pipeline.steps.finalize_step.aggregate_tokens_from_trace_buffer") as mock_agg, \
         patch("app.services.ai.agent_service._persist_assistant_message_and_summary", new_callable=AsyncMock) as mock_persist, \
         patch("app.services.ai.agent_service._should_persist_turn_history", return_value=True):
        mock_agg.return_value = (100, 200, 300)

        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

        meta_chunk = next((c for c in chunks if c.get("type") == "meta"), None)
        assert meta_chunk is not None
        assert meta_chunk["prompt_tokens"] == 100
        assert meta_chunk["completion_tokens"] == 200
        assert meta_chunk["total_tokens"] == 300

        status_chunk = next((c for c in chunks if c.get("type") == "run_status"), None)
        assert status_chunk is not None
        assert status_chunk["status"] == "success"
        assert status_chunk["persisting"] is True

        mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_step_history_loading_and_window():
    """Verify that ContextStep loads server history, saves user message, and trims context window."""
    from app.services.ai.pipeline.steps.context_step import ContextStep

    context = PipelineContext(
        messages=[{"role": "user", "content": "hello agent"}],
        user_info={"user_id": 123},
        conversation_id="conv_context_test",
    )
    context.shared_state["process_timeline"] = []

    step = ContextStep()

    fake_history = [
        {"role": "user", "content": "prev query"},
        {"role": "assistant", "content": "prev answer"},
    ]

    with patch("app.services.ai.memory_service.memory_service.get_history", new_callable=AsyncMock) as mock_get_history, \
         patch("app.services.ai.memory_service.memory_service.add_message", new_callable=AsyncMock) as mock_add_message, \
         patch("app.services.ai.memory_service.memory_service.get_context_snapshot", new_callable=AsyncMock) as mock_snapshot, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        mock_get_history.return_value = fake_history
        mock_snapshot.return_value = None
        mock_config.return_value = "50"

        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

        mock_get_history.assert_awaited_once_with("123", "conv_context_test")
        mock_add_message.assert_awaited_once_with(
            "123", "conv_context_test", "user", "hello agent", files=None
        )

        history_log = next((c for c in chunks if c.get("id") == "context:history"), None)
        assert history_log is not None
        assert "会话 conv_context_test" in history_log["details"]
        assert len(context.messages) == 3
        assert context.messages[-1]["content"] == "hello agent"
        assert context.shared_state["user_query"] == "hello agent"
        assert context.user_query == "hello agent"


@pytest.mark.asyncio
async def test_context_step_without_conversation_id():
    """Verify that ContextStep skips server history when conversation_id is None."""
    from app.services.ai.pipeline.steps.context_step import ContextStep

    context = PipelineContext(
        messages=[{"role": "user", "content": "one shot test"}],
        user_info={"user_id": 123},
        conversation_id=None,
    )
    context.shared_state["process_timeline"] = []

    step = ContextStep()

    with patch("app.services.ai.memory_service.memory_service.get_history", new_callable=AsyncMock) as mock_get_history:
        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

        mock_get_history.assert_not_called()
        history_log = next((c for c in chunks if c.get("id") == "context:history"), None)
        assert history_log is not None
        assert "未绑定会话" in history_log["details"]
        assert len(context.messages) == 1



@pytest.mark.asyncio
async def test_assemble_step_prompt_building_and_boundary():
    """Verify that AssembleStep compiles layered system prompt and injects boundary."""
    from app.services.ai.pipeline.steps.assemble_step import AssembleStep
    from app.services.ai.agent_prompts import AgentServicePrompts

    class DummyAgentConfig:
        name = "test_bot"
        system_prompt = "You are a helpful test bot."
        engine_type = "LOCAL"

    dummy_config = DummyAgentConfig()
    context = PipelineContext(
        messages=[{"role": "user", "content": "help me"}],
        user_info={"user_id": 123},
        conversation_id="conv_prompt_1",
    )
    context.shared_state["agent_config"] = dummy_config
    context.shared_state["preparation_started_at"] = 0

    step = AssembleStep()

    with patch(
        "app.services.ai.context_manager.AgentContextManager.setup_context",
        new_callable=AsyncMock,
    ), patch(
        "app.services.ai.pipeline.steps.assemble_step.resolve_prompt_assembler_flags",
        new_callable=AsyncMock,
    ) as mock_flags:
        mock_flags.return_value = (True, True)

        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

        # 验证能力目录日志
        catalog_log = next((c for c in chunks if c.get("id") == "capability:catalog"), None)
        assert catalog_log is not None

        # 验证 Prompt 组装日志
        assembly_log = next((c for c in chunks if c.get("id") == "prompt:assembly"), None)
        assert assembly_log is not None

        # 验证准备就绪日志
        prep_log = next((c for c in chunks if c.get("id") == "preparation:auth_context_capability"), None)
        assert prep_log is not None
        assert prep_log["status"] == "success"

        # 验证系统提示词边界保护
        assert AgentServicePrompts.CHAT_HISTORY_BOUNDARY_PROMPT.strip() in dummy_config.system_prompt
        assert "You are a helpful test bot." in dummy_config.system_prompt
        assert context.shared_state.get("preparation_ready") is True


@pytest.mark.asyncio
async def test_assemble_step_debug_override():
    """Verify that AssembleStep handles debug system_prompt_override correctly."""
    from app.services.ai.pipeline.steps.assemble_step import AssembleStep

    class DummyAgentConfig:
        name = "test_bot"
        system_prompt = "Normal prompt."
        engine_type = "LOCAL"

    dummy_config = DummyAgentConfig()
    context = PipelineContext(
        messages=[{"role": "user", "content": "debug test"}],
        user_info={"user_id": 123},
        debug_options={"system_prompt_override": "DEBUG_OVERRIDE_PROMPT"},
    )
    context.shared_state["agent_config"] = dummy_config

    step = AssembleStep()

    with patch(
        "app.services.ai.context_manager.AgentContextManager.setup_context",
        new_callable=AsyncMock,
    ):
        chunks = []
        async for chunk in step.run(context):
            chunks.append(chunk)

    debug_chunk = next((c for c in chunks if c.get("title") == "Debug: Prompt Override"), None)
    assert debug_chunk is not None
    assert "DEBUG_OVERRIDE_PROMPT" in dummy_config.system_prompt


@pytest.mark.asyncio
async def test_route_step_mention_and_config():
    """Verify that RouteStep extracts @mention and routes correctly."""
    from app.services.ai.pipeline.steps.route_step import RouteStep

    context = PipelineContext(
        messages=[{"role": "user", "content": "@finance_expert 请帮我分析 Q3 财报"}],
        user_info={"user_id": 123},
        conversation_id="conv_route_1",
    )
    context.user_query = "@finance_expert 请帮我分析 Q3 财报"

    step = RouteStep()

    class DummyAgentConfig:
        name = "finance_expert"
        agent_name = "finance_expert"
        agent_display_name = "财务专家"
        system_prompt = "You are a finance expert."
        engine_type = "LOCAL"

    context.shared_state["agent_config"] = DummyAgentConfig()

    chunks = []
    async for chunk in step.run(context):
        chunks.append(chunk)

    assert context.agent_name == "finance_expert"
    assert context.user_query == "请帮我分析 Q3 财报"
    assert context.shared_state["agent_name"] == "finance_expert"
    assert context.shared_state["user_query"] == "请帮我分析 Q3 财报"


@pytest.mark.asyncio
async def test_route_step_permission_denial_stops_before_runtime_setup():
    """入口智能体鉴权失败后不得继续挂载工具或解析模型。"""
    from app.services.ai.pipeline.steps.route_step import RouteStep

    class DummyAgentConfig:
        agent_id = "restricted"
        agent_name = "restricted_agent"
        agent_display_name = "受限智能体"
        agent_type = "GENERAL"
        capabilities = []
        tools = []
        engine_config = {}

    class DeniedRouteService:
        runtime_resolution_called = False

        def _start_route_resolution(self, *, route_events, resolve_kwargs):
            async def resolve():
                return (
                    DummyAgentConfig(),
                    None,
                    1.0,
                    "当前用户无权访问该智能体",
                )

            return asyncio.create_task(resolve())

        async def _resolve_runtime_model_info_safe(self, **kwargs):
            self.runtime_resolution_called = True
            return None

    service = DeniedRouteService()
    context = PipelineContext(
        messages=[{"role": "user", "content": "执行受限任务"}],
        user_info={"user_id": 123},
        conversation_id="conv_permission_denied",
    )
    context.user_query = "执行受限任务"

    with patch(
        "app.services.ai.pipeline.steps.route_step.apply_session_mcp_tools_to_agent_config"
    ) as apply_mcp:
        chunks = [chunk async for chunk in RouteStep(service).run(context)]

    assert any(
        chunk.get("type") == "error"
        and chunk.get("status") == "denied"
        and "无权访问" in str(chunk.get("content") or "")
        for chunk in chunks
    )
    assert context.execution_status == "denied"
    assert context.shared_state["execution_status"] == "denied"
    assert context.shared_state.get("agent_config") is None
    assert service.runtime_resolution_called is False
    apply_mcp.assert_not_called()


@pytest.mark.asyncio
async def test_assemble_step_context_setup_failure_is_fail_closed():
    """用户与资源权限上下文初始化失败时必须中止提示词组装。"""
    from app.services.ai.pipeline.steps.assemble_step import AssembleStep

    class DummyAgentConfig:
        agent_id = "agent-1"
        agent_name = "test_agent"
        agent_display_name = "测试智能体"
        system_prompt = "system"
        engine_type = "LOCAL"
        engine_config = {}

    context = PipelineContext(
        messages=[{"role": "user", "content": "读取受保护数据"}],
        user_info={"user_id": 123},
        conversation_id="conv_context_failure",
    )
    context.shared_state["agent_config"] = DummyAgentConfig()

    with patch(
        "app.services.ai.context_manager.AgentContextManager.setup_context",
        new_callable=AsyncMock,
        side_effect=RuntimeError("permission scope load failed"),
    ):
        with pytest.raises(RuntimeError, match="permission scope load failed"):
            _ = [chunk async for chunk in AssembleStep().run(context)]

    assert context.shared_state["preparation_ready"] is False


@pytest.mark.asyncio
async def test_route_step_scopes_reusable_result_and_updates_turn_decision():
    """知识轮次的结果复用必须按类型重验，并传给实际执行决策。"""
    from app.services.ai.pipeline.steps.route_step import RouteStep
    from app.services.ai.reusable_result import ReusableResultDecision

    class DummyAgentConfig:
        agent_id = "knowledge-agent"
        agent_name = "knowledge_agent"
        agent_display_name = "知识助手"
        agent_type = "KNOWLEDGE"
        capabilities = ["knowledge_base"]
        tools = []
        engine_config = {}

    class ReuseService:
        def __init__(self):
            self.allowed_types = []

        async def _resolve_reusable_result_decision(self, **kwargs):
            self.allowed_types.append(kwargs.get("allowed_result_types"))
            return ReusableResultDecision(
                mode="reuse",
                result={"result_id": "knowledge-result-1", "result_type": "knowledge"},
                reason="selected_result_explicit",
            )

    service = ReuseService()
    context = PipelineContext(
        messages=[{"role": "user", "content": "继续总结上面的知识库结果"}],
        user_info={"user_id": 123},
        agent_id="knowledge-agent",
        reusable_result_id="knowledge-result-1",
    )
    context.user_query = "继续总结上面的知识库结果"
    context.shared_state["agent_config"] = DummyAgentConfig()

    with patch(
        "app.services.ai.pipeline.steps.route_step.apply_session_mcp_tools_to_agent_config"
    ):
        chunks = [chunk async for chunk in RouteStep(service).run(context)]

    decision = context.shared_state["turn_decision"]
    assert service.allowed_types == [None, {"knowledge"}]
    assert decision.turn_kind == "knowledge"
    assert decision.reusable_result_mode == "reuse"
    assert decision.reusable_result_id == "knowledge-result-1"
    assert decision.reusable_result_reason == "selected_result_explicit"
    assert [chunk.get("status") for chunk in chunks if chunk.get("type") == "reusable_result_status"] == [
        "reused"
    ]


@pytest.mark.asyncio
async def test_assemble_step_restores_knowledge_context_and_ltm():
    """知识轮次必须执行专用增强和显式数据集上下文，并注入 LTM。"""
    from app.services.ai.pipeline.steps.assemble_step import AssembleStep
    from app.services.ai.turn_decision import TurnDecision

    class DummyAgentConfig:
        agent_id = "knowledge-agent"
        agent_name = "knowledge_agent"
        agent_display_name = "知识助手"
        system_prompt = "knowledge system"
        engine_type = "LOCAL"
        engine_config = {"dataset_ids": ["kb-1"]}

    config = DummyAgentConfig()
    preflight_ctx = SimpleNamespace(
        skills_injection=[],
        matched_skills_to_log=[],
        effective_prompt_tool_names=[],
        delegable_agent_count=0,
        roster_loaded=False,
        agent_system_prompt=config.system_prompt,
        sub_agents_context=None,
        memory_recall_hint=None,
        preloaded_memories_text=None,
        user_profile=None,
        accessible_resources=None,
        ltm_profile="长期偏好：使用中文",
        ltm_loaded_data={"language": "zh-CN"},
    )
    context = PipelineContext(
        messages=[{"role": "user", "content": "查询知识库"}],
        user_info={"user_id": 123},
        knowledge_dataset_ids=["kb-1"],
    )
    context.user_query = "查询知识库"
    context.shared_state.update(
        {
            "agent_config": config,
            "preflight_ctx": preflight_ctx,
            "turn_decision": TurnDecision(
                route_status="resolved",
                turn_kind="knowledge",
                capability="knowledge_search",
            ),
        }
    )
    assembled = SimpleNamespace(
        full_text="assembled prompt",
        stable_prefix="",
        dynamic_suffix="",
        cache_boundary_enabled=False,
        cache_reorder_enabled=False,
        section_names=[],
        section_char_counts={},
    )

    with patch(
        "app.services.ai.context_manager.AgentContextManager.setup_context",
        new_callable=AsyncMock,
    ) as setup_context, patch(
        "app.services.ai.context_manager.AgentContextManager.enrich_for_knowledge_turn",
        new_callable=AsyncMock,
        return_value=config,
    ) as enrich_knowledge, patch(
        "app.services.ai.pipeline.steps.assemble_step.resolve_prompt_assembler_flags",
        new_callable=AsyncMock,
        return_value=(False, False),
    ), patch(
        "app.services.ai.pipeline.steps.assemble_step.assemble_system_prompt",
        return_value=assembled,
    ) as assemble_prompt:
        _ = [chunk async for chunk in AssembleStep().run(context)]

    enrich_knowledge.assert_awaited_once_with(config, user_query="查询知识库")
    assert setup_context.await_count == 2
    assert setup_context.await_args_list[1].kwargs["require_explicit_dataset"] is True
    assert assemble_prompt.call_args.args[0].ltm_profile == "长期偏好：使用中文"
    assert context.ltm_profile == "长期偏好：使用中文"


@pytest.mark.asyncio
async def test_assemble_step_emits_skill_log_and_raw_prompt_debug_event():
    """模块化组装步骤必须保留技能匹配日志与原始提示词调试协议。"""
    from app.services.ai.pipeline.steps.assemble_step import AssembleStep

    class DummyAgentConfig:
        agent_id = "agent-1"
        agent_name = "main"
        agent_display_name = "主助手"
        system_prompt = "system"
        engine_type = "LOCAL"
        engine_config = {}

    class SkillLogService:
        @staticmethod
        def _build_skill_log_chunk(skill_id, skill_name, details_msg):
            return {
                "type": "log",
                "id": f"skill:{skill_id}",
                "title": skill_name,
                "details": details_msg,
                "status": "success",
            }

    config = DummyAgentConfig()
    preflight_ctx = SimpleNamespace(
        skills_injection=["skill instruction"],
        matched_skills_to_log=[("skill-1", "测试技能", "已匹配")],
        effective_prompt_tool_names=[],
        delegable_agent_count=0,
        roster_loaded=False,
        agent_system_prompt=config.system_prompt,
        sub_agents_context=None,
        memory_recall_hint=None,
        preloaded_memories_text=None,
        user_profile=None,
        accessible_resources=None,
        ltm_profile=None,
        ltm_loaded_data=None,
    )
    context = PipelineContext(
        messages=[{"role": "user", "content": "使用测试技能"}],
        user_info={"user_id": 123},
        debug_options={"return_raw_prompt": True},
    )
    context.user_query = "使用测试技能"
    context.shared_state.update(
        {"agent_config": config, "preflight_ctx": preflight_ctx}
    )
    assembled = SimpleNamespace(
        full_text="assembled prompt",
        stable_prefix="",
        dynamic_suffix="",
        cache_boundary_enabled=False,
        cache_reorder_enabled=False,
        section_names=[],
        section_char_counts={},
    )

    with patch(
        "app.services.ai.context_manager.AgentContextManager.setup_context",
        new_callable=AsyncMock,
    ), patch(
        "app.services.ai.pipeline.steps.assemble_step.resolve_prompt_assembler_flags",
        new_callable=AsyncMock,
        return_value=(False, False),
    ), patch(
        "app.services.ai.pipeline.steps.assemble_step.assemble_system_prompt",
        return_value=assembled,
    ):
        chunks = [chunk async for chunk in AssembleStep(SkillLogService()).run(context)]

    assert any(chunk.get("id") == "skill:skill-1" for chunk in chunks)
    raw_prompt = next(
        chunk
        for chunk in chunks
        if chunk.get("type") == "debug" and chunk.get("subtype") == "raw_prompt"
    )
    assert raw_prompt["data"] == context.messages


@pytest.mark.asyncio
async def test_execution_step_dispatch_and_stream():
    """Verify that ExecutionStep emits meta and yields executed chunks."""
    from app.services.ai.pipeline.steps.execution_step import ExecutionStep

    class DummyAgentConfig:
        name = "sql_bot"
        agent_name = "sql_bot"
        agent_display_name = "SQL小助手"
        system_prompt = "SQL Helper"
        engine_type = "LOCAL"
        model_name = "gpt-4o"

    context = PipelineContext(
        messages=[{"role": "user", "content": "SELECT 1"}],
        user_info={"user_id": 123},
        conversation_id="conv_exec_1",
    )
    context.shared_state["agent_config"] = DummyAgentConfig()

    class DummyExecutor:
        async def execute(self, messages):
            yield {"content": "Data: "}
            yield {"content": "1"}

    mock_agent_service = MagicMock()
    mock_agent_service._dispatch_executor = AsyncMock(return_value=DummyExecutor())

    step = ExecutionStep(mock_agent_service)

    chunks = []
    async for chunk in step.run(context):
        chunks.append(chunk)

    meta_chunk = next((c for c in chunks if c.get("type") == "meta"), None)
    assert meta_chunk is not None
    assert meta_chunk["agent_name"] == "sql_bot"

    assert context.full_response_content == "Data: 1"
    assert context.execution_status == "success"


@pytest.mark.asyncio
async def test_execution_step_writes_partial_content_before_next_chunk():
    """流被取消前，已经发送的正文必须同步存在于 PipelineContext。"""
    from app.services.ai.pipeline.steps.execution_step import ExecutionStep

    class DummyAgentConfig:
        agent_name = "stream_agent"
        agent_display_name = "流式助手"
        agent_type = "GENERAL"
        model_name = "test-model"

    class DummyExecutor:
        async def execute(self, messages):
            yield {"type": "answer_delta", "content": "部分回答"}
            yield {"type": "answer_delta", "content": "不会读取"}

    service = MagicMock()
    service._dispatch_executor = AsyncMock(return_value=DummyExecutor())
    context = PipelineContext(
        messages=[{"role": "user", "content": "流式回答"}],
        user_info={"user_id": 123},
    )
    context.user_query = "流式回答"
    context.shared_state["agent_config"] = DummyAgentConfig()

    stream = ExecutionStep(service).run(context)
    try:
        assert (await stream.__anext__())["type"] == "meta"
        assert (await stream.__anext__())["content"] == "部分回答"
        assert context.full_response_content == "部分回答"
        assert context.shared_state["full_response_content"] == "部分回答"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_pipeline_runner_full_chain():
    """Verify that PipelineRunner runs sequentially and executes cleanly."""
    from app.services.ai.pipeline.runner import PipelineRunner
    from app.services.ai.pipeline.steps.preflight_step import PreflightStep
    from app.services.ai.pipeline.steps.finalize_step import FinalizeStep

    class StepA(PreflightStep):
        async def run(self, ctx):
            ctx.shared_state["step_a"] = True
            yield {"status": "init", "step": "A"}

    class StepB(FinalizeStep):
        async def run(self, ctx):
            ctx.shared_state["step_b"] = True
            yield {"type": "run_status", "status": ctx.execution_status, "step": "B"}

    runner = PipelineRunner([StepA(), StepB()])

    context = PipelineContext(
        messages=[{"role": "user", "content": "test runner"}],
        user_info={"user_id": 123},
    )

    chunks = []
    async for chunk in runner.run(context):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0]["step"] == "A"
    assert chunks[1]["step"] == "B"
    assert context.shared_state.get("step_a") is True
    assert context.shared_state.get("step_b") is True


@pytest.mark.asyncio
async def test_pipeline_runner_short_circuit():
    """Verify that PipelineRunner skips middle steps on quota_exceeded and finalizes."""
    from app.services.ai.pipeline.runner import PipelineRunner
    from app.services.ai.pipeline.base import BasePipelineStep
    from app.services.ai.pipeline.steps.finalize_step import FinalizeStep

    class BlockStep(BasePipelineStep):
        async def run(self, ctx):
            ctx.execution_status = "quota_exceeded"
            yield {"type": "error", "status": "quota_exceeded"}

    class MiddleStep(BasePipelineStep):
        async def run(self, ctx):
            ctx.shared_state["middle_ran"] = True
            yield {"content": "should not run"}

    class EndStep(FinalizeStep):
        async def run(self, ctx):
            ctx.shared_state["end_ran"] = True
            yield {"type": "run_status", "status": ctx.execution_status}

    runner = PipelineRunner([BlockStep(), MiddleStep(), EndStep()])
    context = PipelineContext(
        messages=[{"role": "user", "content": "blocked query"}],
        user_info={"user_id": 123},
    )

    chunks = []
    async for chunk in runner.run(context):
        chunks.append(chunk)

    assert context.shared_state.get("middle_ran") is None
    assert context.shared_state.get("end_ran") is True
    assert len(chunks) == 2
    assert chunks[0]["status"] == "quota_exceeded"
    assert chunks[1]["type"] == "run_status"


@pytest.mark.asyncio
async def test_pipeline_runner_tracks_stream_events_in_timeline():
    """流水线透传的 Todo/日志必须进入最终可持久化时间线。"""
    from app.services.ai.pipeline.runner import PipelineRunner
    from app.services.ai.pipeline.base import BasePipelineStep

    class TodoStep(BasePipelineStep):
        async def run(self, ctx):
            yield {
                "type": "todo_update",
                "todos": [{"content": "检查权限", "status": "in_progress"}],
            }

    class EndStep(FinalizeStep):
        async def run(self, ctx):
            yield {"type": "run_status", "status": ctx.execution_status}

    context = PipelineContext(
        messages=[{"role": "user", "content": "执行任务"}],
        user_info={"user_id": 123},
    )
    _ = [chunk async for chunk in PipelineRunner([TodoStep(), EndStep()]).run(context)]

    assert context.shared_state["process_timeline"][0]["kind"] == "todo"
    assert context.shared_state["process_timeline"][0]["todos"][0]["content"] == "检查权限"


@pytest.mark.asyncio
async def test_finalize_completes_todo_and_retracts_untrusted_content_before_status():
    """成功终态必须先校正正文和 Todo，再发布 run_status 与持久化。"""
    context = PipelineContext(
        messages=[{"role": "user", "content": "生成文件"}],
        user_info={"user_id": 123},
        conversation_id="conv_finalize_guard",
    )
    context.lane_user_id = 123
    context.user_query = "生成文件"
    context.full_response_content = "安全正文 /api/v1/chat/generated-files/fake?token=bad"
    context.shared_state["process_timeline"] = [
        {
            "kind": "todo",
            "id": "todo_current",
            "title": "任务清单",
            "todos": [{"content": "生成文件", "status": "in_progress"}],
        }
    ]

    with patch(
        "app.services.ai.agent_service._filter_current_turn_download_urls",
        return_value="安全正文",
    ), patch(
        "app.services.ai.agent_service._persist_assistant_message_and_summary",
        new_callable=AsyncMock,
    ) as persist, patch(
        "app.services.ai.agent_service._should_persist_turn_history",
        return_value=True,
    ), patch(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        new_callable=AsyncMock,
    ):
        chunks = [chunk async for chunk in FinalizeStep().run(context)]

    event_types = [chunk.get("type") for chunk in chunks]
    assert event_types.index("retraction") < event_types.index("todo_update") < event_types.index("run_status")
    assert context.full_response_content == "安全正文"
    assert persist.await_args.kwargs["content"] == "安全正文"
    assert persist.await_args.kwargs["process_timeline"][0]["counts"]["completed"] == 1


@pytest.mark.asyncio
async def test_finalize_awaiting_user_skips_completed_audit():
    """等待用户回答属于暂停态，不能记录成已完成事务。"""
    context = PipelineContext(
        messages=[{"role": "user", "content": "需要确认"}],
        user_info={"user_id": 123},
    )
    context.execution_status = "awaiting_user"
    context.user_query = "需要确认"

    with patch(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        new_callable=AsyncMock,
    ) as audit:
        _ = [chunk async for chunk in FinalizeStep().run(context)]

    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_runner_sanitizes_unhandled_step_exception():
    """步骤异常必须通过统一错误呈现，不能泄露密码和内部路径。"""
    from app.services.ai.pipeline.runner import PipelineRunner
    from app.services.ai.pipeline.base import BasePipelineStep

    class BadStep(BasePipelineStep):
        async def run(self, ctx):
            raise RuntimeError("password=top-secret /private/internal/path")
            yield

    class EndStep(FinalizeStep):
        async def run(self, ctx):
            yield {"type": "run_status", "status": ctx.execution_status}

    safe_error = {
        "type": "error",
        "status": "error",
        "content": "执行失败，请稍后重试。",
        "error_detail": {"raw_error": "password=[REDACTED] [internal path]"},
    }
    context = PipelineContext(
        messages=[{"role": "user", "content": "触发异常"}],
        user_info={"user_id": 123},
    )

    with patch(
        "app.services.ai.agent_service._enrich_terminal_error_chunk",
        new_callable=AsyncMock,
        return_value=safe_error,
    ) as enrich_error:
        chunks = [chunk async for chunk in PipelineRunner([BadStep(), EndStep()]).run(context)]

    error_chunk = next(chunk for chunk in chunks if chunk.get("type") == "error")
    assert error_chunk == safe_error
    assert "top-secret" not in str(error_chunk)
    assert "/private/internal/path" not in str(error_chunk)
    enrich_error.assert_awaited_once()
