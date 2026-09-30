import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace
from pydantic import BaseModel
from app.services.ai.runtime.agentscope.compat import AIMessage
from app.services.ai.executors.assistant_executor import AssistantExecutor
from app.services.ai.turn_decision import TurnDecision
from app.schemas.agent import ChatConfig


def _tool_result_text(block) -> str:
    """按 AgentScope 契约取 ToolResultBlock 的文本。

    ``ToolResultBlock.output`` 的类型是 ``str | List[TextBlock | DataBlock]``
    （见 agentscope/message/_block.py）。测试替身此前只假设了列表形态，碰到
    平台以 ``output=str(...)`` 构造的结果就会 'str' object has no attribute 'text'。
    """
    output = block.output
    if isinstance(output, str):
        return output
    return "".join(getattr(item, "text", "") for item in output)

async def _config_get_with_knowledge_enabled(key, default=None, **kwargs):
    """ConfigService.get 测试替身：知识库开关须为 true，其余沿用历史的 "5"。

    knowledge_utils.is_knowledge_base_enabled() 读的是 "knowledge_base_enabled"
    （默认 "true"）。把 get 一律 mock 成 "5" 会让知识库判定为未开启，runner 直接
    以「知识库功能未开启」终止，后续断言自然全数落空。
    """
    if key == "knowledge_base_enabled":
        return "true"
    return "5"

def _answer_text(events) -> str:
    """按平台口径拼接"回答正文"事件。

    正文事件类型集合见 app/services/ai/runtime/execution_observability.py 的
    _ANSWER_CHUNK_TYPES：``""``（历史形态，无 type）、``answer_delta``、
    ``process_narration_promote``。旧断言写死"无 type"这一种，协议演进后
    （正文改由 process_narration_promote 承载）就全部失效了。
    """
    from app.services.ai.runtime.execution_observability import _ANSWER_CHUNK_TYPES

    return "".join(
        e.get("content", "")
        for e in events
        if e.get("type", "") in _ANSWER_CHUNK_TYPES
    )


def test_grounding_buffer_only_holds_legacy_untyped_answer_content():
    from app.services.ai.runners.assistant_agent_runner import _is_grounding_bufferable_chunk

    assert _is_grounding_bufferable_chunk({"content": "回答片段"}) is True
    assert _is_grounding_bufferable_chunk({"type": "retraction", "content": ""}) is False
    assert _is_grounding_bufferable_chunk({"type": "process_narration_promote", "content": "最终正文"}) is False
    assert _is_grounding_bufferable_chunk({"type": "answer_delta", "content": "最终正文"}) is False
    assert _is_grounding_bufferable_chunk({"type": "answer_delta", "content": "合成正文", "phase": "synthesis"}) is False
    assert _is_grounding_bufferable_chunk({"type": "process_narration", "content": "正在搜索"}) is False
    assert _is_grounding_bufferable_chunk({"type": "process_narration_commit", "content": "正在搜索"}) is False

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override the global fixture to avoid real DB/Redis connection in unit tests."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield

class MockLLM:
    def __init__(self, responses):
        self.responses = responses # List of AIMessage or chunks
        self.call_count = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            if isinstance(response, list):
                # If response is a list of chunks, return the merged result or the first one
                return response[0] if response else AIMessage(content="")
            return response
        return AIMessage(content="Final synthesis result")

    async def astream(self, messages):
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            
            # If response is a list, yield items as chunks
            if isinstance(response, list):
                for chunk in response:
                    yield chunk
            else:
                yield response
        else:
            # Return a generic final response if exhausted
            yield AIMessage(content="Final synthesis result")

@pytest.fixture
def chat_config():
    return ChatConfig(
        agent_id="test-agent-id",
        agent_name="TestAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a test agent.",
        tools=["test_tool"]
    )

@pytest.fixture
def mock_tool():
    tool = AsyncMock()
    tool.name = "test_tool"
    tool.description = "Test tool"
    tool.args_schema = None
    tool.ainvoke.return_value = "Tool Result"
    return tool

# --- Tests ---

@pytest.mark.asyncio
async def test_general_chat_executor_delegates_to_general_agent_runner(chat_config):
    """Executor 只做接入壳，实际执行委托给 AssistantAgentRunner。"""
    trace_buffer = []
    executor = AssistantExecutor(
        config=chat_config,
        trace_id="trace-runner",
        trace_buffer=trace_buffer,
        debug_options={"return_raw_prompt": True},
        user_info={"id": "u1"},
        conversation_id="c1",
        turn_decision=TurnDecision(
            route_status="resolved",
            source="general",
            capability="answer",
            turn_labels=["same_topic"],
        ),
    )

    runner_instance = MagicMock()

    async def fake_execute(history):
        yield {"content": f"runner:{history[0]['content']}"}

    runner_instance.execute = fake_execute

    with patch("app.services.ai.executors.assistant_executor.AssistantAgentRunner", return_value=runner_instance) as runner_cls:
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "hello"}]):
            events.append(chunk)

    assert events == [{"content": "runner:hello"}]
    runner_cls.assert_called_once()
    _, kwargs = runner_cls.call_args
    assert kwargs["config"] is chat_config
    assert kwargs["trace_id"] == "trace-runner"
    assert kwargs["trace_buffer"] is trace_buffer
    assert kwargs["debug_options"] == {"return_raw_prompt": True}
    assert kwargs["user_info"] == {"id": "u1"}
    assert kwargs["conversation_id"] == "c1"
    assert kwargs["turn_decision"].turn_labels == ["same_topic"]
    assert executor.step_counter == runner_instance.step_counter


@pytest.mark.asyncio
async def test_simple_chat_no_tools(chat_config):
    """测试无工具的简单对话模式"""
    chat_config.tools = [] # Disable tools
    executor = AssistantExecutor(config=chat_config, trace_id="test-trace-1", trace_buffer=[])

    # Mock LLM streaming response
    mock_chunks = [
        AIMessage(content="Hello"),
        AIMessage(content=" World")
    ]

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]):
        mock_llm = MockLLM([mock_chunks])
        mock_get_llm.return_value = mock_llm
        
        history = [{"role": "user", "content": "Hi"}]
        results = []
        async for chunk in executor.execute(history):
            if "content" in chunk:
                results.append(chunk["content"])
        
        assert "".join(results) == "Hello World"


@pytest.mark.asyncio
async def test_general_chat_injects_turn_decision_as_weak_system_hint(chat_config):
    """General 可参考路由标签，但标签不作为硬分支。"""
    chat_config.tools = []
    captured = {}

    class CaptureLLM:
        async def astream(self, messages):
            captured["messages"] = messages
            yield AIMessage(content="ok")

    executor = AssistantExecutor(
        config=chat_config,
        trace_id="test-route-hints",
        trace_buffer=[],
        turn_decision=TurnDecision(
            route_status="resolved",
            source="general",
            capability="context_transform",
            turn_labels=["continuation_followup", "same_topic"],
            relation_to_previous="followup",
            user_action_type="transform_context",
        ),
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=CaptureLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "继续"}]):
            events.append(chunk)

    system_content = captured["messages"][0].content
    assert "本轮执行决策（仅供参考）" in system_content
    assert "continuation_followup, same_topic" in system_content
    assert "relation_to_previous: followup" in system_content
    assert "user_action_type: transform_context" in system_content
    assert "实际工具和数据权限仍由运行时代码校验" in system_content
    assert any(chunk.get("content") == "ok" for chunk in events)


