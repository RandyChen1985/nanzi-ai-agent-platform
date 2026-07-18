import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace
from pydantic import BaseModel
from app.services.ai.runtime.agentscope.compat import AIMessage
from app.services.ai.executors.assistant_executor import AssistantExecutor
from app.schemas.agent import ChatConfig

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
        route_hints={"turn_labels": ["same_topic"]},
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
    assert kwargs["route_hints"] == {"turn_labels": ["same_topic"]}
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
async def test_general_chat_injects_route_hints_as_weak_system_hint(chat_config):
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
        route_hints={
            "turn_labels": ["continuation_followup", "same_topic"],
            "relation_to_previous": "followup",
            "user_action_type": "transform_context",
        },
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=CaptureLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "继续"}]):
            events.append(chunk)

    system_content = captured["messages"][0].content
    assert "路由层通用理解（仅供参考）" in system_content
    assert "continuation_followup, same_topic" in system_content
    assert "relation_to_previous: followup" in system_content
    assert "user_action_type: transform_context" in system_content
    assert "不要机械服从" in system_content
    assert any(chunk.get("content") == "ok" for chunk in events)


def test_route_hints_expose_data_semantic_evidence_for_delegation():
    """Main 应看到已复用的数据语义，并被要求走真实数据获取路径。"""
    from app.services.ai.executors.prompts import AssistantPrompts
    from app.services.ai.intent_service import IntentType

    hint = AssistantPrompts.route_hints({
        "semantic_intent": IntentType.DATA_QUERY,
        "semantic_confidence": 0.91,
        "semantic_reasoning": "请求系统结构化记录",
    })

    assert "semantic_intent: DATA_QUERY" in hint
    assert "0.91" in hint
    assert "sub_agent_call" in hint


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
    assert events == [
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
    assert events == [
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
    assert "Native final answer" in "".join(
        e["content"] for e in events if "content" in e and "type" not in e
    )


@pytest.mark.asyncio
async def test_general_runner_recovers_reply_when_text_block_delta_missing(chat_config):
    """工具执行后若 AgentScope 未发 TEXT_BLOCK_DELTA，应从 AgentState 兜底输出正文。"""
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
        c["content"] for c in chunks if "content" in c and "type" not in c
    )
    assert "recovered after bash" in content


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
    state = new_native_stream_state(user_query="count files")
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
        c["content"] for c in chunks if "content" in c and "type" not in c
    )
    assert "synthesis fallback answer" in content


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
    state = new_native_stream_state(user_query="查企业信息")

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
        c["content"] for c in chunks if "content" in c and "type" not in c
    )
    assert "让我尝试通过搜索来获取" in content
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
    state = new_native_stream_state(user_query="查企业")

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
        c["content"] for c in chunks if "content" in c and "type" not in c
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
    state = new_native_stream_state(user_query="查企业")

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
        c["content"] for c in chunks if "content" in c and "type" not in c
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
        c["content"] for c in chunks if "content" in c and c.get("type") != "error"
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
        user_info={"user_id": "u-state"},
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
                content=[TextBlock(text=f"confirmed final: {tool_result.output[0].text}")],
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
    assert "confirmed final: sent:hello" in "".join(
        e["content"] for e in resume_events if "content" in e and "type" not in e
    )


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
                content=[TextBlock(text=f"snapshot final: {tool_result.output[0].text}")],
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
        user_info={"user_id": "u-snapshot"},
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
            user_info={"user_id": "u-snapshot"},
        ):
            resume_events.append(chunk)

    assert invocations == ["snapshot"]
    assert any(event.get("type") == "permission_result" for event in resume_events)
    assert "snapshot final: sent:snapshot" in "".join(
        e["content"] for e in resume_events if "content" in e and "type" not in e
    )


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
                content=[TextBlock(text=f"final: {tool_result.output[0].text}")],
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
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "知识库问题"}]):
            events.append(chunk)

    assert any(e.get("title") == "自动检索知识库" for e in events)
    assert any(e.get("title", "").startswith("调用工具: search_knowledge_base") for e in events)
    assert "knowledge final" in "".join(
        e["content"] for e in events if "content" in e and "type" not in e
    )


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
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
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
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_classifier import TurnClassification, TurnType

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

    runner = AssistantAgentRunner(config=chat_config, trace_id="test-greeting-table", trace_buffer=[])
    runner.config.tools = []
    runner.turn_classification = TurnClassification(
        turn_type=TurnType.GENERAL,
        reasoning="用户仅打招呼",
        intent=IntentType.GENERAL,
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "你好"}]):
            events.append(chunk)

    assert not any(e.get("title") == "引导切换数据智能体" for e in events)
    assert any(greeting_reply in str(e.get("content", "")) for e in events if e.get("content"))


