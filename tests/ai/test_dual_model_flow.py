import pytest
from unittest.mock import AsyncMock, patch
from app.services.ai.executors.chat_executor import GeneralChatExecutor
from app.schemas.agent import ChatConfig

@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_agentscope_native_tool_execution_flow():
    """
    验证 AgentScope 原生工具执行流：
    1. configured native model 负责工具调用。
    2. AgentScope Agent 基于工具结果生成最终回复。
    3. 旧 LangChain bind_tools/ReAct fallback 不再参与。
    """
    from pydantic import BaseModel
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    config = ChatConfig(
        agent_id="test-dual",
        agent_name="DualAgent",
        model_name="gpt-4o",
        temperature=0.0,
        synthesis_model_name="deepseek-v3.2",
        synthesis_temperature=0.7,
        system_prompt="You are a helper.",
        tools=["test_tool"]
    )

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
                            id="call_1",
                            name="test_tool",
                            input='{"input": "val"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="Final Answer from AgentScope")],
                is_last=True,
            )

    async def runtime_tool(input: str):
        return f"Result Data: {input}"

    runtime_spec = RuntimeToolSpec(
        name="test_tool",
        description="Test tool",
        parameters_schema={
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        },
        source_type="static",
        callable=runtime_tool,
        permission_scope="read",
    )
    native_handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-dual",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-dual",
        temperature=0.0,
        streaming=True,
    )

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_orch, \
         patch("app.services.ai.config.AgentConfigProvider.get_fallback_llm", new_callable=AsyncMock, return_value=None), \
         patch("app.services.ai.config.AgentConfigProvider.get_synthesis_llm", new_callable=AsyncMock) as mock_get_syn, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", new_callable=AsyncMock) as mock_get_runtime_tools, \
         patch("app.services.ai.tools.registry.ToolRegistry.get_tools", AsyncMock(side_effect=AssertionError("legacy tools should not be loaded"))), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.general_agent_runner.get_local_workspace", new_callable=AsyncMock, return_value=None), \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get:

        mock_get_orch.return_value = native_handle
        mock_get_runtime_tools.return_value = [runtime_spec]
        mock_config_get.return_value = "5"

        executor = GeneralChatExecutor(config=config, trace_id="trace-dual", trace_buffer=[])
        
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "Help me"}]):
            events.append(chunk)

        mock_get_orch.assert_awaited_once()
        mock_get_runtime_tools.assert_awaited_once_with(config.tools)
        mock_get_syn.assert_not_called()

        final_content = "".join([e["content"] for e in events if "content" in e])
        assert "Final Answer from AgentScope" in final_content
        assert any(e.get("title", "").startswith("调用工具: test_tool") for e in events)
        assert any(e.get("title", "").startswith("工具完成: test_tool") for e in events)

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_agentscope_native_tool_execution_flow())