def test_turn_decision_exposes_data_semantic_evidence_for_delegation():
    """Main 应看到已复用的数据语义，并被要求走真实数据获取路径。"""
    from app.services.ai.executors.prompts import AssistantPrompts
    from app.services.ai.intent_service import IntentType

    hint = AssistantPrompts.turn_decision_context(TurnDecision(
        route_status="resolved",
        turn_kind="data_query",
        source="internal_structured_data",
        capability="data_query",
        semantic_intent=IntentType.DATA_QUERY.value,
        semantic_confidence=0.91,
        semantic_reasoning="请求系统结构化记录",
    ))

    assert "semantic_intent: DATA_QUERY" in hint
    assert "0.91" in hint
    assert "sub_agent_call" in hint


def test_turn_decision_blocks_chatbi_delegation_for_local_file_domain():
    from app.services.ai.executors.prompts import AssistantPrompts
    from app.services.ai.intent_service import IntentType

    hint = AssistantPrompts.turn_decision_context(TurnDecision(
        route_status="resolved",
        turn_kind="general",
        source="local_file",
        capability="answer",
        semantic_intent=IntentType.DATA_QUERY.value,
        semantic_confidence=0.96,
        semantic_domain="local_file",
        semantic_operation="aggregate",
        fact_kind="file_count",
        reference_mode="new_query",
        needs_fresh_data=True,
    ))

    assert "不是 ChatBI 业务数据" in hint
    assert "禁止调用 data_query/ChatBI 子智能体" in hint


@pytest.mark.asyncio
async def test_standard_tool_call(chat_config, mock_tool):
    """系统隐式 legacy 工具应转 RuntimeToolSpec，不再触发手写 ReAct。"""
    chat_config.tools = []
    executor = AssistantExecutor(config=chat_config, trace_id="test-trace-2", trace_buffer=[])

    class NoNativeModelHandle:
        native_model = None

        def bind_tools(self, tools):
            raise AssertionError("legacy bind_tools fallback should not run")

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=NoNativeModelHandle())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(side_effect=AssertionError("legacy tools should not be loaded"))), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[mock_tool]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Do something"}]):
            events.append(chunk)

    mock_tool.ainvoke.assert_not_called()
    # 结论事件之前可能还有 tool_preflight 等中间件日志，故只比对 error 结论本身
    assert [e for e in events if e.get("type") == "error"] == [
        {
            "type": "error",
            "status": "error",
            "content": "当前模型适配器未提供 AgentScope native_model，无法执行 AgentScope 原生工具链。",
        }
    ]


@pytest.mark.asyncio
async def test_general_runner_runtime_tool_specs_do_not_fallback_to_legacy_tools(chat_config):
    """RuntimeToolSpec 工具链缺 native_model 时应显式失败，不再回落 legacy 工具执行。"""
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    executor = AssistantExecutor(config=chat_config, trace_id="test-runtime-tool", trace_buffer=[])

    async def runtime_tool(query: str):
        return "Runtime Tool Result"

    runtime_spec = RuntimeToolSpec(
        name="test_tool",
        description="Runtime test tool",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=runtime_tool,
        permission_scope="read",
    )

    class NoNativeModelHandle:
        native_model = None

        def bind_tools(self, tools):
            raise AssertionError("legacy bind_tools fallback should not run")

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=NoNativeModelHandle())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])) as mock_get_runtime_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(side_effect=AssertionError("legacy tools should not be loaded"))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Do runtime tool"}]):
            events.append(chunk)

    mock_get_runtime_tools.assert_awaited_once_with(chat_config.tools)
    # 结论事件之前可能还有 tool_preflight 等中间件日志，故只比对 error 结论本身
    assert [e for e in events if e.get("type") == "error"] == [
        {
            "type": "error",
            "status": "error",
            "content": "当前模型适配器未提供 AgentScope native_model，无法执行 AgentScope 原生工具链。",
        }
    ]


@pytest.mark.asyncio
async def test_general_runner_uses_agentscope_native_agent_for_runtime_tools(chat_config):
    """当具备 AgentScope native model 和 RuntimeToolSpec 时，General 应走原生 Agent/Toolkit。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.executors.prompts import AssistantPrompts
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_native_general",
                            name="test_tool",
                            input='{"query": "native"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="Native final answer")],
                is_last=True,
            )

    async def runtime_tool(query: str):
        return f"runtime:{query}"

    runtime_spec = RuntimeToolSpec(
        name="test_tool",
        description="Runtime test tool",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=runtime_tool,
        permission_scope="read",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    executor = AssistantExecutor(config=chat_config, trace_id="test-native-general", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Use native"}]):
            events.append(chunk)

    assert any(e.get("title", "").startswith("调用工具: test_tool") for e in events)
    assert any(e.get("title", "").startswith("工具完成: test_tool") for e in events)
    assert "Native final answer" in _answer_text(events)


@pytest.mark.asyncio
async def test_general_runner_synthesizes_when_text_block_delta_missing(chat_config):
    """工具执行后即使 AgentState 有文本，也统一进入 synthesis，不直接回填正文。"""
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(
                session_id="s1",
                reply_id="r1",
                context=[
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=[TextBlock(text="recovered after bash")],
                    ),
                ],
            )

    async def fake_event_stream():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_bash",
            tool_call_name="Bash",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_bash",
            delta="command ok",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_bash")
        yield SimpleNamespace(
            type="MODEL_CALL_END",
            reply_id="r1",
            input_tokens=100,
            output_tokens=223,
        )
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-missed-text-delta",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    state["used_tools"] = True
    state["tool_names"] = {"call_bash": "Bash"}
    state["tool_outputs"] = {"call_bash": "command ok"}
    state["tool_started_at"] = {"call_bash": 0.0}

    chunks = []
    async for chunk in runner._stream_agentscope_native_events(
        event_stream=fake_event_stream(),
        agent=FakeAgent(),
        tools=[],
        native_model=SimpleNamespace(model="qwen-test"),
        state=state,
    ):
        chunks.append(chunk)

    content = "".join(
        c["content"] for c in chunks if c.get("type") == "answer_delta"
    )
    assert "recovered after bash" in content
    assert "未能生成完整回答" not in content


@pytest.mark.asyncio
async def test_general_runner_keeps_tool_preamble_as_process_narration(chat_config):
    """工具前旁白走过程消息并按 delta 流式追加；无工具的最终文字在 turn 结束时提升为正文。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(type="MODEL_CALL_START", reply_id="r1")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="Let me search.")
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_search",
            tool_call_name="web_search",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_search",
            delta="ok",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_search")
        yield SimpleNamespace(type="MODEL_CALL_END", reply_id="r1")
        yield SimpleNamespace(type="MODEL_CALL_START", reply_id="r2")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="# 晋景新能综合分析报告\n\n这是基于工具结果生成的完整回答，包含足够的可见正文内容。",
        )
        yield SimpleNamespace(type="MODEL_CALL_END", reply_id="r2")
        yield SimpleNamespace(type="REPLY_END", reply_id="r2", session_id="s1")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-process-narration-stream",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    chunks = []
    async for chunk in runner._stream_agentscope_native_events(
        event_stream=fake_event_stream(),
        agent=FakeAgent(),
        tools=[],
        native_model=SimpleNamespace(model="qwen-test"),
        state=state,
    ):
        chunks.append(chunk)

    from app.services.ai.agent_service import _accumulate_stream_content

    narration = "".join(
        str(c.get("content") or "")
        for c in chunks
        if c.get("type") in ("process_narration", "process_narration_commit")
    )
    answer_deltas = "".join(
        str(c.get("content") or "")
        for c in chunks
        if c.get("type") == "answer_delta"
    )
    visible = ""
    for chunk in chunks:
        visible = _accumulate_stream_content(visible, chunk)
    assert "Let me search." in narration
    assert "Let me search." not in answer_deltas
    assert "# 晋景新能综合分析报告" not in answer_deltas
    assert "Let me search." not in visible
    assert "# 晋景新能综合分析报告" in visible
    assert not any(c.get("type") == "retraction" for c in chunks)
    assert any(c.get("type") == "process_narration_promote" for c in chunks)
    first_narration = next(
        index for index, chunk in enumerate(chunks)
        if chunk.get("type") == "process_narration"
    )
    first_tool_log = next(
        index for index, chunk in enumerate(chunks)
        if chunk.get("type") == "log" and "调用工具" in str(chunk.get("title") or "")
    )
    assert first_narration < first_tool_log


