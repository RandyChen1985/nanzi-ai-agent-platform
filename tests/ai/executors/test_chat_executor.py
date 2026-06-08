import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from pydantic import BaseModel
from app.services.ai.runtime.agentscope.compat import AIMessage
from app.services.ai.executors.chat_executor import GeneralChatExecutor
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
    tool.ainvoke.return_value = "Tool Result"
    return tool

# --- Tests ---

@pytest.mark.asyncio
async def test_general_chat_executor_delegates_to_general_agent_runner(chat_config):
    """Executor 只做接入壳，实际执行委托给 GeneralAgentRunner。"""
    trace_buffer = []
    executor = GeneralChatExecutor(
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

    with patch("app.services.ai.executors.chat_executor.GeneralAgentRunner", return_value=runner_instance) as runner_cls:
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
    executor = GeneralChatExecutor(config=chat_config, trace_id="test-trace-1", trace_buffer=[])

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

    executor = GeneralChatExecutor(
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

@pytest.mark.asyncio
async def test_standard_tool_call(chat_config, mock_tool):
    """测试标准的 ReAct 工具调用流程 (Think -> Act -> Observe -> Final Answer)"""
    executor = GeneralChatExecutor(config=chat_config, trace_id="test-trace-2", trace_buffer=[])
    
    # Sequence:
    # 1. LLM decides to call tool
    # 2. LLM receives tool output and gives final answer
    
    msg_call_tool = AIMessage(
        content="", 
        tool_calls=[{"name": "test_tool", "args": {"query": "foo"}, "id": "call_1"}]
    )
    msg_after_tool = AIMessage(content="I have the data now.")
    msg_final_synthesis = AIMessage(content="Here is the result: Tool Result")
    
    mock_llm = MockLLM([msg_call_tool, msg_after_tool, msg_final_synthesis])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get:
         
        mock_get_llm.return_value = mock_llm
        mock_get_tools.return_value = [mock_tool]
        mock_config_get.return_value = "5" # Max steps
        
        history = [{"role": "user", "content": "Do something"}]
        
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        # Verify Tool Call Log
        tool_logs = [e for e in events if e.get("type") == "log" and "synthesis" not in e.get("id", "")]
        assert len(tool_logs) >= 2 # Call start + Call finish
        assert tool_logs[0]["title"] == "调用工具: test_tool"
        assert tool_logs[1]["title"].startswith("工具完成: test_tool")
        
        # Verify Synthesis Logs
        syn_logs = [e for e in events if e.get("type") == "log" and ("synthesis_react" in e.get("id", "") or "gen_start" in e.get("id", ""))]
        assert len(syn_logs) == 2
        assert any("汇总工具结果" in l["title"] for l in syn_logs)
        assert any("开始生成回复" in l["title"] for l in syn_logs)
        
        # Verify Final Content
        content_chunks = [e["content"] for e in events if "content" in e and "type" not in e]
        # Note: msg_call_tool has empty content, msg_final has content
        assert "Here is the result: Tool Result" in "".join(content_chunks)
        
        # Verify Tool Execution
        mock_tool.ainvoke.assert_called_once_with({"query": "foo"})


@pytest.mark.asyncio
async def test_general_runner_executes_runtime_tool_specs(chat_config):
    """General 工具链应通过 RuntimeToolSpec 执行，而不是直接消费 legacy tool。"""
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    executor = GeneralChatExecutor(config=chat_config, trace_id="test-runtime-tool", trace_buffer=[])
    invocations = []

    async def runtime_tool(query: str):
        invocations.append({"query": query})
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
    )

    msg_call_tool = AIMessage(
        content="",
        tool_calls=[{"name": "test_tool", "args": {"query": "runtime"}, "id": "call_runtime"}],
    )
    msg_after_tool = AIMessage(content="I have the runtime data now.")
    msg_final_synthesis = AIMessage(content="Here is the result: Runtime Tool Result")
    mock_llm = MockLLM([msg_call_tool, msg_after_tool, msg_final_synthesis])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=mock_llm)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])) as mock_get_runtime_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(side_effect=AssertionError("legacy tools should not be loaded"))), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Do runtime tool"}]):
            events.append(chunk)

    mock_get_runtime_tools.assert_awaited_once_with(chat_config.tools)
    assert invocations == [{"query": "runtime"}]
    assert any(e.get("title", "").startswith("工具完成: test_tool") for e in events)
    assert "Here is the result: Runtime Tool Result" in "".join(
        e["content"] for e in events if "content" in e and "type" not in e
    )


