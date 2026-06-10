import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.ai.executors.knowledge_executor import KnowledgeExecutor
from app.services.ai.intent_service import IntentResponse, IntentType
from app.services.ai.turn_classifier import TurnClassification, TurnType, attach_turn_classification
from app.services.ai.turn_classifier import (
    should_inject_ltm,
    should_inject_memory_recall_hint,
    should_inject_user_context,
    should_run_active_memory_preload,
)
from app.schemas.agent import ChatConfig


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.fixture
def chat_config():
    return ChatConfig(
        agent_id="test-agent-id",
        agent_name="TestAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a test agent.",
        tools=["search_knowledge_base"],
    )


@pytest.mark.asyncio
async def test_knowledge_turn_forces_search_before_answer(chat_config):
    """知识库轮次应走 KnowledgeExecutor，并自动调用 search_knowledge_base。"""
    from pydantic import BaseModel
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    executor = KnowledgeExecutor(
        config=chat_config, trace_id="test-knowledge", trace_buffer=[], conversation_id="c1"
    )
    classification = TurnClassification(
        turn_type=TurnType.KNOWLEDGE,
        reasoning="SOP 问答",
        requires_knowledge_search=True,
        intent=IntentType.KNOWLEDGE_BASE,
    )
    attach_turn_classification(executor, classification)
    assert executor.turn_classification.turn_type == TurnType.KNOWLEDGE

    invocations: list[str] = []

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
                            id="call_kb_1",
                            name="search_knowledge_base",
                            input='{"query": "高温告警处理流程"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="知识库检索后的回答")], is_last=True)

    async def search_knowledge_base(query: str, dataset_ids: str | None = None):
        invocations.append(query)
        return f"kb:{query}"

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

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=handle)), \
         patch("app.services.ai.config.AgentConfigProvider.get_fallback_llm", AsyncMock(return_value=None)), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_runtime_tools", AsyncMock(return_value=[runtime_spec])), \
         patch("app.services.ai.tools.registry.ToolRegistry.get_system_implicit_tools", return_value=[]), \
         patch("app.services.ai.runners.assistant_agent_runner.get_local_workspace", AsyncMock(return_value=None)), \
         patch("app.services.config_service.ConfigService.get", AsyncMock(return_value="5")), \
         patch("app.services.memory_config_service.MemoryConfigService.get_bool", AsyncMock(return_value=False)):
        history = [{"role": "user", "content": "高温告警处理流程是什么"}]
        events = []
        async for chunk in executor.execute(history):
            events.append(chunk)

    assert invocations
    assert "高温告警" in invocations[0]
    assert any(e.get("title") == "自动检索知识库" for e in events)
    assert "知识库检索后的回答" in "".join(
        e["content"] for e in events if "content" in e and "type" not in e
    )


def test_injection_profile_for_data_turns():
    assert should_inject_ltm(TurnType.DATA_QUERY_REQUEST) is False
    assert should_inject_memory_recall_hint(TurnType.DATA_QUERY_REQUEST) is False
    assert should_run_active_memory_preload(TurnType.DATA_QUERY_REQUEST) is False
    assert should_inject_ltm(TurnType.CONTEXT_ACTION) is True
    assert should_inject_memory_recall_hint(TurnType.KNOWLEDGE) is False
    assert should_inject_user_context(TurnType.DATA_QUERY_REQUEST) is False
    assert should_inject_user_context(TurnType.GENERAL) is True


def test_prepend_platform_global_system_prompt_with_tool_config_items():
    from app.services.ai.agent_prompts import AgentServicePrompts
    from app.schemas.agent import ToolConfigItem
    from unittest.mock import MagicMock

    # 模拟一个同时包含 str、ToolConfigItem 实例和 dict 的复杂工具配置
    mock_config = MagicMock()
    mock_config.tools = [
        "get_dataset_schema",
        ToolConfigItem(name="execute_sql_query", model_name="gpt-4o", temperature=0.1),
        {"name": "read_file"}
    ]
    mock_config.capabilities = ["knowledge_base"]

    # 执行全局提示词的 prepend 拼接动作，应成功运行且不抛出 unhashable type 异常
    result = AgentServicePrompts.prepend_platform_global_system_prompt(
        system_prompt="My custom prompt",
        agent_config=mock_config
    )

    # 验证全局守则和业务提示词均被正确合并拼装
    assert "[云枢智能体平台 · 全局守则]" in result
    assert "My custom prompt" in result