@pytest.mark.asyncio
async def test_general_runner_synthesis_fallback_when_no_text_and_no_context(chat_config):
    """工具执行后既无 TEXT_BLOCK_DELTA 也无 context 文本时，应走 synthesis 兜底。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_bash",
            tool_call_name="Bash",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_bash",
            delta="42",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_bash")
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    class SynthesisLLM:
        async def astream(self, messages):
            yield AIMessage(content="synthesis fallback answer")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-synthesis-fallback",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    state["user_query"] = "count files"
    state["used_tools"] = True
    state["tool_names"] = {"call_bash": "Bash"}
    state["tool_outputs"] = {"call_bash": "42"}
    state["tool_started_at"] = {"call_bash": 0.0}

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=SynthesisLLM()),
    ):
        chunks = []
        async for chunk in runner._stream_agentscope_native_events(
            event_stream=fake_event_stream(),
            agent=FakeAgent(),
            tools=[],
            native_model=SimpleNamespace(model="qwen-test"),
            state=state,
        ):
            chunks.append(chunk)

    content = "".join(
        c["content"] for c in chunks if c.get("type") == "answer_delta"
    )
    assert "synthesis fallback answer" in content


@pytest.mark.asyncio
async def test_general_runner_todo_only_result_keeps_reply_visible_without_synthesis(chat_config):
    """仅更新任务清单且没有业务结果时，返回安全提示而不是把 todo 当答案。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-todo-only-reconcile",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    state["used_tools"] = True
    state["tool_names"] = {"todo-1": "todo_write"}
    state["tool_outputs"] = {
        "todo-1": '{"todos":[{"content":"整理报告","status":"completed"}]}'
    }

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        new_callable=AsyncMock,
    ) as get_synthesis_llm:
        chunks = []
        async for chunk in runner._reconcile_reply_after_stream(
            agent=FakeAgent(),
            state=state,
            native_model=SimpleNamespace(model="qwen-test"),
        ):
            chunks.append(chunk)

    content = "".join(
        str(chunk.get("content") or "")
        for chunk in chunks
        if chunk.get("type") == "answer_delta"
    )
    assert "未能生成完整回答" in content
    assert "todo_write" not in content
    get_synthesis_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_runner_preserves_answer_before_final_todo_write(chat_config):
    """模型先结束、随后执行 todo_write 时，前面的最终正文不能变成过程消息。"""
    from agentscope.state import AgentState

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(type="MODEL_CALL_START", reply_id="r1")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="这是已经生成的完整最终正文。",
        )
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="todo-1",
            tool_call_name="todo_write",
        )
        # AgentScope 的模型调用在工具真正执行前已经结束。
        yield SimpleNamespace(type="MODEL_CALL_END", reply_id="r1")
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="todo-1",
            delta='{"todos":[{"content":"整理报告","status":"completed"}]}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="todo-1")
        yield SimpleNamespace(type="MODEL_CALL_START", reply_id="r2")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="报告已完成。",
        )
        yield SimpleNamespace(type="MODEL_CALL_END", reply_id="r2")
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-answer-before-final-todo",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    chunks = []
    async for chunk in runner._stream_agentscope_native_events(
        event_stream=fake_event_stream(),
        agent=FakeAgent(),
        tools=[],
        native_model=SimpleNamespace(model="qwen-test"),
        state=state,
    ):
        chunks.append(chunk)

    from app.services.ai.agent_service import _accumulate_stream_content

    visible = ""
    for chunk in chunks:
        visible = _accumulate_stream_content(visible, chunk)
    assert "这是已经生成的完整最终正文。" in visible
    assert "整理报告" not in visible
    assert "todos" not in visible
    assert any(
        chunk.get("type") == "process_narration_promote"
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_general_runner_synthesis_after_transitional_text_and_more_tools(chat_config):
    """过渡语后又调工具、再无正文时，应触发 synthesis 而非因 partial 跳过兜底。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_skill",
            tool_call_name="Skill",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_skill",
            delta="ok",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_skill")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="企业信息查询需要实时数据，让我尝试通过搜索来获取：",
        )
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_bash",
            tool_call_name="Bash",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_bash",
            delta="<meta name='aliyun_waf_aa'>",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_bash")
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    class SynthesisLLM:
        async def astream(self, messages):
            yield AIMessage(content="未能获取实时企业数据，建议使用官方 API 查询。")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-partial-then-tools",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    state["user_query"] = "查企业信息"

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=SynthesisLLM()),
    ):
        chunks = []
        async for chunk in runner._stream_agentscope_native_events(
            event_stream=fake_event_stream(),
            agent=FakeAgent(),
            tools=[],
            native_model=SimpleNamespace(model="qwen-test"),
            state=state,
        ):
            chunks.append(chunk)

    from app.services.ai.agent_service import _accumulate_stream_content

    content = ""
    for chunk in chunks:
        content = _accumulate_stream_content(content, chunk)
    narration = "".join(
        str(c.get("content") or "")
        for c in chunks
        if c.get("type") in ("process_narration", "process_narration_commit")
    )
    assert "让我尝试通过搜索来获取" in narration
    assert "让我尝试通过搜索来获取" not in content
    assert "未能获取实时企业数据" in content


@pytest.mark.asyncio
async def test_general_runner_synthesis_when_empty_delta_after_tools_clears_pending(chat_config):
    """工具后又收到仅含 think 的空 delta 时，仍应 synthesis（不能误清 pending）。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_skill",
            tool_call_name="Skill",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_skill",
            delta="ok",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_skill")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="让我尝试通过搜索来获取：",
        )
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_bash",
            tool_call_name="Bash",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_bash",
            delta="waf page",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_bash")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="",
        )
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    class SynthesisLLM:
        async def astream(self, messages):
            yield AIMessage(content="synthesis after empty think delta")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-empty-delta-after-tools",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    state["user_query"] = "查企业"

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=SynthesisLLM()),
    ):
        chunks = []
        async for chunk in runner._stream_agentscope_native_events(
            event_stream=fake_event_stream(),
            agent=FakeAgent(),
            tools=[],
            native_model=SimpleNamespace(model="qwen-test"),
            state=state,
        ):
            chunks.append(chunk)

    content = "".join(
        c["content"] for c in chunks if c.get("type") == "answer_delta"
    )
    assert "synthesis after empty think delta" in content