@pytest.mark.asyncio
async def test_general_runner_uses_agentscope_native_agent_for_runtime_tools(chat_config):
    """当具备 AgentScope native model 和 RuntimeToolSpec 时，General 应走原生 Agent/Toolkit。"""
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
    from app.services.ai.runners.general_agent_runner import GeneralAgentRunner

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
    executor = GeneralChatExecutor(config=chat_config, trace_id="test-native-general", trace_buffer=[])

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch.object(GeneralAgentRunner, "_execute_single_tool_safe", side_effect=AssertionError("manual tool execution should not run")):
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Use native"}]):
            events.append(chunk)

    assert any(e.get("title", "").startswith("调用工具: test_tool") for e in events)
    assert any(e.get("title", "").startswith("工具完成: test_tool") for e in events)
    assert "Native final answer" in "".join(
        e["content"] for e in events if "content" in e and "type" not in e
    )

@pytest.mark.asyncio
async def test_xml_tool_call_parsing(chat_config, mock_tool):
    """测试 XML 格式的工具调用解析 (XML -> Tool Call)"""
    executor = GeneralChatExecutor(config=chat_config, trace_id="test-trace-3", trace_buffer=[])
    
    xml_content = """
    Thinking...
    <function_calls>
    <invoke name="test_tool">
    <parameter name="query">xml_query</parameter>
    </invoke>
    </function_calls>
    """
    
    msg_xml_call = AIMessage(content=xml_content)
    msg_final = AIMessage(content="Final Answer")
    
    mock_llm = MockLLM([msg_xml_call, msg_final])
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get:
         
        mock_get_llm.return_value = mock_llm
        mock_get_tools.return_value = [mock_tool]
        mock_config_get.return_value = "5"
        
        history = [{"role": "user", "content": "Use XML"}]
        
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        # Verify Tool Execution with parsed args
        mock_tool.ainvoke.assert_called_once_with({"query": "xml_query"})

@pytest.mark.asyncio
async def test_max_steps_limit(chat_config, mock_tool):
    """测试最大执行步数限制"""
    executor = GeneralChatExecutor(config=chat_config, trace_id="test-trace-5", trace_buffer=[])
    
    # LLM keeps calling tool forever
    msg_loop = AIMessage(
        content="Looping...", 
        tool_calls=[{"name": "test_tool", "args": {}, "id": "loop_call"}]
    )
    
    # Create an infinite generator of tool calls
    mock_llm = MockLLM([msg_loop] * 10) 
    
    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", new_callable=AsyncMock) as mock_get_tools, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get:
         
        mock_get_llm.return_value = mock_llm
        mock_get_tools.return_value = [mock_tool]
        mock_config_get.return_value = "3" # Low limit for test
        
        history = [{"role": "user", "content": "Loop"}]
        
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)
            
        # Check final system message
        last_msg = events[-1]
        assert "[系统提示] 达到最大执行步骤" in last_msg.get("content", "")


@pytest.mark.asyncio
async def test_chat_executor_simple_mode_retries_on_stream_error(chat_config):
    """无工具模式下流式 transient 失败应自动重试。"""
    chat_config.tools = []
    executor = GeneralChatExecutor(config=chat_config, trace_id="test-chat-retry", trace_buffer=[])

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
