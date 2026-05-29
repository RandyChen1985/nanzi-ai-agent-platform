import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from langchain_core.messages import AIMessage
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