@pytest.mark.asyncio
async def test_general_runner_static_fallback_when_synthesis_returns_empty(chat_config):
    """synthesis 模型若只输出 thinking 被 strip 后，应给出静态 WAF 说明。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_bash",
            tool_call_name="Bash",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_bash",
            delta="<meta name='aliyun_waf_aa'>acw_sc__v2",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_bash")
        yield SimpleNamespace(
            type="TEXT_BLOCK_DELTA",
            delta="让我尝试通过搜索来获取：",
        )
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_bash2",
            tool_call_name="Bash",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_bash2")
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    class EmptySynthesisLLM:
        async def astream(self, messages):
            yield AIMessage(content="")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-static-fallback",
        trace_buffer=[],
    )
    state = new_native_stream_state()
    state["user_query"] = "查企业"

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=EmptySynthesisLLM()),
    ):
        chunks = []
        async for chunk in runner._stream_agentscope_native_events(
            event_stream=fake_event_stream(),
            agent=FakeAgent(),
            tools=[],
            native_model=SimpleNamespace(model="qwen-test"),
            state=state,
        ):
            chunks.append(chunk)

    content = "".join(
        c["content"] for c in chunks if c.get("type") == "answer_delta"
    )
    assert "WAF" in content or "未能生成完整回答" in content


@pytest.mark.asyncio
async def test_general_runner_continues_synthesis_after_tool_error_log(chat_config):
    """工具失败日志 status=error 不应中断 native 流，应继续 synthesis 兜底输出正文。"""
    from agentscope.state import AgentState
    from types import SimpleNamespace

    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage
    from app.services.ai.runtime.agentscope.event_stream import new_native_stream_state

    class FakeAgent:
        def __init__(self):
            self.state = AgentState(session_id="s1", reply_id="r1", context=[])

    async def fake_event_stream():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_read",
            tool_call_name="Read",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_read",
            delta="Error: File does not exist: /tmp/missing.py",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_read")
        yield SimpleNamespace(type="REPLY_END", reply_id="r1", session_id="s1")

    class SynthesisLLM:
        async def astream(self, messages):
            yield AIMessage(content="文件不存在，请检查路径后重试。")

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-tool-error-synthesis",
        trace_buffer=[],
    )
    state = new_native_stream_state(system_content="sys", max_steps=5)
    state["user_query"] = "读取 user.py"

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=SynthesisLLM()),
    ):
        chunks = []
        async for chunk in runner._stream_agentscope_native_events(
            event_stream=fake_event_stream(),
            agent=FakeAgent(),
            tools=[],
            native_model=SimpleNamespace(model="qwen-test"),
            state=state,
        ):
            chunks.append(chunk)

    tool_logs = [c for c in chunks if c.get("type") == "log" and c.get("status") == "error"]
    assert tool_logs, "应保留工具失败日志"
    content = "".join(
        c["content"] for c in chunks if c.get("type") == "answer_delta"
    )
    assert "文件不存在" in content or "未能生成完整回答" in content


@pytest.mark.asyncio
async def test_general_runner_restored_state_only_sends_latest_user_message(chat_config):
    """恢复 AgentState 后，只应向 AgentScope 追加本轮最新用户消息，避免重复灌入历史。"""
    from agentscope.message import Msg, TextBlock
    from agentscope.state import AgentState

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.agent_runtime import build_tools_fingerprint
    from app.services.ai.runtime.agentscope.state_store import RuntimeStateEnvelope, SCHEMA_VERSION
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    async def runtime_tool(query: str):
        return f"runtime:{query}"

    runtime_spec = RuntimeToolSpec(
        name="test_tool",
        description="Runtime test tool",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=runtime_tool,
        permission_scope="read",
    )
    restored_state = AgentState(
        session_id="session-restored",
        reply_id="reply-restored",
        context=[
            Msg(name="user", role="user", content=[TextBlock(text="old user")]),
            Msg(name="assistant", role="assistant", content=[TextBlock(text="old assistant")]),
        ],
    )
    captured_inputs = []

    class DeltaEvent:
        type = "TEXT_BLOCK_DELTA"
        delta = "restored ok"

    class FakeAgent:
        def __init__(self, state):
            self.state = state

        async def reply_stream(self, inputs):
            captured_inputs.extend(inputs)
            yield DeltaEvent()

    class FakeNativeModel:
        model = "fake-native-general"

    handle = AgentScopeLLMHandle(
        native_model=FakeNativeModel(),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-state-restore-inputs",
        trace_buffer=[],
        user_info={"user_id": "9001"},
        conversation_id="c-state",
    )
    fingerprint = build_tools_fingerprint(chat_config, [runtime_spec])
    envelope = RuntimeStateEnvelope(
        schema_version=SCHEMA_VERSION,
        agent_name=chat_config.agent_name,
        agent_version=chat_config.agent_version,
        tools_fingerprint=fingerprint,
        model_name="fake-native-general",
        updated_at="2026-06-08T00:00:00Z",
        state=restored_state.model_dump(mode="json"),
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.services.ai.runners.assistant_agent_runner.agent_state_store.load", AsyncMock(return_value=envelope)), \
         patch("app.services.ai.runners.assistant_agent_runner.agent_state_store.save", AsyncMock()), \
         patch.object(runner, "_build_native_agent", AsyncMock(return_value=FakeAgent(restored_state))):
        events = []
        async for chunk in runner.execute([
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old assistant"},
            {"role": "user", "content": "latest user"},
        ]):
            events.append(chunk)

    assert [msg.role for msg in captured_inputs] == ["user"]
    assert captured_inputs[0].get_text_content() == "latest user"
    assert "restored ok" in "".join(e["content"] for e in events if "content" in e)


@pytest.mark.asyncio
async def test_general_runner_emits_permission_required_for_agentscope_ask_tool(chat_config):
    """AgentScope ASK 权限事件应转换成现有 SSE 可消费的确认事件，且不执行工具。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="call_requires_confirm",
                        name="send_message",
                        input='{"message": "hello"}',
                    )
                ],
                is_last=True,
            )

    invoked = False

    async def send_message(message: str):
        nonlocal invoked
        invoked = True
        return f"sent:{message}"

    runtime_spec = RuntimeToolSpec(
        name="send_message",
        description="Send a message",
        parameters_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        source_type="static",
        callable=send_message,
        permission_scope="ask",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    executor = AssistantExecutor(config=chat_config, trace_id="test-native-ask", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Send it"}]):
            events.append(chunk)

    permission_events = [event for event in events if event.get("type") == "permission_required"]
    assert len(permission_events) == 1
    assert permission_events[0]["type"] == "permission_required"
    assert permission_events[0]["status"] == "pending"
    assert permission_events[0]["id"] == "call_requires_confirm"
    assert permission_events[0]["permission_request_id"].startswith("perm_")
    assert permission_events[0]["reply_id"]
    assert permission_events[0]["title"] == "需要确认工具调用: send_message"
    assert permission_events[0]["details"] == '参数: {"message": "hello"}'
    assert permission_events[0]["tool_call"] == {
        "id": "call_requires_confirm",
        "name": "send_message",
        "args": {"message": "hello"},
    }
    assert invoked is False


@pytest.mark.asyncio
async def test_general_runner_resumes_agentscope_ask_tool_after_confirmation(chat_config):
    """用户确认后，应基于同一个 AgentScope Agent 继续执行工具并生成回答。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    pending_agentscope_confirmations.clear()

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_requires_confirm",
                            name="send_message",
                            input='{"message": "hello"}',
                        )
                    ],
                    is_last=True,
                )
            tool_result = next(
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            )
            return ChatResponse(
                content=[TextBlock(text=f"confirmed final: {_tool_result_text(tool_result)}")],
                is_last=True,
            )

    invocations = []

    async def send_message(message: str):
        invocations.append(message)
        return f"sent:{message}"

    runtime_spec = RuntimeToolSpec(
        name="send_message",
        description="Send a message",
        parameters_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        source_type="static",
        callable=send_message,
        permission_scope="ask",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    executor = AssistantExecutor(config=chat_config, trace_id="test-native-resume", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Send it"}]):
            events.append(chunk)

    permission_event = next(event for event in events if event.get("type") == "permission_required")
    pending = pending_agentscope_confirmations.pop(permission_event["permission_request_id"])
    assert pending is not None
    assert invocations == []

    resume_events = []
    async for chunk in pending.runner.resume_agentscope_native_confirmation(pending, confirmed=True):
        resume_events.append(chunk)

    assert invocations == ["hello"]
    assert "confirmed final: sent:hello" in _answer_text(resume_events)


@pytest.mark.asyncio
async def test_agent_service_resumes_agentscope_ask_from_snapshot(chat_config):
    """进程内 live Agent 丢失后，应能基于 pending snapshot 重建 runner 并继续 ASK。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.agent_service import AgentService
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    pending_agentscope_confirmations.clear()

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_requires_confirm_snapshot",
                            name="send_message",
                            input='{"message": "snapshot"}',
                        )
                    ],
                    is_last=True,
                )
            tool_result = next(
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            )
            return ChatResponse(
                content=[TextBlock(text=f"snapshot final: {_tool_result_text(tool_result)}")],
                is_last=True,
            )

    invocations = []

    async def send_message(message: str):
        invocations.append(message)
        return f"sent:{message}"

    runtime_spec = RuntimeToolSpec(
        name="send_message",
        description="Send a message",
        parameters_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        source_type="static",
        callable=send_message,
        permission_scope="ask",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    executor = AssistantExecutor(
        config=chat_config,
        trace_id="test-native-resume-snapshot",
        trace_buffer=[],
        user_info={"user_id": "9002"},
        conversation_id="c-snapshot",
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.services.ai.runners.assistant_agent_runner.get_local_workspace", AsyncMock(return_value=None)), \
         patch("app.services.memory_config_service.MemoryConfigService.get_bool", AsyncMock(return_value=False)):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Send snapshot"}]):
            events.append(chunk)

        permission_event = next(event for event in events if event.get("type") == "permission_required")
        # Simulate another worker/process: no live Agent handle, only Redis/in-memory snapshot remains.
        pending_agentscope_confirmations._items.clear()

        resume_events = []
        async for chunk in AgentService().resume_agentscope_permission_stream(
            permission_request_id=permission_event["permission_request_id"],
            confirmed=True,
            user_info={"user_id": "9002"},
        ):
            resume_events.append(chunk)

    assert invocations == ["snapshot"]
    assert any(event.get("type") == "permission_result" for event in resume_events)
    assert "snapshot final: sent:snapshot" in _answer_text(resume_events)