@pytest.mark.asyncio
async def test_general_runner_without_tools_intercepts_hallucination(chat_config):
    """通用助手在没有调用工具时，如果生成了包含表格或 IP 的回复，应予以拦截。"""
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
         patch(
             "app.services.ai.runners.assistant_agent_runner.AgentManagerService.list_allowed_agents",
             AsyncMock(return_value=[data_agent]),
         ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下资产列表和设备清单"}]):
            events.append(chunk)
            
    assert any(e.get("title") == "引导切换数据智能体" for e in events)
    assert any("请切换到 **业务数据专家** 后继续查询" in str(e.get("content", "")) for e in events)
    assert any("quick:/switch_agent_expert?agent_id=agent-data-custom" in str(e.get("content", "")) for e in events)


@pytest.mark.asyncio
async def test_general_runner_without_data_tool_intercepts_fake_chatbi_handoff(chat_config):
    """通用助手不能在无 data_query 能力时假装衔接 ChatBI 或检索数据集。"""
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_classifier import TurnClassification, TurnType

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
    )
    runner.config.agent_id = "sys-agent-chat"
    runner.config.agent_name = "main"
    runner.config.tools = []
    runner.turn_classification = TurnClassification(
        turn_type=TurnType.GENERAL,
        reasoning="识别为查数请求，但当前 Agent 无 data_query 能力",
        intent=IntentType.DATA_QUERY,
    )

    data_agent = SimpleNamespace(
        id="agent-data-custom",
        display_name="业务数据专家",
        name="biz-data-agent",
        capabilities=["data_query"],
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch(
             "app.services.ai.runners.assistant_agent_runner.AgentManagerService.list_allowed_agents",
             AsyncMock(return_value=[data_agent]),
         ):
        events = []
        async for chunk in runner.execute([{"role": "user", "content": "查一下最近一周总订单量"}]):
            events.append(chunk)

    assert any(e.get("title") == "引导切换数据智能体" for e in events)
    assert any("请切换到 **业务数据专家** 后继续查询" in str(e.get("content", "")) for e in events)
    assert any("quick:/switch_agent_expert?agent_id=agent-data-custom" in str(e.get("content", "")) for e in events)
    assert not any(fake_handoff_text in str(e.get("content", "")) for e in events if e.get("content"))


@pytest.mark.asyncio
async def test_general_runner_plain_chatbi_suggestions_without_hallucination_not_intercepted(chat_config):
    """仅口头建议切换 ChatBI、无编造数据特征时，反查数护栏不再误拦。"""
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
    from app.services.ai.turn_classifier import TurnClassification, TurnType

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
    )
    runner.config.agent_id = "sys-agent-chat"
    runner.config.agent_name = "main"
    runner.config.tools = []
    runner.turn_classification = TurnClassification(
        turn_type=TurnType.GENERAL,
        reasoning="识别为查数请求，但当前 Agent 无 data_query 能力",
        intent=IntentType.DATA_QUERY,
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", AsyncMock(return_value=FakeLLM())), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]):
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
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
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
    assert "memory final" in "".join(
        e["content"] for e in events if "content" in e and "type" not in e
    )


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

    assert events == [
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
    assert events == [
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