@pytest.mark.asyncio
async def test_permission_resume_restores_user_context_for_user_scoped_tools(chat_config):
    """工具确认恢复后应能读到当前 user_id（如钉钉/企微 webhook 查询依赖 AgentContext）。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.context import get_current_agent_context, set_agent_context
    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.agent_service import AgentService
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    pending_agentscope_confirmations.clear()

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_user_scoped_tool",
                            name="send_message",
                            input='{"message": "hello"}',
                        )
                    ],
                    is_last=True,
                )
            tool_result = next(
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            )
            return ChatResponse(
                content=[TextBlock(text=f"final: {_tool_result_text(tool_result)}")],
                is_last=True,
            )

    captured_user_ids = []

    async def send_message(message: str):
        ctx = get_current_agent_context()
        captured_user_ids.append(ctx.user_id if ctx else None)
        return f"sent:{message}"

    runtime_spec = RuntimeToolSpec(
        name="send_message",
        description="Send a message",
        parameters_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        source_type="static",
        callable=send_message,
        permission_scope="ask",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    executor = AssistantExecutor(
        config=chat_config,
        trace_id="test-permission-user-context",
        trace_buffer=[],
        user_info={"user_id": "42", "user_name": "tester"},
        conversation_id="conv-user-context",
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.services.ai.runners.assistant_agent_runner.get_local_workspace", AsyncMock(return_value=None)), \
         patch("app.services.memory_config_service.MemoryConfigService.get_bool", AsyncMock(return_value=False)):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Send it"}]):
            events.append(chunk)

        permission_event = next(event for event in events if event.get("type") == "permission_required")
        pending_agentscope_confirmations._items.clear()
        set_agent_context(None)

        resume_events = []
        async for chunk in AgentService().resume_agentscope_permission_stream(
            permission_request_id=permission_event["permission_request_id"],
            confirmed=True,
            user_info={"user_id": "42", "user_name": "tester"},
        ):
            resume_events.append(chunk)

    assert captured_user_ids == [42]


@pytest.mark.asyncio
async def test_knowledge_runner_uses_agentscope_native_agent(chat_config):
    """KnowledgeAgentRunner 应自动检索知识库并走 AgentScope 原生 Agent。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_kb",
                            name="search_knowledge_base",
                            input='{"query": "知识库问题"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="knowledge final")], is_last=True)

    async def search_knowledge_base(query: str, dataset_ids: str | None = None):
        return f"kb:{query}:{dataset_ids or ''}"

    runtime_spec = RuntimeToolSpec(
        name="search_knowledge_base",
        description="Search knowledge base",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "dataset_ids": {"type": "string"},
            },
            "required": ["query"],
        },
        source_type="static",
        callable=search_knowledge_base,
        permission_scope="read",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-knowledge",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-knowledge",
        temperature=0.0,
        streaming=True,
    )
    runner = KnowledgeAgentRunner(config=chat_config, trace_id="test-native-kb", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.get_local_workspace", AsyncMock(return_value=None)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(side_effect=_config_get_with_knowledge_enabled)):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "知识库问题"}]):
            events.append(chunk)

    assert any(e.get("title") == "自动检索知识库" for e in events)
    assert any(e.get("title", "").startswith("调用工具: search_knowledge_base") for e in events)
    assert "knowledge final" in _answer_text(events)


@pytest.mark.asyncio
async def test_knowledge_runner_reuses_cached_result_without_search_tool(chat_config):
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner

    async def no_multimodal_gate(*args, **kwargs):
        if False:
            yield {}

    async def cached_native_turn(self, **kwargs):
        yield {"content": "cached knowledge final"}

    runner = KnowledgeAgentRunner(
        config=chat_config,
        trace_id="trace-cached-kb",
        trace_buffer=[],
        debug_options={"grounding_enabled": False},
        user_info={"user_id": "42"},
        conversation_id="conv-cached-kb",
        turn_decision=TurnDecision(
            reusable_result_mode="reuse",
            reusable_result_id="kb-result-1",
        ),
        current_user_query="继续总结\n\n---\n\n【被点击的 AI 回复】\n上一轮知识库结果",
    )
    cached_result = {
        "result_id": "kb-result-1",
        "result_type": "knowledge",
        "status": "completed",
        "content": "上一轮知识库结果正文",
        "text_excerpt": "上一轮知识库结果正文",
        "structured": {"answer": "上一轮知识库结果正文"},
    }

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.ai.multimodal_support.run_multimodal_gate",
        new=no_multimodal_gate,
    ), patch.object(
        runner,
        "_resolve_knowledge_tools",
        AsyncMock(return_value=[]),
    ), patch(
        "app.services.ai.session_tool_artifact.load_session_tool_artifact",
        AsyncMock(return_value=cached_result),
    ), patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=SimpleNamespace(native_model=object())),
    ), patch(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="5"),
    ), patch.object(
        KnowledgeAgentRunner,
        "_execute_with_agentscope_native_agent",
        new=cached_native_turn,
    ), patch(
        "app.services.ai.runtime.agentscope.workspace.append_session_workspace_sandbox_to_system_prompt",
        AsyncMock(side_effect=lambda content, **kwargs: content),
    ):
        events = []
        async for chunk in runner.execute([
            {
                "role": "user",
                "content": runner.current_user_query,
            }
        ]):
            events.append(chunk)

    assert not any("search_knowledge_base 不可用" in str(event.get("content", "")) for event in events)
    assert "cached knowledge final" in _answer_text(events)


def test_knowledge_runner_only_reuses_knowledge_results():
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner

    assert KnowledgeAgentRunner._is_knowledge_reusable_result(
        {"result_type": "knowledge", "content": "知识库结果"}
    ) is True
    assert KnowledgeAgentRunner._is_knowledge_reusable_result(
        {"result_type": "data", "content": "数据查询结果"}
    ) is False
    assert KnowledgeAgentRunner._is_knowledge_reusable_result(
        {"result_type": "web", "content": "网页结果"}
    ) is False


@pytest.mark.asyncio
async def test_knowledge_runner_does_not_bypass_search_for_data_result(chat_config):
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner

    async def no_multimodal_gate(*args, **kwargs):
        if False:
            yield {}

    runner = KnowledgeAgentRunner(
        config=chat_config,
        trace_id="trace-data-cache-in-kb",
        trace_buffer=[],
        user_info={"user_id": "42"},
        conversation_id="conv-data-cache-in-kb",
        turn_decision=TurnDecision(
            reusable_result_mode="reuse",
            reusable_result_id="data-result-1",
        ),
        current_user_query="请回答知识库问题",
    )

    with patch(
        "app.services.ai.runners.knowledge_agent_runner.is_knowledge_base_enabled",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.ai.multimodal_support.run_multimodal_gate",
        new=no_multimodal_gate,
    ), patch.object(
        runner,
        "_resolve_knowledge_tools",
        AsyncMock(return_value=[]),
    ), patch(
        "app.services.ai.session_tool_artifact.load_session_tool_artifact",
        AsyncMock(
            return_value={
                "result_id": "data-result-1",
                "result_type": "data",
                "status": "completed",
                "content": "上一轮数据查询结果",
            }
        ),
    ):
        events = []
        async for chunk in runner.execute(
            [{"role": "user", "content": "请回答知识库问题"}]
        ):
            events.append(chunk)

    assert any("search_knowledge_base 不可用" in str(event.get("content", "")) for event in events)
    assert not any(event.get("title") == "自动复用知识库" for event in events)


@pytest.mark.asyncio
async def test_is_knowledge_service_unavailable_ignores_document_content_false_positives():
    """检索正文含 503/timeout 等子串时，不应误判为服务不可用。"""
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
    import json

    doc_content = (
        "I found the following information in the knowledge base.\n"
        "--- [ID:1] Source: EC6 用户手册.pdf ---\n"
        "请拨打 400-503-1234 或等待 30 秒 timeout 后重试蓝牙连接。"
    )
    tool_output = json.dumps(
        {"content": doc_content, "citations": [{"id": "1", "content": doc_content}]},
        ensure_ascii=False,
    )
    assert KnowledgeAgentRunner._is_knowledge_service_unavailable(tool_output) is False
    assert KnowledgeAgentRunner._is_knowledge_service_unavailable(doc_content) is False


@pytest.mark.asyncio
async def test_knowledge_runner_stops_on_service_unavailable(chat_config):
    """知识库服务不可用时，应终止问答并返回明确提示，不进入 ReAct。"""
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
    from app.services.metadata_rag_service import MetadataRagService

    react_called = False

    async def fake_agent_turn(*args, **kwargs):
        nonlocal react_called
        react_called = True
        if False:
            yield {}

    async def search_knowledge_base(query: str, dataset_ids: str | None = None):
        return MetadataRagService.knowledge_unavailable_hint("RAGFlow HTTP Error 502: ")

    runtime_spec = RuntimeToolSpec(
        name="search_knowledge_base",
        description="Search knowledge base",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "dataset_ids": {"type": "string"},
            },
            "required": ["query"],
        },
        source_type="static",
        callable=search_knowledge_base,
        permission_scope="read",
    )
    runner = KnowledgeAgentRunner(config=chat_config, trace_id="trace-kb-fatal", trace_buffer=[])

    with patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.get_local_workspace", AsyncMock(return_value=None)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(side_effect=_config_get_with_knowledge_enabled)), \
         patch(
             "app.services.ai.runners.knowledge_agent_runner.KnowledgeAgentRunner._execute_with_agentscope_native_agent",
             fake_agent_turn,
         ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "换电流程是什么"}]):
            events.append(chunk)

    assert react_called is False
    assert any(event.get("title") == "知识库服务不可用" for event in events)
    assert any(
        "知识库检索服务当前不可用" in str(event.get("content", ""))
        for event in events
        if event.get("content")
    )


@pytest.mark.asyncio
async def test_general_runner_greeting_with_table_not_intercepted(chat_config):
    """纯问候语场景下，模型用表格做能力引导时不应误触反幻觉拦截。"""
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_decision import TurnDecision

    greeting_reply = (
        "您好！我是 NanZi · AI。\n"
        "| 我能帮您 | 示例 |\n"
        "| --- | --- |\n"
        "| 闲聊问答 | 今天天气怎么样 |\n"
        "| 查业务数据 | 请使用数据智能助手 |\n"
    )

    class FakeLLM:
        async def astream(self, messages, *args, **kwargs):
            class Chunk:
                content = greeting_reply
            yield Chunk()

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-greeting-table",
        trace_buffer=[],
        turn_decision=TurnDecision(
            route_status="resolved",
            turn_kind="general",
            source="general",
            capability="answer",
            semantic_intent="GENERAL",
        ),
    )
    runner.config.tools = []

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "你好"}]):
            events.append(chunk)

    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert any(greeting_reply in str(e.get("content", "")) for e in events if e.get("content"))


@pytest.mark.asyncio
async def test_general_runner_without_tools_preserves_hallucination_with_risk_warning(chat_config):
    """通用助手无工具时编造的资产表格：保留正文并追加风险提示，不再硬拦截。

    契约见 fee89578「未接地事实的软风险提示（PASS_WITH_WARNING）」。
    """
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    
    # 模拟大模型直接生成带 IP 表格的内容
    hallucinated_text = (
        "为您查询到以下机器配置信息：\n"
        "| 主机名 | IP 地址 | 配置 |\n"
        "| --- | --- | --- |\n"
        "| app-01 | 192.168.1.10 | 8C16G |\n"
    )
    
    class FakeLLM:
        async def astream(self, messages, *args, **kwargs):
            class Chunk:
                content = hallucinated_text
            yield Chunk()
            
    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-hallucination-intercept",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        debug_options={"grounding_enabled": True},
    )
    runner.config.agent_id = "sys-agent-chat"
    runner.config.agent_name = "main"
    runner.config.tools = [] # 确保无工具，走 Simple Mode
    
    data_agent = SimpleNamespace(
        id="agent-data-custom",
        display_name="业务数据专家",
        name="biz-data-agent",
        capabilities=["data_query"],
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.RegistryToolProvider.get_implicit_tool", AsyncMock(return_value=None)), \
         patch(
             "app.services.ai.runners.assistant_agent_runner.AgentManagerService.list_allowed_agents",
             AsyncMock(return_value=[data_agent]),
         ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下资产列表和设备清单"}]):
            events.append(chunk)
            
    content = "".join(str(e.get("content") or "") for e in events)
    assert hallucinated_text in content  # 正文保留，不再被吞掉
    assert content.count("风险提示") == 1  # 追加一条软风险提示
    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert not any(e.get("type") == "grounding_blocked" for e in events)


@pytest.mark.asyncio
async def test_general_runner_without_data_tool_flags_fake_chatbi_handoff(chat_config):
    """通用助手无 data_query 能力时假装衔接 ChatBI：保留正文并追加风险提示。"""
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_decision import TurnDecision

    fake_handoff_text = (
        "检测到您的需求涉及业务数据查询，我将自动为您衔接 ChatBI 数据分析专家的能力，"
        "尝试查询最近一周的总订单量。\n\n正在为您检索相关数据集..."
    )

    class FakeLLM:
        async def astream(self, messages, *args, **kwargs):
            class Chunk:
                content = fake_handoff_text
            yield Chunk()

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-fake-chatbi-handoff",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        debug_options={"grounding_enabled": True},
        turn_decision=TurnDecision(
            route_status="resolved",
            turn_kind="general",
            source="general",
            capability="answer",
            semantic_intent="DATA_QUERY",
        ),
    )
    runner.config.agent_id = "sys-agent-chat"
    runner.config.agent_name = "main"
    runner.config.tools = []

    data_agent = SimpleNamespace(
        id="agent-data-custom",
        display_name="业务数据专家",
        name="biz-data-agent",
        capabilities=["data_query"],
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.RegistryToolProvider.get_implicit_tool", AsyncMock(return_value=None)), \
         patch(
             "app.services.ai.runners.assistant_agent_runner.AgentManagerService.list_allowed_agents",
             AsyncMock(return_value=[data_agent]),
         ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下最近一周总订单量"}]):
            events.append(chunk)

    content = "".join(str(e.get("content") or "") for e in events)
    assert any(e.get("title") == "事实来源风险提示已追加" for e in events)
    assert content.count("风险提示") == 1
    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert not any(e.get("type") == "grounding_blocked" for e in events)


@pytest.mark.asyncio
async def test_general_runner_plain_chatbi_suggestions_without_hallucination_not_intercepted(chat_config):
    """仅口头建议切换 ChatBI、无编造数据特征时，反查数护栏不再误拦。"""
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_decision import TurnDecision

    plain_suggestion_text = (
        "作为通用全能助手，我无法直接访问您的内部业务数据库。"
        "但我已为您识别意图，该任务最适合由 ChatBI 数据分析专家来处理。\n\n"
        "- [🙋 查询所有活跃用户](quick:查询所有活跃用户)\n"
        "- [🙋 查询今日新增用户](quick:查询今日新增用户)"
    )

    class FakeLLM:
        async def astream(self, messages, *args, **kwargs):
            class Chunk:
                content = plain_suggestion_text
            yield Chunk()

    runner = AssistantAgentRunner(
        config=chat_config,
        trace_id="test-plain-chatbi-suggestions",
        trace_buffer=[],
        user_info={"role": "admin", "user_id": "1"},
        turn_decision=TurnDecision(
            route_status="resolved",
            turn_kind="general",
            source="general",
            capability="answer",
            semantic_intent="DATA_QUERY",
        ),
    )
    runner.config.agent_id = "sys-agent-chat"
    runner.config.agent_name = "main"
    runner.config.tools = []

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.RegistryToolProvider.get_implicit_tool", AsyncMock(return_value=None)):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "帮我查一下用户列表数据呢"}]):
            events.append(chunk)

    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert any(plain_suggestion_text in str(e.get("content", "")) for e in events if e.get("content"))


@pytest.mark.asyncio
async def test_knowledge_runner_with_rag_but_no_citations_intercepts_hallucination(chat_config):
    """知识库助手有文档召回，但大模型脑补了不带任何引用的长事实回复，应予以拦截。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.knowledge_agent_runner import KnowledgeAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            # 模拟模型生成长文描述，但完全没有 [1] 类似的引用标签
            brain_text = "根据配置要求，您需要首先登录系统后台，在设置中心找到网络连接，输入正确的子网掩码并保存，然后再重启网卡即可。"
            return ChatResponse(content=[TextBlock(text=brain_text)], is_last=True)

    async def search_knowledge_base(query: str, dataset_ids: str | None = None):
        # 模拟有正常内容返回
        return '{"content": "知识库的真实内容", "citations": [{"id": "1", "content": "真实内容"}]}'

    runtime_spec = RuntimeToolSpec(
        name="search_knowledge_base",
        description="Search knowledge base",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "dataset_ids": {"type": "string"},
            },
            "required": ["query"],
        },
        source_type="static",
        callable=search_knowledge_base,
        permission_scope="read",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-knowledge",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-knowledge",
        temperature=0.0,
        streaming=True,
    )
    
    runner = KnowledgeAgentRunner(config=chat_config, trace_id="test-kb-no-citation", trace_buffer=[])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.get_local_workspace", AsyncMock(return_value=None)), \
         patch("app.services.ai.runners.knowledge_agent_runner.resolve_knowledge_dataset_ids", AsyncMock(return_value=(["ds-1"], None))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(side_effect=_config_get_with_knowledge_enabled)):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "怎么配置网络"}]):
            events.append(chunk)

    assert any("无任何来源引用的事实回答" in str(e.get("details", "")) for e in events)
    assert any("⚠️ 抱歉，在系统知识库中未检索到相关内容" in str(e.get("content", "")) for e in events)


@pytest.mark.asyncio
async def test_general_runner_memory_guard_uses_agentscope_native_agent(chat_config):
    """跨会话记忆召回轮次也应走 AgentScope 原生 Agent，不再回落手写 ReAct。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            if not any(msg.has_content_blocks("tool_result") for msg in messages):
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_memory",
                            name="memory_search",
                            input='{"query": "今天聊了什么", "scope": "summary"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="memory final")], is_last=True)

    async def memory_search(query: str, scope: str = "summary", conversation_id: str | None = None):
        return f"memory:{scope}:{query}:{conversation_id or ''}"

    runtime_spec = RuntimeToolSpec(
        name="memory_search",
        description="Search cross-session memory",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string"},
                "conversation_id": {"type": "string"},
            },
            "required": ["query"],
        },
        source_type="system",
        callable=memory_search,
        permission_scope="read",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )
    runner = AssistantAgentRunner(config=chat_config, trace_id="test-native-memory", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.memory_config_service.MemoryConfigService.get_bool", AsyncMock(return_value=True)), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "今天聊了什么"}]):
            events.append(chunk)

    assert any(e.get("title", "").startswith("调用工具: memory_search") for e in events)
    assert "memory final" in _answer_text(events)


@pytest.mark.asyncio
async def test_general_runner_runtime_tools_require_agentscope_native_model(chat_config):
    """RuntimeToolSpec 工具链缺少 native_model 时应显式报错，而不是静默回落手写 ReAct。"""
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    async def test_tool(query: str):
        return f"tool:{query}"

    runtime_spec = RuntimeToolSpec(
        name="test_tool",
        description="Test runtime tool",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=test_tool,
        permission_scope="read",
    )

    class NoNativeModelHandle:
        native_model = None

        def bind_tools(self, tools):
            raise AssertionError("LangChain bind_tools fallback should not run")

    runner = AssistantAgentRunner(config=chat_config, trace_id="test-native-required", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=NoNativeModelHandle())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "Use tool"}]):
            events.append(chunk)

    # 结论事件之前可能还有 tool_preflight 等中间件日志，故只比对 error 结论本身
    assert [e for e in events if e.get("type") == "error"] == [
        {
            "type": "error",
            "status": "error",
            "content": "当前模型适配器未提供 AgentScope native_model，无法执行 AgentScope 原生工具链。",
        }
    ]


@pytest.mark.asyncio
async def test_agent_service_resume_permission_returns_error_for_missing_request():
    """确认请求不存在时，service 应返回明确错误事件而不是空 SSE。"""
    from app.services.ai.agent_service import AgentService
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )

    pending_agentscope_confirmations.clear()
    events = []
    async for chunk in AgentService().resume_agentscope_permission_stream(
        permission_request_id="perm_missing",
        confirmed=True,
        user_info={"user_id": "u1"},
    ):
        events.append(chunk)

    assert events == [
        {
            "type": "error",
            "status": "error",
            "content": "工具确认请求不存在或已过期，请重新发起本轮对话。",
        }
    ]


@pytest.mark.asyncio
async def test_xml_tool_call_parsing(chat_config, mock_tool):
    """旧手写 ReAct 的 XML function_calls 解析已移除，不再执行 legacy tool。"""
    executor = AssistantExecutor(config=chat_config, trace_id="test-trace-3", trace_buffer=[])

    class NoNativeModelHandle:
        native_model = None

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=NoNativeModelHandle())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[mock_tool]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Use XML"}]):
            events.append(chunk)

    mock_tool.ainvoke.assert_not_called()
    # 结论事件之前可能还有 tool_preflight 等中间件日志，故只比对 error 结论本身
    assert [e for e in events if e.get("type") == "error"] == [
        {
            "type": "error",
            "status": "error",
            "content": "当前模型适配器未提供 AgentScope native_model，无法执行 AgentScope 原生工具链。",
        }
    ]

@pytest.mark.asyncio
async def test_max_steps_limit(chat_config, mock_tool):
    """最大执行步数由 AgentScope ReActConfig(max_iters) 控制。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.executors.prompts import AssistantPrompts
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    executor = AssistantExecutor(config=chat_config, trace_id="test-trace-5", trace_buffer=[])

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        # AgentScope 2.0.9 起 Agent 会读取 model.formatter.supported_input_media_types，
        # 测试替身须自行提供（真实模型在 __init__ 中设为 OpenAIChatFormatter()）。
        from agentscope.formatter import OpenAIChatFormatter as _OpenAIChatFormatter
        formatter = _OpenAIChatFormatter()
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id=f"loop_call_{len(messages)}",
                        name="test_tool",
                        input='{"query": "loop"}',
                    )
                ],
                is_last=True,
            )

    async def runtime_tool(query: str):
        return f"loop:{query}"

    runtime_spec = RuntimeToolSpec(
        name="test_tool",
        description="Runtime test tool",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=runtime_tool,
        permission_scope="read",
    )
    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-general",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-general",
        temperature=0.0,
        streaming=True,
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="3")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Loop"}]):
            events.append(chunk)

    assert any(AssistantPrompts.MAX_STEPS_REACHED in event.get("content", "") for event in events)


@pytest.mark.asyncio
async def test_chat_executor_simple_mode_retries_on_stream_error(chat_config):
    """无工具模式下流式 transient 失败应自动重试。"""
    chat_config.tools = []
    executor = AssistantExecutor(config=chat_config, trace_id="test-chat-retry", trace_buffer=[])

    stream_calls = {"count": 0}

    async def failing_then_success(*args, **kwargs):
        stream_calls["count"] += 1
        if stream_calls["count"] == 1:
            raise IndexError("list index out of range")
        yield AIMessage(content="Retry succeeded")

    mock_llm = MagicMock()
    mock_llm.astream.side_effect = failing_then_success

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=mock_llm)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("asyncio.sleep", AsyncMock()):

        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Hello"}]):
            events.append(chunk)

    assert stream_calls["count"] == 2
    assert any(
        chunk.get("type") == "log" and "正在重试" in chunk.get("title", "")
        for chunk in events
    )
    assert any(chunk.get("content") == "Retry succeeded" for chunk in events)
